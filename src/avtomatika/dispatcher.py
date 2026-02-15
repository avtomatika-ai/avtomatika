from collections import defaultdict
from logging import getLogger
from random import choice
from typing import Any

try:
    from opentelemetry.propagate import inject
except ImportError:

    def inject(carrier, context=None):
        pass


from .config import Config
from .storage.base import StorageBackend

logger = getLogger(__name__)


class Dispatcher:
    """Responsible for dispatching tasks to specific workers using various strategies.
    In the PULL model, this means enqueuing the task for the worker.
    """

    def __init__(self, storage: StorageBackend, config: Config):
        self.storage = storage
        self.config = config
        self._round_robin_indices: dict[str, int] = defaultdict(int)

    @staticmethod
    def _check_worker_compliance(
        worker: dict[str, Any],
        requirements: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Checks if a worker meets requirements. Returns (is_compliant, rejection_reason)."""
        if required_gpu := requirements.get("gpu_info"):
            gpu_info = worker.get("resources", {}).get("gpu_info")
            if not gpu_info:
                return False, "missing_gpu"
            if required_gpu.get("model") and required_gpu["model"] not in gpu_info.get("model", ""):
                return False, f"gpu_model_mismatch ({gpu_info.get('model')})"
            if required_gpu.get("vram_gb") and required_gpu["vram_gb"] > gpu_info.get("vram_gb", 0):
                return False, "vram_insufficient"

        if required_models := requirements.get("installed_models"):
            installed_models = {m["name"] for m in worker.get("installed_models", [])}
            if not set(required_models).issubset(installed_models):
                return False, "missing_models"

        # HLN UNIVERSALITY: Match against custom 'extra' capabilities
        if extra_reqs := requirements.get("extra_requirements"):
            worker_extra = worker.get("capabilities", {}).get("extra", {})
            for key, req_value in extra_reqs.items():
                worker_value = worker_extra.get(key)
                if worker_value != req_value:
                    return False, f"extra_mismatch: {key}"

        return True, None

    @staticmethod
    def _is_worker_compliant(worker: dict[str, Any], requirements: dict[str, Any]) -> bool:
        # Wrapper for backward compatibility if needed, but better use _check_worker_compliance
        is_valid, _ = Dispatcher._check_worker_compliance(worker, requirements)
        return is_valid

    @staticmethod
    def _select_default(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Default strategy: first selects "warm" workers (those that have the
        task in their hot_skills or hot_cache), and then selects the cheapest among them.

        Note: This strategy uses the deprecated `cost` field for backward
        compatibility. For more accurate cost-based selection, use the `cheapest`
        strategy.
        """
        # 1. Prioritize Hot Skills
        hot_skill_workers = [w for w in workers if task_type in w.get("hot_skills", [])]

        # 2. Then Hot Cache
        hot_cache_workers = [w for w in workers if task_type in w.get("hot_cache", [])]

        target_pool = hot_skill_workers or hot_cache_workers or workers

        # The `cost` field is deprecated but maintained for backward compatibility.
        min_cost = min(w.get("cost", float("inf")) for w in target_pool)
        cheapest_workers = [w for w in target_pool if w.get("cost", float("inf")) == min_cost]

        return choice(cheapest_workers)

    def _select_round_robin(
        self,
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """ "Round Robin" strategy: distributes tasks sequentially among all
        available workers.
        """
        idx = self._round_robin_indices[task_type]
        selected_worker = workers[idx % len(workers)]
        self._round_robin_indices[task_type] = idx + 1
        return selected_worker

    @staticmethod
    def _select_least_connections(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """ "Least Connections" strategy: selects the worker with the fewest
        active tasks (based on the `load` field).
        """
        return min(workers, key=lambda w: w.get("load", 0.0))

    @staticmethod
    def _select_cheapest(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Selects the cheapest worker based on cost for the specific task_type."""

        def get_cost(w):
            # Check modern cost_per_skill first
            capabilities = w.get("capabilities", {})
            cost_map = capabilities.get("cost_per_skill", {})
            if task_type in cost_map:
                return cost_map[task_type]
            # Fallback to legacy fields
            return w.get("cost_per_second", w.get("cost", float("inf")))

        return min(workers, key=get_cost)

    @staticmethod
    def _get_best_value_score(worker: dict[str, Any], task_type: str) -> float:
        """Calculates a "score" for a worker using the formula cost / reputation.
        The lower the score, the better.
        """
        capabilities = worker.get("capabilities", {})
        cost_map = capabilities.get("cost_per_skill", {})
        cost = cost_map.get(task_type, worker.get("cost_per_second", worker.get("cost", float("inf"))))

        # Default reputation is 1.0 if absent
        reputation = worker.get("reputation", 1.0)
        # Avoid division by zero
        return float("inf") if reputation == 0 else cost / reputation

    def _select_best_value(
        self,
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Selects the worker with the best price-quality (reputation) ratio."""
        return min(workers, key=lambda w: self._get_best_value_score(w, task_type))

    async def dispatch(self, job_state: dict[str, Any], task_info: dict[str, Any]) -> None:
        job_id = job_state["id"]
        task_type = task_info.get("type")
        if not task_type:
            raise ValueError("Task info must include a 'type'")

        dispatch_strategy = task_info.get("dispatch_strategy", "default")
        resource_requirements = task_info.get("resource_requirements")

        # HLN OPTIMIZATION: Hot Cache and Hot Skill awareness
        model_hint = task_info.get("params", {}).get("model_name")
        capable_workers = []

        hot_skill_ids = await self.storage.find_workers_by_hot_skill(task_type)
        if hot_skill_ids:
            logger.info(f"HLN: Found {len(hot_skill_ids)} HOT workers for skill '{task_type}'.")
            capable_workers = await self.storage.get_workers(hot_skill_ids)
            from . import metrics

            metrics.tasks_hot_dispatched_total.inc(
                {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"), "kind": "hot_skill"}
            )

        if not capable_workers and model_hint:
            hot_ids = await self.storage.find_hot_workers(task_type, model_hint)
            if hot_ids:
                logger.info(f"HLN: Found {len(hot_ids)} HOT workers with model '{model_hint}' loaded.")
                capable_workers = await self.storage.get_workers(hot_ids)
                from . import metrics

                metrics.tasks_hot_dispatched_total.inc(
                    {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"), "kind": "hot_cache"}
                )

        if not capable_workers:
            candidate_ids = await self.storage.find_workers_for_skill(task_type)
            if not candidate_ids:
                logger.warning(f"No idle workers found for task '{task_type}'")
                raise RuntimeError(f"No suitable workers for task type '{task_type}'")
            capable_workers = await self.storage.get_workers(candidate_ids)

        logger.debug(f"Found {len(capable_workers)} capable workers for task '{task_type}'")

        if not capable_workers:
            raise RuntimeError(f"No suitable workers for task type '{task_type}' (data missing)")

        if resource_requirements:
            compliant_workers = []
            rejection_reasons: dict[str, int] = defaultdict(int)

            for w in capable_workers:
                is_valid, reason = self._check_worker_compliance(w, resource_requirements)
                if is_valid:
                    compliant_workers.append(w)
                else:
                    rejection_reasons[reason or "unknown"] += 1

            logger.debug(
                f"Compliant workers for resources '{resource_requirements}': "
                f"{[w['worker_id'] for w in compliant_workers]}"
            )
            if not compliant_workers:
                summary = dict(rejection_reasons)
                logger.warning(f"No worker satisfies requirements. Rejection summary: {summary}")
                raise RuntimeError(
                    f"No worker satisfies the resource requirements for task '{task_type}'. Reasons: {summary}",
                )
            capable_workers = compliant_workers

        max_cost = task_info.get("max_cost")
        if max_cost is not None:

            def is_cost_compliant(w: dict[str, Any]) -> bool:
                capabilities = w.get("capabilities", {})
                cost_map = capabilities.get("cost_per_skill", {})
                if task_type in cost_map:
                    return bool(cost_map[task_type] <= max_cost)
                # Fallback to legacy fields
                cost = w.get("cost_per_second", w.get("cost", float("inf")))
                return bool(cost <= max_cost)

            cost_compliant_workers = [w for w in capable_workers if is_cost_compliant(w)]
            logger.debug(
                f"Cost compliant workers (max_cost={max_cost}): {[w['worker_id'] for w in cost_compliant_workers]}"
            )
            if not cost_compliant_workers:
                raise RuntimeError(
                    f"No worker meets the maximum cost ({max_cost}) for task '{task_type}'",
                )
            capable_workers = cost_compliant_workers

        if dispatch_strategy == "round_robin":
            selected_worker = self._select_round_robin(capable_workers, task_type)
        elif dispatch_strategy == "least_connections":
            selected_worker = self._select_least_connections(capable_workers, task_type)
        elif dispatch_strategy == "cheapest":
            selected_worker = self._select_cheapest(capable_workers, task_type)
        elif dispatch_strategy == "best_value":
            selected_worker = self._select_best_value(capable_workers, task_type)
        else:  # "default"
            selected_worker = self._select_default(capable_workers, task_type)

        worker_id = selected_worker.get("worker_id")
        if not worker_id:
            raise RuntimeError(f"Selected worker for task '{task_type}' has no worker_id")

        logger.info(
            f"Dispatching task '{task_type}' to worker {worker_id} (strategy: {dispatch_strategy})",
        )

        task_id = task_info.get("task_id") or job_id
        payload = {
            "job_id": job_id,
            "task_id": task_id,
            "type": task_type,
            "params": task_info.get("params", {}),
            "tracing_context": {},
            "params_metadata": job_state.get("data_metadata"),
        }
        inject(payload["tracing_context"], context=job_state.get("tracing_context"))

        try:
            priority = task_info.get("priority", 0.0)
            await self.storage.enqueue_task_for_worker(worker_id, payload, priority)

            # HLN SCALABILITY: Optimistically increment load to prevent overloading
            # the worker before the next heartbeat arrives.
            await self.storage.increment_worker_load(worker_id)

            logger.info(
                f"Task {task_id} with priority {priority} successfully enqueued for worker {worker_id}",
            )
            job_state["current_task_id"] = task_id
            job_state["task_worker_id"] = worker_id
            await self.storage.save_job_state(job_id, job_state)

        except Exception as e:
            logger.exception(
                f"Error enqueuing task for worker {worker_id}",
            )
            raise e

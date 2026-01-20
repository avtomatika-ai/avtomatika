from collections import defaultdict
from logging import getLogger
from random import choice
from typing import Any
from uuid import uuid4

try:
    from opentelemetry.propagate import inject  # type: ignore[import]
except ImportError:

    def inject(carrier, context=None):  # type: ignore[misc]
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
    def _is_worker_compliant(
        worker: dict[str, Any],
        requirements: dict[str, Any],
    ) -> bool:
        """Checks if a worker meets the specified resource requirements."""
        if required_gpu := requirements.get("gpu_info"):
            gpu_info = worker.get("resources", {}).get("gpu_info")
            if not gpu_info:
                return False
            if required_gpu.get("model") and required_gpu["model"] not in gpu_info.get(
                "model",
                "",
            ):
                return False
            if required_gpu.get("vram_gb") and required_gpu["vram_gb"] > gpu_info.get(
                "vram_gb",
                0,
            ):
                return False

        if required_models := requirements.get("installed_models"):
            installed_models = {m["name"] for m in worker.get("installed_models", [])}
            if not set(required_models).issubset(installed_models):
                return False

        return True

    @staticmethod
    def _select_default(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Default strategy: first selects "warm" workers (those that have the
        task in their cache), and then selects the cheapest among them.

        Note: This strategy uses the deprecated `cost` field for backward
        compatibility. For more accurate cost-based selection, use the `cheapest`
        strategy.
        """
        warm_workers = [w for w in workers if task_type in w.get("hot_cache", [])]

        target_pool = warm_workers or workers

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
        """Selects the cheapest worker based on 'cost_per_second'."""
        return min(workers, key=lambda w: w.get("cost_per_second", float("inf")))

    @staticmethod
    def _get_best_value_score(worker: dict[str, Any]) -> float:
        """Calculates a "score" for a worker using the formula cost / reputation.
        The lower the score, the better.
        """
        cost = worker.get("cost_per_second", float("inf"))
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
        return min(workers, key=self._get_best_value_score)

    async def dispatch(self, job_state: dict[str, Any], task_info: dict[str, Any]) -> None:
        job_id = job_state["id"]
        task_type = task_info.get("type")
        if not task_type:
            raise ValueError("Task info must include a 'type'")

        dispatch_strategy = task_info.get("dispatch_strategy", "default")
        resource_requirements = task_info.get("resource_requirements")

        candidate_ids = await self.storage.find_workers_for_task(task_type)
        if not candidate_ids:
            logger.warning(f"No idle workers found for task '{task_type}'")
            raise RuntimeError(f"No suitable workers for task type '{task_type}'")

        capable_workers = await self.storage.get_workers(candidate_ids)
        logger.debug(f"Found {len(capable_workers)} capable workers for task '{task_type}'")

        if not capable_workers:
            raise RuntimeError(f"No suitable workers for task type '{task_type}' (data missing)")

        if resource_requirements:
            compliant_workers = [w for w in capable_workers if self._is_worker_compliant(w, resource_requirements)]
            logger.debug(
                f"Compliant workers for resources '{resource_requirements}': "
                f"{[w['worker_id'] for w in compliant_workers]}"
            )
            if not compliant_workers:
                raise RuntimeError(
                    f"No worker satisfies the resource requirements for task '{task_type}'",
                )
            capable_workers = compliant_workers

        max_cost = task_info.get("max_cost")
        if max_cost is not None:
            cost_compliant_workers = [w for w in capable_workers if w.get("cost_per_second", float("inf")) <= max_cost]
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
        logger.info(
            f"Dispatching task '{task_type}' to worker {worker_id} (strategy: {dispatch_strategy})",
        )

        task_id = task_info.get("task_id") or str(uuid4())
        payload = {
            "job_id": job_id,
            "task_id": task_id,
            "type": task_type,
            "params": task_info.get("params", {}),
            "tracing_context": {},
        }
        # Inject tracing context into the payload, not headers
        inject(payload["tracing_context"], context=job_state.get("tracing_context"))

        try:
            priority = task_info.get("priority", 0.0)
            await self.storage.enqueue_task_for_worker(worker_id, payload, priority)
            logger.info(
                f"Task {task_id} with priority {priority} successfully enqueued for worker {worker_id}",
            )
            # Save task ID and worker ID in the Job state for cancellation capability
            job_state["current_task_id"] = task_id
            job_state["task_worker_id"] = worker_id
            await self.storage.save_job_state(job_id, job_state)

        except Exception as e:
            logger.exception(
                f"Error enqueuing task for worker {worker_id}",
            )
            raise e

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from collections import defaultdict
from logging import getLogger
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
        self._worker_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._worker_cache_ttl = 2.0

    async def _get_workers_cached(self, worker_ids: list[str]) -> list[dict[str, Any]]:
        """Gets worker info from memory cache or Redis."""
        import time

        now = time.time()

        # 1. Identify what's missing or expired in cache
        to_fetch_ids = []
        result_map = {}

        unique_ids = list(set(worker_ids))
        for wid in unique_ids:
            if wid in self._worker_cache:
                expiry, data = self._worker_cache[wid]
                if now < expiry:
                    result_map[wid] = data
                    continue
            to_fetch_ids.append(wid)

        if to_fetch_ids:
            # 2. Fetch missing from Redis
            fetched = await self.storage.get_workers(to_fetch_ids)
            # 3. Update cache
            for w in fetched:
                wid = w["worker_id"]
                self._worker_cache[wid] = (now + self._worker_cache_ttl, w)
                result_map[wid] = w

        # 4. Periodically clean up cache
        if len(self._worker_cache) > 5000:
            expired_keys = [k for k, v in self._worker_cache.items() if now > v[0]]
            for k in expired_keys:
                del self._worker_cache[k]
            # If still over limit, clear half of the cache (LRU-like simple approach)
            if len(self._worker_cache) > 5000:
                keys_to_del = list(self._worker_cache.keys())[:2500]
                for k in keys_to_del:
                    del self._worker_cache[k]

        return [result_map[wid] for wid in worker_ids if wid in result_map]

    @staticmethod
    def _check_worker_compliance(
        worker: dict[str, Any],
        requirements: dict[str, Any],
        task_type: str | None = None,
    ) -> tuple[bool, str | None]:
        """Checks if a worker meets requirements using standard RXON Resource types."""
        worker_resources = worker.get("resources", {}) or {}

        # 0. High-level Resource Checks (RAM, CPU)
        if req_ram := requirements.get("resources", {}).get("ram_gb"):
            worker_ram = worker_resources.get("ram_gb", 0.0)
            if worker_ram < req_ram:
                return False, f"insufficient_ram: {worker_ram} < {req_ram}"

        if req_cpu := requirements.get("resources", {}).get("cpu_cores"):
            worker_cpu = worker_resources.get("cpu_cores", 0)
            if worker_cpu < req_cpu:
                return False, f"insufficient_cpu: {worker_cpu} < {req_cpu}"

        # 1. Generic Hardware & Device Checks
        if required_devices := requirements.get("resources", {}).get("devices"):
            worker_devices = worker_resources.get("devices") or []

            for req_dev in required_devices:
                req_type = req_dev.get("type")
                # resource identifier can be passed in multiple formats
                req_id = req_dev.get("id") or req_dev.get("unit_id")
                found_match = False

                for w_dev in worker_devices:
                    if w_dev.get("type") == req_type:
                        # 1. Match by ID if requested
                        w_id = w_dev.get("id") or w_dev.get("unit_id")
                        id_match = not req_id or str(req_id) == str(w_id)

                        # 2. Match by Model if specified
                        model_match = not req_dev.get("model") or req_dev["model"] in w_dev.get("model", "")

                        # 3. Match Generic Properties (Universal Logic)
                        # We iterate over all other fields in req_dev and look for them in w_dev.properties
                        props_match = True
                        w_props = w_dev.get("properties", {}) or {}

                        # Ignore standard fields we already matched
                        SKIP_FIELDS = {"type", "model", "id", "unit_id"}

                        for key, req_val in req_dev.items():
                            if key in SKIP_FIELDS:
                                continue

                            w_val = w_props.get(key)
                            if w_val is None:
                                props_match = False
                                break

                            # Smart Numeric Comparison: if both are numbers, check >= (Greater/Equal)
                            if isinstance(req_val, (int, float)) and isinstance(w_val, (int, float)):
                                if w_val < req_val:
                                    props_match = False
                                    break
                            # String/Equality check
                            elif w_val != req_val:
                                props_match = False
                                break

                        if id_match and model_match and props_match:
                            found_match = True
                            break

                if not found_match:
                    return False, f"missing_resource: {req_type} (id={req_id}, model={req_dev.get('model')})"

        if required_artifacts := requirements.get("installed_artifacts"):
            # Flexible matching for artifacts (can be strings or dicts with requirements)
            worker_artifacts = worker.get("installed_artifacts", [])

            for req in required_artifacts:
                req_name = req if isinstance(req, str) else req.get("name")
                req_ver = None if isinstance(req, str) else req.get("version")

                found_artifact = False
                for w_art in worker_artifacts:
                    if w_art.get("name") == req_name:
                        # Version check (if requested)
                        if req_ver and w_art.get("version") != req_ver:
                            continue

                        # Property matching (if requested)
                        if isinstance(req, dict) and (req_props := req.get("properties")):
                            w_props = w_art.get("properties", {}) or {}
                            props_match = True
                            for pk, pv in req_props.items():
                                if w_props.get(pk) != pv:
                                    props_match = False
                                    break
                            if not props_match:
                                continue

                        found_artifact = True
                        break

                if not found_artifact:
                    return False, f"missing_artifact: {req_name}"

        # 2. Flexible Skill Parameter Checks
        if task_type:
            target_skill = None
            for skill in worker.get("supported_skills", []):
                if isinstance(skill, dict) and (skill.get("name") == task_type or skill.get("type") == task_type):
                    target_skill = skill
                    break

            if not target_skill:
                return False, f"skill_not_found: {task_type}"

            # Check explicit skill parameter requirements
            if skill_reqs := requirements.get("skill"):
                for field, expected_value in skill_reqs.items():
                    actual_value = target_skill.get(field)
                    if actual_value != expected_value:
                        return False, f"skill_param_mismatch: {field} (expected {expected_value}, got {actual_value})"

            # Check if current params match worker's input_schema
            if params_to_check := requirements.get("params"):
                input_schema = target_skill.get("input_schema")
                if input_schema:
                    from rxon.schema import validate_data

                    is_valid, error_msg = validate_data(params_to_check, input_schema)
                    if not is_valid:
                        return False, f"schema_validation_failed: {error_msg}"

            # Check if worker supports all required output statuses
            if transitions := requirements.get("transitions"):
                worker_statuses = target_skill.get("output_statuses")
                if worker_statuses:
                    # Ignore standard statuses
                    STANDARD_STATUSES = {"success", "failure", "cancelled"}
                    required_custom = {s for s in transitions if s not in STANDARD_STATUSES}

                    if required_custom:
                        supported_set = set(worker_statuses)
                        missing = required_custom - supported_set
                        if missing:
                            return False, f"unsupported_output_statuses: {sorted(missing)}"
        # 3. Custom 'extra' capabilities match
        if extra_reqs := requirements.get("extra_requirements"):
            worker_extra = worker.get("capabilities", {}).get("extra", {})
            for key, req_value in extra_reqs.items():
                worker_value = worker_extra.get(key)
                if worker_value != req_value:
                    return False, f"extra_mismatch: {key}"

        return True, None

    @staticmethod
    def _select_default(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Default strategy: selects "warm" workers (those that have the
        task in their hot_skills or hot_cache), and then selects the cheapest among them.
        """
        # 1. Prioritize Hot Skills (Strictly objects)
        hot_skill_workers = [
            w
            for w in workers
            if any(hs.get("name") == task_type or hs.get("type") == task_type for hs in (w.get("hot_skills") or []))
        ]

        # 2. Then Hot Cache
        hot_cache_workers = [w for w in workers if any(hc == task_type for hc in w.get("hot_cache", []))]

        target_pool = hot_skill_workers or hot_cache_workers or workers

        def get_cost(w: dict[str, Any]) -> float:
            cost_map = w.get("capabilities", {}).get("cost_per_skill", {})
            return float(cost_map.get(task_type, float("inf")))

        min_cost = min(get_cost(w) for w in target_pool)
        cheapest_workers = [w for w in target_pool if get_cost(w) == min_cost]

        if len(cheapest_workers) == 1:
            return cheapest_workers[0]

        # If costs are equal, pick the one with better reputation
        return max(cheapest_workers, key=lambda w: w.get("reputation", 1.0))

    async def _select_round_robin(
        self,
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """ "Round Robin" strategy: distributes tasks sequentially among all
        available workers using a cluster-wide atomic counter in storage.
        """
        counter_key = f"orchestrator:dispatcher:round_robin:{task_type}"
        idx = await self.storage.increment_key(counter_key)
        return workers[idx % len(workers)]

    @staticmethod
    def _select_least_connections(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """ "Least Connections" strategy: selects the worker with the fewest
        active tasks (based on the `_internal_load` field).
        """
        return min(workers, key=lambda w: w.get("_internal_load", 0.0))

    @staticmethod
    def _select_cheapest(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Selects the cheapest worker based on cost for the specific task_type."""

        def get_cost(w: dict[str, Any]) -> float:
            return float(w.get("capabilities", {}).get("cost_per_skill", {}).get(task_type, float("inf")))

        return min(workers, key=get_cost)

    @staticmethod
    def _get_best_value_score(worker: dict[str, Any], task_type: str) -> float:
        """Calculates a "score" for a worker using the formula cost / reputation.
        The lower the score, the better.
        """
        cost = worker.get("capabilities", {}).get("cost_per_skill", {}).get(task_type, float("inf"))

        # Default reputation is 1.0 if absent
        reputation = worker.get("reputation", 1.0)
        # Avoid division by zero
        return float("inf") if reputation == 0 else cost / reputation

    @staticmethod
    def _select_best_value(
        workers: list[dict[str, Any]],
        task_type: str,
    ) -> dict[str, Any]:
        """Selects the worker with the best price-quality (reputation) ratio."""
        return min(workers, key=lambda w: Dispatcher._get_best_value_score(w, task_type))

    async def _select_overflow(
        self,
        workers: list[dict[str, Any]],
        task_type: str,
        max_cost: float | None = None,
    ) -> dict[str, Any]:
        """Strategy "Overflow to More Expensive":
        1. Sort candidates by cost.
        2. Assign to the cheapest worker whose queue length < SOFT_LIMIT.
        3. Fallback: pick the one with the smallest queue among those within the price limit.
        """

        def get_cost(w: dict[str, Any]) -> float:
            return float(w.get("capabilities", {}).get("cost_per_skill", {}).get(task_type, float("inf")))

        # Sort by cost (ASC)
        sorted_workers = sorted(workers, key=get_cost)

        # Apply max_cost filter if provided
        if max_cost is not None:
            sorted_workers = [w for w in sorted_workers if get_cost(w) <= max_cost]

        if not sorted_workers:
            raise RuntimeError(f"No worker meets the maximum cost ({max_cost}) for task '{task_type}'")

        # Get queue lengths
        worker_queue_lens = {}
        for w in sorted_workers:
            wid = w["worker_id"]
            worker_queue_lens[wid] = await self.storage.get_worker_queue_length(wid)

        soft_limit = getattr(self.config, "DISPATCHER_SOFT_LIMIT", 3)

        # Try to find the cheapest worker with a small queue
        for w in sorted_workers:
            wid = w["worker_id"]
            if worker_queue_lens[wid] < soft_limit:
                return w

        # Find the one with the minimum queue among candidates
        return min(sorted_workers, key=lambda w: worker_queue_lens[w["worker_id"]])

    async def dispatch(self, job_state: dict[str, Any], task_info: dict[str, Any]) -> None:
        job_id = job_state["id"]
        task_type = task_info.get("type")
        if not task_type:
            raise ValueError("Task info must include a 'type'")

        dispatch_strategy = task_info.get("dispatch_strategy", "default")
        resource_requirements = (task_info.get("resource_requirements") or {}).copy()
        if "params" not in resource_requirements:
            resource_requirements["params"] = task_info.get("params") or {}
        if "transitions" not in resource_requirements:
            resource_requirements["transitions"] = task_info.get("transitions") or {}

        # Hot Cache and Hot Skill awareness
        params = task_info.get("params") or {}
        resource_hint = params.get("resource_hint") or params.get("model_name")
        capable_workers = []

        hot_skill_ids = await self.storage.find_workers_by_hot_skill(task_type)
        if hot_skill_ids:
            logger.info(f"HLN: Found {len(hot_skill_ids)} HOT workers for skill '{task_type}'.")
            capable_workers = await self._get_workers_cached(hot_skill_ids)
            if capable_workers:
                from . import metrics

                metrics.tasks_hot_dispatched_total.inc(
                    {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"), "kind": "hot_skill"}
                )

        if not capable_workers and resource_hint:
            hot_ids = await self.storage.find_hot_workers(task_type, resource_hint)
            if hot_ids:
                capable_workers = await self._get_workers_cached(hot_ids)
                if capable_workers:
                    logger.info(f"HLN: Found {len(hot_ids)} HOT workers with resource '{resource_hint}' loaded.")
                    from . import metrics

                    metrics.tasks_hot_dispatched_total.inc(
                        {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"), "kind": "hot_cache"}
                    )

        if not capable_workers:
            candidate_ids = await self.storage.find_workers_for_skill(task_type)
            if not candidate_ids:
                logger.warning(f"No idle workers found for task '{task_type}'")
                raise RuntimeError(f"No suitable workers for task type '{task_type}'")
            capable_workers = await self._get_workers_cached(candidate_ids)

        logger.debug(f"Found {len(capable_workers)} capable workers for task '{task_type}'")

        if not capable_workers:
            raise RuntimeError(f"No suitable workers for task type '{task_type}' (data missing)")

        if resource_requirements:
            compliant_workers = []
            rejection_reasons: dict[str, int] = defaultdict(int)

            # Optimization - only check up to DISPATCHER_MAX_CANDIDATES
            max_candidates = getattr(self.config, "DISPATCHER_MAX_CANDIDATES", 50)
            if not isinstance(max_candidates, int):
                max_candidates = 50
            candidate_count = 0

            for w in capable_workers:
                is_valid, reason = self._check_worker_compliance(w, resource_requirements, task_type)
                if is_valid:
                    compliant_workers.append(w)
                    candidate_count += 1
                    if candidate_count >= max_candidates:
                        break
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
                cost = w.get("capabilities", {}).get("cost_per_skill", {}).get(task_type, float("inf"))
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

        # Ignore workers below the threshold
        min_reputation = getattr(self.config, "REPUTATION_MIN_THRESHOLD", 0.0)
        if not isinstance(min_reputation, (int, float)):
            min_reputation = 0.0

        def get_rep(w: dict[str, Any]) -> float:
            val = w.get("reputation", 1.0)
            try:
                return float(val)
            except (TypeError, ValueError):
                return 1.0

        trusted_workers = [w for w in capable_workers if get_rep(w) >= min_reputation]

        if not trusted_workers:
            logger.warning(f"No trusted workers (rep >= {min_reputation}) found for task '{task_type}'")
            raise RuntimeError(
                f"No suitable workers meeting the reputation threshold ({min_reputation}) for task '{task_type}'"
            )
        capable_workers = trusted_workers

        if dispatch_strategy == "round_robin":
            selected_worker = await self._select_round_robin(capable_workers, task_type)
        elif dispatch_strategy == "least_connections":
            selected_worker = self._select_least_connections(capable_workers, task_type)
        elif dispatch_strategy == "cheapest":
            selected_worker = self._select_cheapest(capable_workers, task_type)
        elif dispatch_strategy == "best_value":
            selected_worker = self._select_best_value(capable_workers, task_type)
        elif dispatch_strategy == "overflow":
            selected_worker = await self._select_overflow(capable_workers, task_type, max_cost=max_cost)
        else:  # "default"
            selected_worker = self._select_default(capable_workers, task_type)

        worker_id = selected_worker.get("worker_id")
        if not worker_id:
            raise RuntimeError(f"Selected worker for task '{task_type}' has no worker_id")

        logger.info(
            f"Dispatching task '{task_type}' to worker {worker_id} (strategy: {dispatch_strategy})",
        )

        # Metadata Filtering: Only send metadata for files actually used in this task
        full_metadata = job_state.get("data_metadata", {})
        task_params = task_info.get("params", {})
        filtered_metadata = {k: v for k, v in full_metadata.items() if k in task_params}

        task_id = task_info.get("task_id") or job_id
        priority = task_info.get("priority", 0.0)

        # Deadline propagation (absolute timestamp)
        # We prefer result_deadline if set, otherwise we don't send it (worker will use its local timeout)
        deadline = job_state.get("result_deadline")

        payload = {
            "job_id": job_id,
            "task_id": task_id,
            "type": task_type,
            "params": task_params,
            "tracing_context": {},
            "params_metadata": filtered_metadata if filtered_metadata else None,
            "priority": priority,
            "deadline": deadline,
        }
        inject(payload["tracing_context"], context=job_state.get("tracing_context"))

        try:
            await self.storage.enqueue_task_for_worker(worker_id, payload, priority)

            # Optimistically increment load to prevent overloading
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

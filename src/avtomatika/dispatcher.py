# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from collections import defaultdict
from logging import getLogger
from time import time
from typing import Any

from rxon.constants import WORKER_STATUS_DRAINING
from rxon.models import InstalledArtifact, Resources, SkillInfo, TaskPayload
from rxon.schema import validate_data
from rxon.utils import from_dict, to_dict

try:
    from opentelemetry.propagate import inject
except ImportError:

    def inject(carrier, context=None):
        pass


from . import metrics
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
        now = time()

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
            fetched = await self.storage.get_workers(to_fetch_ids)
            for w in fetched:
                wid = w["worker_id"]
                self._worker_cache[wid] = (now + self._worker_cache_ttl, w)
                result_map[wid] = w

        if len(self._worker_cache) > 5000:
            expired_keys = [k for k, v in self._worker_cache.items() if now > v[0]]
            for k in expired_keys:
                del self._worker_cache[k]
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
        parsed_resources: Any | None = None,
        parsed_artifacts: list[Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Checks if a worker meets requirements using standardized RXON matching."""
        if worker.get("status") == WORKER_STATUS_DRAINING:
            return False, "worker_is_draining"

        if parsed_resources is not None:
            worker_res_dict = worker.get("resources")
            if not worker_res_dict:
                return False, "missing_worker_resources"

            worker_resources = from_dict(Resources, worker_res_dict)

            if not worker_resources.matches(parsed_resources):
                return False, "resource_mismatch"

        if parsed_artifacts is not None:
            worker_artifacts_raw = worker.get("installed_artifacts", [])
            worker_artifacts = [from_dict(InstalledArtifact, a) for a in (worker_artifacts_raw or [])]

            for req_art in parsed_artifacts:
                if not any(wa.matches(req_art) for wa in worker_artifacts):
                    return False, f"missing_artifact: {req_art.name}"

        if required_software := requirements.get("installed_software"):
            worker_sw = worker.get("installed_software", {}) or {}
            for sw_name, sw_version in required_software.items():
                if sw_name not in worker_sw:
                    return False, f"missing_software: {sw_name}"
                if sw_version != "any" and worker_sw[sw_name] != sw_version:
                    return False, f"software_version_mismatch: {sw_name} ({worker_sw[sw_name]} != {sw_version})"

        if task_type:
            target_skill_data = None
            for skill in worker.get("supported_skills", []):
                # Strictly use 'name' as per RXON b8 standard. No 'type' fallback.
                if isinstance(skill, dict) and skill.get("name") == task_type:
                    target_skill_data = skill
                    break

            if not target_skill_data:
                return False, f"skill_not_found: {task_type}"

            target_skill = from_dict(SkillInfo, target_skill_data)

            skill_version = requirements.get("skill_version") or "1.0.0"

            # Check version/type compatibility using strict RXON b10 SkillInfo.matches
            req_skill = SkillInfo(
                name=task_type,
                version=skill_version,
                type=requirements.get("skill_type"),
            )

            # Relaxed matching for backward compatibility: if worker has no version, ignore version check
            is_match = target_skill.matches(req_skill)
            if not is_match and not target_skill.version:
                # If target_skill has no version, we only check the name (which is already done) and type
                is_match = target_skill.name == req_skill.name and (
                    not req_skill.type or target_skill.type == req_skill.type
                )

            if not is_match:
                return (
                    False,
                    f"skill_contract_mismatch: {task_type} "
                    f"(expected version {req_skill.version}, type {req_skill.type})",
                )

            # Check explicit skill parameter requirements
            if skill_reqs := requirements.get("skill"):
                my_props = target_skill.properties or {}
                for field, expected_value in skill_reqs.items():
                    # Check both direct fields and properties
                    actual_value = getattr(target_skill, field, None)
                    if actual_value is None:
                        actual_value = my_props.get(field)

                    if actual_value != expected_value:
                        return False, f"skill_param_mismatch: {field} (expected {expected_value}, got {actual_value})"

            # Check if current params match worker's input_schema
            if (params_to_check := requirements.get("params")) and target_skill.input_schema:
                is_valid, error_msg = validate_data(params_to_check, target_skill.input_schema)
                if not is_valid:
                    return False, f"schema_validation_failed: {error_msg}"

            # Check if worker supports all required output statuses
            if (transitions := requirements.get("transitions")) and target_skill.output_statuses:
                # Ignore standard statuses
                STANDARD_STATUSES = {"success", "failure", "cancelled"}
                required_custom = {s for s in transitions if s not in STANDARD_STATUSES}

                if required_custom:
                    supported_set = set(target_skill.output_statuses)
                    missing = required_custom - supported_set
                    if missing:
                        return False, f"unsupported_output_statuses: {sorted(missing)}"

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
        hot_skill_workers = [
            w
            for w in workers
            if any(
                (hs.get("name") if isinstance(hs, dict) else hs) == task_type
                or (hs.get("type") if isinstance(hs, dict) else None) == task_type
                for hs in (w.get("hot_skills") or [])
            )
        ]

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

        # Add skill version and type to requirements so _check_worker_compliance can find them
        # Beta 10: Fallback to job_state if missing in task_info
        resource_requirements["skill_version"] = task_info.get("skill_version") or job_state.get("skill_version")
        resource_requirements["skill_type"] = task_info.get("skill_type") or job_state.get("skill_type")

        # Hot Cache and Hot Skill awareness
        params = task_info.get("params") or {}
        resource_hint = task_info.get("resource_hint")
        if not resource_hint and isinstance(params, dict):
            resource_hint = params.get("resource_hint") or params.get("model_name")
        capable_workers = []

        hot_skill_ids = await self.storage.find_workers_by_hot_skill(task_type)
        if hot_skill_ids:
            logger.info(f"HLN: Found {len(hot_skill_ids)} HOT workers for skill '{task_type}'.")
            capable_workers = await self._get_workers_cached(hot_skill_ids)
            if capable_workers:
                metrics.tasks_hot_dispatched_total.inc(
                    {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"), "kind": "hot_skill"}
                )

        if not capable_workers and resource_hint:
            hot_ids = await self.storage.find_hot_workers(task_type, resource_hint)
            if hot_ids:
                capable_workers = await self._get_workers_cached(hot_ids)
                if capable_workers:
                    logger.info(f"HLN: Found {len(hot_ids)} HOT workers with resource '{resource_hint}' loaded.")
                    metrics.tasks_hot_dispatched_total.inc(
                        {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"), "kind": "hot_cache"}
                    )

        if not capable_workers:
            candidate_ids = await self.storage.find_workers_for_skill(task_type)
            if not candidate_ids:
                logger.warning(f"No idle workers found for task '{task_type}'")
                raise RuntimeError(f"No suitable workers for task type '{task_type}'")

            capable_workers = await self._get_workers_cached(candidate_ids)
            if not capable_workers:
                # Beta 21 Resilience: If IDs are in index but data is missing,
                # it's a stale index. We just treat it as "no workers" and let it retry.
                logger.debug(f"HLN: Stale index detected for '{task_type}'. IDs {candidate_ids} have no data.")
                raise RuntimeError(f"No suitable workers for task type '{task_type}'")

        if resource_requirements:
            compliant_workers = []
            rejection_reasons: dict[str, int] = defaultdict(int)

            # Optimization - only check up to DISPATCHER_MAX_CANDIDATES
            max_candidates = getattr(self.config, "DISPATCHER_MAX_CANDIDATES", 50)
            if not isinstance(max_candidates, int):
                max_candidates = 50
            candidate_count = 0

            # Pre-parse requirements to avoid O(N) parsing overhead
            parsed_resources = None
            if req_res_dict := resource_requirements.get("resources"):
                # Ensure it has properties if it came from flat structure
                if "properties" not in req_res_dict and ("cpu_cores" in req_res_dict or "ram_gb" in req_res_dict):
                    req_res_dict = {"properties": req_res_dict}

                parsed_resources = from_dict(Resources, req_res_dict)

            parsed_artifacts = None
            if required_artifacts := resource_requirements.get("installed_artifacts"):
                parsed_artifacts = []
                for req_raw in required_artifacts:
                    if isinstance(req_raw, str):
                        parsed_artifacts.append(InstalledArtifact(name=req_raw))
                    else:
                        parsed_artifacts.append(from_dict(InstalledArtifact, req_raw))

            for w in capable_workers:
                is_valid, reason = self._check_worker_compliance(
                    w,
                    resource_requirements,
                    task_type,
                    parsed_resources=parsed_resources,
                    parsed_artifacts=parsed_artifacts,
                )
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

        payload_obj = TaskPayload(
            job_id=job_id,
            task_id=task_id,
            type=task_type,
            params=task_params,
            tracing_context={},
            params_metadata=filtered_metadata if filtered_metadata else None,
            priority=priority,
            deadline=deadline,
            security=job_state.get("security"),
            metadata=job_state.get("metadata"),
            timestamp=time(),
        )
        inject(payload_obj.tracing_context, context=job_state.get("tracing_context"))
        payload = to_dict(payload_obj)

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

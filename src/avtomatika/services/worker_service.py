# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from hashlib import sha256
from logging import getLogger
from secrets import token_urlsafe
from time import monotonic
from typing import Any, Optional

from rxon.models import TokenResponse, WorkerEventPayload
from rxon.schema import validate_data
from rxon.validators import validate_identifier

from ..app_keys import S3_SERVICE_KEY
from ..config import Config
from ..constants import (
    ERROR_CODE_CONTRACT_VIOLATION,
    ERROR_CODE_DEPENDENCY,
    ERROR_CODE_INTEGRITY_MISMATCH,
    ERROR_CODE_INVALID_INPUT,
    ERROR_CODE_PERMANENT,
    ERROR_CODE_SECURITY,
    ERROR_CODE_TRANSIENT,
    EVENT_TYPE_PROGRESS,
    IGNORED_REASON_CANCELLED,
    IGNORED_REASON_LATE,
    IGNORED_REASON_NOT_FOUND,
    IGNORED_REASON_STALE,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUARANTINED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_FOR_PARALLEL,
    JOB_STATUS_WAITING_FOR_WORKER,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILURE,
    TASK_STATUS_SUCCESS,
)
from ..history.base import HistoryStorageBase
from ..storage.base import StorageBackend

logger = getLogger(__name__)


class WorkerService:
    def __init__(
        self,
        storage: StorageBackend,
        history_storage: HistoryStorageBase,
        config: Config,
        engine: Any,
    ):
        self.storage = storage
        self.history_storage = history_storage
        self.config = config
        self.engine = engine

    async def register_worker(self, worker_data: dict[str, Any]) -> None:
        """
        Registers a new worker.
        :param worker_data: Raw dictionary from request (validated against WorkerRegistration)
        """
        from rxon.models import WorkerRegistration
        from rxon.utils import to_dict

        # HLN Contract Validation: Ensure worker meets the protocol registration requirements
        try:
            validated_reg = self.engine._from_dict(WorkerRegistration, worker_data)
            # Re-serialize to dict to ensure standard structure and types in storage
            worker_data = to_dict(validated_reg)
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Worker registration failed validation: {e}")
            raise ValueError(f"Invalid WorkerRegistration payload: {e}") from e

        worker_id = worker_data.get("worker_id")
        if not isinstance(worker_id, str):
            raise ValueError("Missing or invalid worker_id in registration data")

        validate_identifier(worker_id, "worker_id")

        # S3 Consistency Check
        s3_service = self.engine.app.get(S3_SERVICE_KEY)
        if s3_service:
            orchestrator_s3_hash = s3_service.get_config_hash()
            worker_capabilities = worker_data.get("capabilities", {})
            worker_s3_hash = worker_capabilities.get("s3_config_hash")

            if orchestrator_s3_hash and worker_s3_hash and orchestrator_s3_hash != worker_s3_hash:
                logger.warning(
                    f"Worker '{worker_id}' has a different S3 configuration hash! "
                    f"Orchestrator: {orchestrator_s3_hash}, Worker: {worker_s3_hash}. "
                    "This may lead to 'split-brain' storage issues."
                )

        ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
        await self.storage.register_worker(worker_id, worker_data, ttl)

        logger.info(f"Worker '{worker_id}' registered with info: {worker_data}")

        await self.history_storage.log_worker_event(
            {
                "worker_id": worker_id,
                "event_type": "registered",
                "worker_info_snapshot": worker_data,
            }
        )

    async def get_next_task(self, worker_id: str) -> Optional[dict[str, Any]]:
        """
        Retrieves the next task for a worker using long-polling configuration.
        """
        logger.debug(f"Worker {worker_id} is requesting a new task.")
        task = await self.storage.dequeue_task_for_worker(worker_id, self.config.WORKER_POLL_TIMEOUT_SECONDS)

        if task:
            job_id = task.get("job_id")
            if job_id:
                now = monotonic()

                async def _mark_picked_up(state: dict[str, Any]) -> dict[str, Any]:
                    state["task_picked_up_at"] = now
                    return state

                try:
                    updated_state = await self.storage.update_job_state_atomic(job_id, _mark_picked_up)

                    # Determine the new deadline for the execution phase
                    result_deadline = updated_state.get("result_deadline")
                    if result_deadline is not None:
                        # Use the absolute deadline for the result
                        new_timeout_at = result_deadline
                    else:
                        # Fallback to execution-only timeout from pick-up time
                        execution_timeout = (
                            updated_state.get("execution_timeout_seconds") or self.config.WORKER_TIMEOUT_SECONDS
                        )
                        new_timeout_at = now + execution_timeout

                    await self.storage.add_job_to_watch(job_id, new_timeout_at)

                    logger.info(f"Task {task.get('task_id')} for job {job_id} picked up by worker {worker_id}")
                except Exception as e:
                    logger.error(f"Failed to update pick-up time for job {job_id}: {e}")

        return task

    async def process_task_result(self, result_payload: dict[str, Any], authenticated_worker_id: str) -> dict[str, Any]:
        """
        Processes a task result submitted by a worker.
        """
        from rxon.models import TaskResult
        from rxon.utils import to_dict

        # HLN Contract Validation: Ensure result matches protocol TaskResult
        try:
            validated_res = self.engine._from_dict(TaskResult, result_payload)
            # Normalize to dict for storage consistency
            result_payload = to_dict(validated_res)
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Task result failed protocol validation: {e}")
            raise ValueError(f"Invalid TaskResult payload: {e}") from e

        payload_worker_id = result_payload.get("worker_id")

        if payload_worker_id and payload_worker_id != authenticated_worker_id:
            raise PermissionError(
                f"Forbidden: Authenticated worker '{authenticated_worker_id}' "
                f"cannot submit results for another worker '{payload_worker_id}'."
            )

        job_id = result_payload.get("job_id")
        task_id = result_payload.get("task_id")

        if not job_id or not task_id:
            raise ValueError("job_id and task_id are required")

        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            # UNIFIED: Handle missing job (might be flushed or expired)
            return {
                "status": "ignored",
                "reason": IGNORED_REASON_NOT_FOUND,
                "message": f"Job {job_id} not found (expired or deleted).",
            }

        # SAFETY CHECK: Verify that the submitted task_id matches the current task of the job.
        # This prevents late results from old tasks (e.g. after a timeout and retry)
        # from overwriting data in a job that has already moved on.
        current_task_id = job_state.get("current_task_id")
        if current_task_id and current_task_id != task_id:
            logger.warning(
                f"Received STALE result for job {job_id}. "
                f"Submitted task_id: {task_id}, current task_id in job: {current_task_id}. Ignoring."
            )
            return {
                "status": "ignored",
                "reason": IGNORED_REASON_STALE,
                "message": f"Result ignored: task_id {task_id} is no longer active for job {job_id}.",
            }

        # Protection: If the job is already failed (e.g., by Watcher due to timeout),
        # or finished, we should not process any late results from workers.
        from ..executor import TERMINAL_STATES

        current_status = job_state.get("status")

        if current_status in TERMINAL_STATES:
            # UNIFIED: Handle terminal states
            error_message = (job_state.get("error_message") or "").lower()
            if "timeout" in error_message:
                reason = IGNORED_REASON_LATE
            elif current_status == JOB_STATUS_CANCELLED:
                reason = IGNORED_REASON_CANCELLED
            else:
                reason = "job_already_terminated"  # Generic terminal reason

            logger.warning(
                f"Received late result for job {job_id} (status: {current_status}, reason: {reason}). Ignoring."
            )
            from .. import metrics

            metrics.tasks_ignored_total.inc(
                {
                    metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown"),
                    "reason": reason,
                }
            )
            return {
                "status": "ignored",
                "reason": reason,
                "message": (f"Result ignored: the job is already in a terminal state ({current_status})."),
            }

        result_status = result_payload.get("status", TASK_STATUS_SUCCESS)
        worker_data_content = result_payload.get("data")

        # HLN Marketplace Enrichment: Find the exact skill definition used
        skill_snapshot = None
        if authenticated_worker_id:
            try:
                worker_info = await self.storage.get_worker_info(authenticated_worker_id)
                if worker_info:
                    task_type = job_state.get("current_task_info", {}).get("type")
                    # Search for the matching skill in the worker's supported_skills
                    # Every skill is strictly a dict (SkillInfo)
                    for skill in worker_info.get("supported_skills", []):
                        if skill.get("name") == task_type or skill.get("type") == task_type:
                            skill_snapshot = skill
                            break
            except Exception as e:
                logger.warning(f"Failed to fetch skill snapshot for validation (job {job_id}): {e}")

        # CONTRACT VALIDATION: Check if result matches skill's output_schema
        if result_status == TASK_STATUS_SUCCESS and skill_snapshot:
            output_schema = skill_snapshot.get("output_schema")
            if output_schema:
                is_valid, error_msg = validate_data(worker_data_content, output_schema)
                if not is_valid:
                    full_error = f"Contract violation: result data does not match output_schema. {error_msg}"
                    logger.error(f"Worker {authenticated_worker_id} violated contract for job {job_id}: {full_error}")

                    # HLN SELF-REGULATION: Penalize reputation for contract violation
                    penalty = self.config.REPUTATION_PENALTY_CONTRACT_VIOLATION
                    worker_info = await self.storage.get_worker_info(authenticated_worker_id)
                    if worker_info:
                        new_rep = max(0.0, worker_info.get("reputation", 1.0) - penalty)
                        await self.storage.update_worker_data(authenticated_worker_id, {"reputation": new_rep})
                        logger.warning(
                            f"Worker {authenticated_worker_id} reputation decreased by {penalty} "
                            f"to {new_rep:.2f} due to contract violation."
                        )
                    # Transform success into failure due to contract violation
                    result_status = TASK_STATUS_FAILURE
                    result_payload["status"] = TASK_STATUS_FAILURE
                    result_payload["error"] = {"code": ERROR_CODE_CONTRACT_VIOLATION, "message": full_error}

        if current_status == JOB_STATUS_WAITING_FOR_PARALLEL:
            await self.storage.remove_job_from_watch(f"{job_id}:{task_id}")

            def _update_parallel_results(state: dict[str, Any]) -> dict[str, Any]:
                state.setdefault("aggregation_results", {})[task_id] = result_payload
                branches = state.setdefault("active_branches", [])
                if task_id in branches:
                    branches.remove(task_id)

                if not branches:
                    state["status"] = JOB_STATUS_RUNNING
                    state["current_state"] = state["aggregation_target"]
                return state

            updated_job_state = await self.storage.update_job_state_atomic(job_id, _update_parallel_results)

            if not updated_job_state.get("active_branches"):
                logger.info(f"All parallel branches for job {job_id} have completed.")
                await self.storage.enqueue_job(job_id)
            else:
                remaining = len(updated_job_state["active_branches"])
                logger.info(
                    f"Branch {task_id} for job {job_id} completed. Waiting for {remaining} more.",
                )

            return {"status": "accepted", "transition": "parallel_branch_accepted"}

        await self.storage.remove_job_from_watch(job_id)

        now = monotonic()
        dispatched_at = job_state.get("task_dispatched_at", now)
        duration_ms = int((now - dispatched_at) * 1000)

        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": job_state.get("current_state"),
                "event_type": "task_finished",
                "duration_ms": duration_ms,
                "worker_id": authenticated_worker_id,
                "context_snapshot": {
                    **job_state,
                    "result": result_payload,
                    "skill_snapshot": skill_snapshot,  # Financial/Contract info
                },
            },
        )

        if result_status == TASK_STATUS_FAILURE:
            await self._handle_task_failure(job_state, task_id, result_payload)
            return {"status": "accepted", "transition": "failure_handled"}

        if result_status == TASK_STATUS_CANCELLED:
            logger.info(f"Task {task_id} for job {job_id} was cancelled by worker.")
            job_state["status"] = JOB_STATUS_CANCELLED
            await self.storage.save_job_state(job_id, job_state)

            transitions = job_state.get("current_task_transitions", {})
            if next_state := transitions.get("cancelled"):
                job_state["current_state"] = next_state
                job_state["status"] = JOB_STATUS_RUNNING
                await self.storage.save_job_state(job_id, job_state)
                await self.storage.enqueue_job(job_id)
            return {"status": "accepted", "transition": "cancelled_handled"}

        transitions = job_state.get("current_task_transitions", {})
        next_state = transitions.get(result_status)

        if next_state:
            logger.info(f"Job {job_id} transitioning based on worker status '{result_status}' to state '{next_state}'")

            if worker_data_content and isinstance(worker_data_content, dict):
                if "state_history" not in job_state:
                    job_state["state_history"] = {}
                job_state["state_history"].update(worker_data_content)

            data_metadata = result_payload.get("data_metadata")
            if data_metadata:
                if "data_metadata" not in job_state:
                    job_state["data_metadata"] = {}
                job_state["data_metadata"].update(data_metadata)
                logger.debug(f"Stored data metadata for job {job_id}: {list(data_metadata.keys())}")

            job_state["current_state"] = next_state
            job_state["status"] = JOB_STATUS_RUNNING

            # HLN SELF-REGULATION: Reward worker for successful task completion
            reward = self.config.REPUTATION_REWARD_SUCCESS
            if authenticated_worker_id and reward > 0:
                worker_info = await self.storage.get_worker_info(authenticated_worker_id)
                if worker_info:
                    new_rep = min(1.0, worker_info.get("reputation", 1.0) + reward)
                    await self.storage.update_worker_data(authenticated_worker_id, {"reputation": new_rep})
                    # Log silently or at debug level to avoid spamming for every success
                    logger.debug(f"Worker {authenticated_worker_id} reputation increased to {new_rep:.4f}")

            await self.storage.save_job_state(job_id, job_state)
            await self.storage.enqueue_job(job_id)
            return {"status": "accepted", "transition": "success_transition"}
        else:
            logger.error(f"Job {job_id} failed. Worker returned unhandled status '{result_status}'.")
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = f"Worker returned unhandled status: {result_status}"
            await self.storage.save_job_state(job_id, job_state)

            # Cleanup S3 on terminal failure
            await self._cleanup_s3_if_needed(job_id)

            return {"status": "accepted", "transition": "unhandled_status_failure"}

    async def _cleanup_s3_if_needed(self, job_id: str) -> None:
        """Helper to cleanup S3 files if auto-cleanup is enabled."""
        if self.config.S3_AUTO_CLEANUP:
            from asyncio import create_task

            from ..app_keys import S3_SERVICE_KEY

            s3_service = self.engine.app.get(S3_SERVICE_KEY)
            if s3_service:
                task_files = s3_service.get_task_files(job_id)
                if task_files:
                    create_task(task_files.cleanup())

    async def _handle_task_failure(self, job_state: dict, task_id: str, result_payload: dict) -> None:
        error_details = result_payload.get("error", {})
        error_type = ERROR_CODE_TRANSIENT
        error_message = "No error details provided."

        if isinstance(error_details, dict):
            error_type = error_details.get("code", ERROR_CODE_TRANSIENT)
            error_message = error_details.get("message", "No error message provided.")
        elif isinstance(error_details, str):
            error_message = error_details

        job_id = job_state["id"]
        logger.warning(f"Task {task_id} for job {job_id} failed with error type '{error_type}'.")

        if error_type in (
            ERROR_CODE_PERMANENT,
            ERROR_CODE_SECURITY,
            ERROR_CODE_DEPENDENCY,
            ERROR_CODE_CONTRACT_VIOLATION,
        ):
            # HLN SELF-REGULATION: Immediate penalty for critical/permanent failures
            penalty = self.config.REPUTATION_PENALTY_TASK_FAILURE
            if error_type == ERROR_CODE_CONTRACT_VIOLATION:
                penalty = self.config.REPUTATION_PENALTY_CONTRACT_VIOLATION

            worker_id = job_state.get("task_worker_id")
            if worker_id:
                worker_info = await self.storage.get_worker_info(worker_id)
                if worker_info:
                    new_rep = max(0.0, worker_info.get("reputation", 1.0) - penalty)
                    await self.storage.update_worker_data(worker_id, {"reputation": new_rep})
                    logger.warning(f"Worker {worker_id} reputation decreased by {penalty} due to {error_type}.")

            job_state["status"] = JOB_STATUS_QUARANTINED
            job_state["error_message"] = f"Task failed with permanent error ({error_type}): {error_message}"
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.quarantine_job(job_id)
        elif error_type == ERROR_CODE_INVALID_INPUT:
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = f"Task failed due to invalid input: {error_message}"
            await self.storage.save_job_state(job_id, job_state)
        elif error_type == ERROR_CODE_INTEGRITY_MISMATCH:
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = f"Task failed due to data integrity mismatch: {error_message}"
            await self.storage.save_job_state(job_id, job_state)
            logger.critical(f"Data integrity mismatch detected for job {job_id}: {error_message}")
        else:
            await self.engine.handle_task_failure(job_state, task_id, error_message)

    async def process_worker_event(self, authenticated_worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Processes a generic worker event with Identity Chain verification."""
        event = self.engine._from_dict(WorkerEventPayload, payload)
        if not event:
            return {"status": "error", "message": "Invalid event payload"}

        # HLN IDENTITY CHECK: Immediate sender must be who they say they are
        if event.worker_id != authenticated_worker_id:
            logger.critical(
                f"SPOOFING DETECTED: Worker {authenticated_worker_id} tried to send event as {event.worker_id}"
            )
            raise PermissionError("Immediate sender ID mismatch")

        # HLN Contract Validation for Events
        events_schema = None

        if authenticated_worker_id == "ghost":
            # Event from internal blueprint handler
            job_state = await self.storage.get_job_state(event.target_job_id or event.target_task_id)
            if job_state:
                contract = self.engine.blueprint_contracts.get(job_state["blueprint_name"], {})
                events_schema = contract.get("events_schema")
        else:
            # Event from external worker
            worker_info = await self.storage.get_worker_info(authenticated_worker_id)
            if worker_info and event.target_task_id:
                # Events are usually scoped to a skill/task if target_task_id is present
                job_state = await self.storage.get_job_state(event.target_job_id or event.target_task_id)
                if job_state:
                    task_type = job_state.get("current_task_info", {}).get("type")
                    for skill in worker_info.get("supported_skills", []):
                        if skill.get("name") == task_type or skill.get("type") == task_type:
                            events_schema = skill.get("events_schema")
                            break

        if events_schema:
            if event.event_type in events_schema:
                is_valid, error_msg = validate_data(event.payload, events_schema[event.event_type])
                if not is_valid:
                    full_error = f"Contract violation for event '{event.event_type}': {error_msg}"
                    logger.error(f"Sender {authenticated_worker_id} violated event contract: {full_error}")
                    return {"status": "error", "code": ERROR_CODE_CONTRACT_VIOLATION, "message": full_error}
            elif event.event_type != EVENT_TYPE_PROGRESS:
                # Check if we should enforce strict validation
                if self.config.STRICT_EVENT_VALIDATION:
                    full_error = f"Contract violation: event type '{event.event_type}' is not declared in schema."
                    logger.error(f"Sender {authenticated_worker_id} violated contract: {full_error}")
                    return {"status": "error", "code": ERROR_CODE_CONTRACT_VIOLATION, "message": full_error}
                else:
                    logger.warning(
                        f"Sender {authenticated_worker_id} emitted undeclared event "
                        f"'{event.event_type}'. Allowed by config."
                    )

        # HLN System Events: Progress
        if event.event_type == EVENT_TYPE_PROGRESS:
            # ... (existing progress logic) ...
            target_id = event.target_job_id or event.target_task_id
            if target_id:
                progress = event.payload.get("progress", 0.0)
                message = event.payload.get("message", "")

                async def _update_progress(state: dict[str, Any]) -> dict[str, Any]:
                    state["progress"] = progress
                    state["progress_message"] = message
                    return state

                try:
                    await self.storage.update_job_state_atomic(target_id, _update_progress)
                except Exception as e:
                    logger.warning(f"Failed to update progress for job {target_id}: {e}")

        # HLN REACTIVE TRANSITIONS: Check if event triggers a state change
        if event.target_job_id:
            job_state = await self.storage.get_job_state(event.target_job_id)
            if job_state and job_state.get("status") == JOB_STATUS_WAITING_FOR_WORKER:
                event_transitions = job_state.get("current_task_event_transitions", {})
                if next_state := event_transitions.get(event.event_type):
                    logger.info(
                        f"Event '{event.event_type}' triggered transition "
                        f"for job {event.target_job_id} to '{next_state}'"
                    )

                    # Merge event payload into history
                    if "state_history" not in job_state:
                        job_state["state_history"] = {}
                    job_state["state_history"].update(event.payload)

                    # Prevent late results from original task
                    job_state["current_task_id"] = f"superseded_by_event:{event.event_id}"

                    job_state["current_state"] = next_state
                    job_state["status"] = JOB_STATUS_RUNNING

                    await self.storage.save_job_state(event.target_job_id, job_state)
                    await self.storage.remove_job_from_watch(event.target_job_id)
                    await self.storage.enqueue_job(event.target_job_id)

        # Log to history
        await self.history_storage.log_job_event(
            {
                "job_id": event.target_job_id or "system",
                "state": "running",
                "event_type": f"worker_event:{event.event_type}",
                "worker_id": event.origin_worker_id,  # Log the REAL creator
                "context_snapshot": {
                    "event_id": event.event_id,
                    "payload": event.payload,
                    "priority": event.priority,
                    "bubbling_chain": event.bubbling_chain,
                    "sender_id": authenticated_worker_id,
                },
            }
        )

        # HLN EVENT BUBBLING: Notify subscribers (like Matryoshka bridge)
        if self.engine.on_worker_event:
            from asyncio import create_task

            for callback in self.engine.on_worker_event:
                create_task(callback(authenticated_worker_id, event))

        # HLN WEBHOOK BUBBLING: Send event to client if webhook_url is set
        if event.target_job_id:
            job_state = await self.storage.get_job_state(event.target_job_id)
            if job_state and job_state.get("webhook_url"):
                from ..utils.webhook_sender import WebhookPayload

                webhook_payload = WebhookPayload(
                    event=f"worker_event:{event.event_type}",
                    job_id=event.target_job_id,
                    status=job_state.get("status", "running"),
                    result=event.payload,
                    error=None,
                )
                await self.engine.webhook_sender.send(job_state["webhook_url"], webhook_payload)

        return {"status": "event_accepted"}

    async def issue_access_token(self, worker_id: str) -> TokenResponse:
        """Generates and stores a temporary access token."""
        raw_token = token_urlsafe(32)
        token_hash = sha256(raw_token.encode()).hexdigest()
        ttl = 3600

        await self.storage.save_worker_access_token(worker_id, token_hash, ttl)
        logger.info(f"Issued temporary access token for worker {worker_id}")

        return TokenResponse(access_token=raw_token, expires_in=ttl, worker_id=worker_id)

    async def update_worker_heartbeat(
        self, worker_id: str, update_data: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        """Updates worker TTL and status, and checks for tasks that should be cancelled."""
        from rxon.constants import HB_RESP_CANCEL_TASKS, HB_RESP_REQUIRE_FULL_SYNC

        ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
        response_data: dict[str, Any] = {"status": "ok"}

        if update_data:
            # HLN Optimization: Traffic reduction
            new_hash = update_data.get("skills_hash")
            current_worker = await self.storage.get_worker_info(worker_id)

            # If hash is provided and matches, we don't need to re-register skills
            if current_worker and new_hash and current_worker.get("skills_hash") == new_hash:
                # SELF-HEALING: Check if storage actually has the skills
                if not current_worker.get("supported_skills"):
                    logger.warning(f"Worker {worker_id} hash match, but skills missing in storage. Forcing Full Sync.")
                    response_data[HB_RESP_REQUIRE_FULL_SYNC] = True

                # Remove skills from update to keep it lightweight
                update_data.pop("supported_skills", None)

            updated_worker = await self.storage.update_worker_status(worker_id, update_data, ttl)

            if updated_worker:
                await self.history_storage.log_worker_event(
                    {
                        "worker_id": worker_id,
                        "event_type": "status_update",
                        "worker_info_snapshot": updated_worker,
                    },
                )

                # Check current tasks for cancellation
                current_tasks = update_data.get("current_tasks", [])
                if current_tasks:
                    cancel_task_ids = []
                    from ..executor import TERMINAL_STATES

                    for task_id in current_tasks:
                        # Find job_id for this task_id.
                        # In Avtomatika, task_id is often the same as job_id or can be mapped.
                        job_state = await self.storage.get_job_state(task_id)
                        if not job_state:
                            # If job is missing, it's definitely stale
                            cancel_task_ids.append(task_id)
                            continue

                        status = job_state.get("status")
                        # If the job is in a terminal state or cancelled, tell the worker to stop
                        if (status in TERMINAL_STATES or status == JOB_STATUS_CANCELLED) and job_state.get(
                            "task_worker_id"
                        ) == worker_id:
                            # Verification: check if THIS worker is still the one assigned
                            cancel_task_ids.append(task_id)

                    if cancel_task_ids:
                        response_data[HB_RESP_CANCEL_TASKS] = cancel_task_ids

            return response_data
        else:
            refreshed = await self.storage.refresh_worker_ttl(worker_id, ttl)
            return {"status": "ttl_refreshed"} if refreshed else None

from hashlib import sha256
from logging import getLogger
from secrets import token_urlsafe
from time import monotonic
from typing import Any, Optional

from rxon.models import TokenResponse
from rxon.validators import validate_identifier

from ..app_keys import S3_SERVICE_KEY
from ..config import Config
from ..constants import (
    ERROR_CODE_DEPENDENCY,
    ERROR_CODE_INTEGRITY_MISMATCH,
    ERROR_CODE_INVALID_INPUT,
    ERROR_CODE_PERMANENT,
    ERROR_CODE_SECURITY,
    ERROR_CODE_TRANSIENT,
    IGNORED_REASON_CANCELLED,
    IGNORED_REASON_LATE,
    IGNORED_REASON_NOT_FOUND,
    IGNORED_REASON_STALE,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUARANTINED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_FOR_PARALLEL,
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
        :param worker_data: Raw dictionary from request (to be validated/converted to Model later)
        """
        worker_id = worker_data.get("worker_id")
        if not worker_id:
            raise ValueError("Missing required field: worker_id")

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
        Returns a standardized dictionary response.
        """
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
                "context_snapshot": {**job_state, "result": result_payload},
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

        if error_type in (ERROR_CODE_PERMANENT, ERROR_CODE_SECURITY, ERROR_CODE_DEPENDENCY):
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
        ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
        response_data: dict[str, Any] = {"status": "ok"}

        if update_data:
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
                        if status in TERMINAL_STATES or status == JOB_STATUS_CANCELLED:
                            # Verification: check if THIS worker is still the one assigned
                            if job_state.get("task_worker_id") != worker_id:
                                cancel_task_ids.append(task_id)
                            else:
                                cancel_task_ids.append(task_id)

                    if cancel_task_ids:
                        response_data["cancel_task_ids"] = cancel_task_ids

            return response_data
        else:
            refreshed = await self.storage.refresh_worker_ttl(worker_id, ttl)
            return {"status": "ttl_refreshed"} if refreshed else None

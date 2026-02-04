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
        return await self.storage.dequeue_task_for_worker(worker_id, self.config.WORKER_POLL_TIMEOUT_SECONDS)

    async def process_task_result(self, result_payload: dict[str, Any], authenticated_worker_id: str) -> str:
        """
        Processes a task result submitted by a worker.
        Returns a status string constant.
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
            raise LookupError("Job not found")

        result_status = result_payload.get("status", TASK_STATUS_SUCCESS)
        worker_data_content = result_payload.get("data")

        if job_state.get("status") == JOB_STATUS_WAITING_FOR_PARALLEL:
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

            return "parallel_branch_result_accepted"

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
            return await self._handle_task_failure(job_state, task_id, result_payload)

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
            return "result_accepted_cancelled"

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
            return "result_accepted_success"
        else:
            logger.error(f"Job {job_id} failed. Worker returned unhandled status '{result_status}'.")
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = f"Worker returned unhandled status: {result_status}"
            await self.storage.save_job_state(job_id, job_state)
            return "result_accepted_failure"

    async def _handle_task_failure(self, job_state: dict, task_id: str, result_payload: dict) -> str:
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
        return "result_accepted_failure"

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
        """Updates worker TTL and status."""
        ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2

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
            return updated_worker
        else:
            refreshed = await self.storage.refresh_worker_ttl(worker_id, ttl)
            return {"status": "ttl_refreshed"} if refreshed else None

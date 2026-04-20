# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import contextlib
from asyncio import create_task
from hashlib import sha256
from logging import getLogger
from secrets import token_urlsafe
from time import monotonic, time
from typing import Any

from rxon.constants import HB_RESP_CANCEL_TASKS, HB_RESP_REQUIRE_FULL_SYNC
from rxon.models import (
    SecurityContext,
    SkillInfo,
    TaskResult,
    TokenResponse,
    WorkerEventPayload,
    WorkerRegistration,
)
from rxon.schema import validate_data
from rxon.security import verify_signature
from rxon.utils import from_dict, to_dict
from rxon.validators import validate_identifier

from avtomatika import metrics
from avtomatika.app_keys import S3_SERVICE_KEY
from avtomatika.config import Config
from avtomatika.constants import (
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
    IGNORED_REASON_MISMATCH,
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
from avtomatika.executor import TERMINAL_STATES
from avtomatika.history.base import HistoryStorageBase
from avtomatika.storage.base import StorageBackend
from avtomatika.utils.webhook_sender import WebhookPayload

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

    async def _verify_zero_trust(
        self,
        payload: dict[str, Any],
        worker_id: str,
        secret: str | None = None,
        ignore_fields: list[str] | None = None,
    ) -> None:
        """
        Verifies Zero Trust signature, signer identity, and message freshness (TTL).
        Raises PermissionError if verification fails.
        """
        # Internal 'ghost' identity is trusted by default
        if worker_id == "ghost":
            return

        msg_timestamp = payload.get("timestamp")
        if msg_timestamp:
            diff = abs(int(time()) - int(msg_timestamp))
            if diff > 60:
                logger.warning(f"REPLAY ATTEMPT or CLOCK SKEW: Message from {worker_id} is too old ({diff:.1f}s)")
                raise PermissionError("Message timestamp expired (replay protection)")
        else:
            # External workers MUST provide a timestamp for replay protection
            logger.critical(f"MISSING TIMESTAMP: Worker {worker_id} did not provide a timestamp for replay protection")
            raise PermissionError("Missing required timestamp for replay protection")

        if secret is None:
            secret = await self.storage.get_worker_token(worker_id) or getattr(self.config, "GLOBAL_WORKER_TOKEN", "")

        security_raw = payload.get("security")
        if not security_raw:
            if secret:
                logger.critical(
                    "MISSING SECURITY CONTEXT: Worker %s did not provide security metadata but token is required",
                    worker_id,
                )
                raise PermissionError("Missing required security context")
            return

        security: SecurityContext = from_dict(SecurityContext, security_raw)

        if not security.signature:
            if secret:
                logger.critical(
                    "MISSING SIGNATURE: Worker %s did provide context but no signature while token is required",
                    worker_id,
                )
                raise PermissionError("Missing required cryptographic signature")
            return

        if security.signer_id and security.signer_id != worker_id:
            logger.critical(f"IDENTITY MISMATCH: Message for {worker_id} signed by {security.signer_id}")
            raise PermissionError(f"Message signer ID '{security.signer_id}' does not match worker ID '{worker_id}'")

        if secret:
            logger.debug(f"Verifying signature for {worker_id} using token starting with: {secret[:4]}...")
        else:
            logger.warning(
                f"No token found for {worker_id}, skipping signature check (Zero Trust disabled for this worker)"
            )

        if secret and not verify_signature(payload, security.signature, secret, ignore_fields=ignore_fields):
            logger.critical(f"SIGNATURE VERIFICATION FAILED from worker {worker_id}")
            raise PermissionError("Invalid cryptographic signature")

    async def register_worker(self, worker_data: dict[str, Any], authenticated_worker_id: str) -> None:
        """
        Registers a new worker.
        :param worker_data: Raw dictionary from request (validated against WorkerRegistration)
        """
        worker_id = worker_data.get("worker_id")
        if not isinstance(worker_id, str):
            raise ValueError("Missing or invalid worker_id in registration data")

        # Immediate sender must be who they say they are
        if worker_id != authenticated_worker_id:
            logger.critical(f"SPOOFING DETECTED: Worker {authenticated_worker_id} tried to register as {worker_id}")
            raise PermissionError("Immediate sender ID mismatch")

        # Zero Trust Verification
        await self._verify_zero_trust(worker_data, worker_id, secret=None)

        # Ensure worker meets the protocol registration requirements
        try:
            validated_reg = from_dict(WorkerRegistration, worker_data)
            # Re-serialize to dict to ensure standard structure and types in storage
            worker_data = to_dict(validated_reg)
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Worker registration failed validation: {e}")
            raise ValueError(f"Invalid WorkerRegistration payload: {e}") from e

        worker_data["supported_skills"] = worker_data.get("supported_skills") or []

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

        logger.info(f"Worker '{worker_id}' registered (timestamp: {worker_data.get('timestamp')})")

        await self.history_storage.log_worker_event(
            {
                "worker_id": worker_id,
                "event_type": "registered",
                "worker_info_snapshot": worker_data,
                "timestamp": worker_data.get("timestamp"),
            }
        )

    async def get_next_task(self, worker_id: str) -> dict[str, Any] | None:
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
                    # Update assignment in case of work stealing
                    state["assigned_worker_id"] = worker_id
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

                    # Enrich task with security context and metadata from the job state
                    if updated_state.get("security"):
                        task["security"] = updated_state["security"]
                    if updated_state.get("metadata"):
                        task["metadata"] = updated_state["metadata"]

                    # Beta 10: Include required skill version/type in the task payload for worker validation
                    if updated_state.get("skill_version"):
                        task["skill_version"] = updated_state["skill_version"]
                    if updated_state.get("skill_type"):
                        task["skill_type"] = updated_state["skill_type"]

                    logger.info(f"Task {task.get('task_id')} for job {job_id} picked up by worker {worker_id}")
                except Exception as e:
                    logger.error(f"Failed to update pick-up time for job {job_id}: {e}")

        return task

    async def process_task_result(self, result_payload: dict[str, Any], authenticated_worker_id: str) -> dict[str, Any]:
        """
        Processes a task result submitted by a worker.
        """
        # Zero Trust Verification
        await self._verify_zero_trust(result_payload, authenticated_worker_id)

        # Optimistically decrement load counter (task finished)
        if authenticated_worker_id != "ghost":
            await self.storage.decrement_worker_load(authenticated_worker_id)

        # Ensure result matches protocol TaskResult
        try:
            validated_res = from_dict(TaskResult, result_payload)
            # Beta 8: Handle optional worker_id - fill from authenticated identity if missing
            if validated_res.worker_id is None:
                validated_res = validated_res._replace(worker_id=authenticated_worker_id)

            # Re-sync payload from the strictly typed model
            result_payload = to_dict(validated_res)
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Task result failed protocol validation: {e}")
            raise ValueError(f"Invalid TaskResult payload: {e}") from e

        job_id = validated_res.job_id
        task_id = validated_res.task_id

        # Now result_payload always has worker_id from either the payload or auth
        payload_worker_id = result_payload["worker_id"]

        if payload_worker_id != authenticated_worker_id:
            raise PermissionError(
                f"Forbidden: Authenticated worker '{authenticated_worker_id}' "
                f"cannot submit results for another worker '{payload_worker_id}'."
            )

        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            return {
                "status": "ignored",
                "reason": IGNORED_REASON_NOT_FOUND,
                "message": f"Job {job_id} not found (expired or deleted).",
            }

        # Use status strictly from the model (model b8 has 'success' as default)
        result_status = validated_res.status
        worker_data_content = validated_res.data

        # Find the exact skill definition used
        skill_snapshot: SkillInfo | None = None
        if authenticated_worker_id:
            try:
                worker_info = await self.storage.get_worker_info(authenticated_worker_id)
                if worker_info:
                    task_type = job_state.get("current_task_info", {}).get("type")

                    # Search strictly by 'name' as per b8
                    for skill_data in worker_info.get("supported_skills", []):
                        if skill_data.get("name") == task_type:
                            skill_snapshot = from_dict(SkillInfo, skill_data)
                            break
            except Exception as e:
                logger.warning(f"Failed to fetch skill snapshot for validation (job {job_id}): {e}")

        # Check if result matches skill's output_schema
        if result_status == TASK_STATUS_SUCCESS and skill_snapshot:
            # Beta 8: Only validate if dialect is json-schema
            if skill_snapshot.schema_dialect == "json-schema" and skill_snapshot.output_schema:
                is_valid, error_msg = validate_data(worker_data_content, skill_snapshot.output_schema)
                if not is_valid:
                    full_error = f"Contract violation: result data does not match output_schema. {error_msg}"
                    logger.error(f"Worker {authenticated_worker_id} violated contract for job {job_id}: {full_error}")

                    penalty = self.config.REPUTATION_PENALTY_CONTRACT_VIOLATION
                    worker_info = await self.storage.get_worker_info(authenticated_worker_id)
                    if worker_info:
                        new_rep = max(0.0, worker_info.get("reputation", 1.0) - penalty)
                        await self.storage.update_worker_data(authenticated_worker_id, {"reputation": new_rep})
                        logger.warning(
                            f"Worker {authenticated_worker_id} reputation decreased by {penalty} "
                            f"to {new_rep:.2f} due to contract violation."
                        )
                    result_status = TASK_STATUS_FAILURE
                    result_payload["status"] = TASK_STATUS_FAILURE
                    result_payload["error"] = {"code": ERROR_CODE_CONTRACT_VIOLATION, "message": full_error}
            elif skill_snapshot.schema_dialect != "json-schema":
                logger.debug(
                    f"Skipping output validation for job {job_id}: "
                    f"dialect '{skill_snapshot.schema_dialect}' not supported for native validation."
                )

        # Verify that the worker submitting the result is the one assigned to the task
        task_worker_id = job_state.get("task_worker_id")
        if task_worker_id and task_worker_id != authenticated_worker_id:
            logger.critical(
                f"HIJACKING ATTEMPT DETECTED: Worker {authenticated_worker_id} tried to submit result "
                f"for job {job_id} task {task_id}, which is assigned to worker {task_worker_id}."
            )
            return {
                "status": "ignored",
                "reason": IGNORED_REASON_MISMATCH,
                "message": "Forbidden: This task is assigned to a different worker.",
            }

        # SAFETY CHECK: Verify that the submitted task_id matches the current task of the job.
        # Skip this check for parallel branches as they have their own branch IDs.
        current_task_id = job_state.get("current_task_id")
        current_status = job_state.get("status")

        if current_status != JOB_STATUS_WAITING_FOR_PARALLEL and current_task_id and current_task_id != task_id:
            logger.warning(
                f"Received STALE result for job {job_id}. "
                f"Submitted task_id: {task_id}, current task_id in job: {current_task_id}. Ignoring."
            )
            return {
                "status": "ignored",
                "reason": IGNORED_REASON_STALE,
                "message": f"Result ignored: task_id {task_id} is no longer active for job {job_id}.",
            }

        # Protection: Terminal states check
        if current_status in TERMINAL_STATES:
            error_message = (job_state.get("error_message") or "").lower()
            if "timeout" in error_message:
                reason = IGNORED_REASON_LATE
            elif current_status == JOB_STATUS_CANCELLED:
                reason = IGNORED_REASON_CANCELLED
            else:
                reason = "job_already_terminated"

            logger.warning(
                f"Received late result for job {job_id} (status: {current_status}, reason: {reason}). Ignoring."
            )

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

        if current_status == JOB_STATUS_WAITING_FOR_PARALLEL:
            await self.storage.remove_job_from_watch(f"{job_id}:{task_id}")

            def _update_parallel_results(state: dict[str, Any]) -> dict[str, Any]:
                state.setdefault("aggregation_results", {})[task_id] = result_payload

                if result_payload.get("security"):
                    state["security"] = result_payload["security"]
                if result_payload.get("metadata"):
                    state.setdefault("metadata", {}).update(result_payload["metadata"])

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
                "origin_task_id": validated_res.task_id,  # Beta 10: Trace graph back to origin task
                "timestamp": result_payload.get("timestamp"),
                "context_snapshot": {
                    **job_state,
                    "result": result_payload,
                    "skill_snapshot": skill_snapshot,
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

            if result_payload.get("security"):
                job_state["security"] = result_payload["security"]
            if result_payload.get("metadata"):
                job_state.setdefault("metadata", {}).update(result_payload["metadata"])
            if result_payload.get("timestamp"):
                job_state["timestamp"] = result_payload["timestamp"]

            job_state["current_state"] = next_state
            job_state["status"] = JOB_STATUS_RUNNING

            reward = self.config.REPUTATION_REWARD_SUCCESS
            if authenticated_worker_id and reward > 0:
                worker_info = await self.storage.get_worker_info(authenticated_worker_id)
                if worker_info:
                    new_rep = min(1.0, worker_info.get("reputation", 1.0) + reward)
                    await self.storage.update_worker_data(authenticated_worker_id, {"reputation": new_rep})

            await self.storage.save_job_state(job_id, job_state)
            await self.storage.enqueue_job(job_id)
            return {"status": "accepted", "transition": "success_transition"}
        else:
            logger.error(f"Job {job_id} failed. Worker returned unhandled status '{result_status}'.")
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = f"Worker returned unhandled status: {result_status}"
            await self.storage.save_job_state(job_id, job_state)
            await self._cleanup_s3_if_needed(job_id)
            return {"status": "accepted", "transition": "unhandled_status_failure"}

    async def _cleanup_s3_if_needed(self, job_id: str) -> None:
        if self.config.S3_AUTO_CLEANUP:
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
            penalty = self.config.REPUTATION_PENALTY_TASK_FAILURE
            if error_type == ERROR_CODE_CONTRACT_VIOLATION:
                penalty = self.config.REPUTATION_PENALTY_CONTRACT_VIOLATION

            worker_id = job_state.get("task_worker_id")
            if worker_id:
                worker_info = await self.storage.get_worker_info(worker_id)
                if worker_info:
                    new_rep = max(0.0, worker_info.get("reputation", 1.0) - penalty)
                    await self.storage.update_worker_data(worker_id, {"reputation": new_rep})

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
        else:
            await self.engine.handle_task_failure(job_state, task_id, error_message)

    async def process_worker_event(self, authenticated_worker_id: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
        await self._verify_zero_trust(raw_payload, authenticated_worker_id, ignore_fields=["bubbling_chain"])

        try:
            event = from_dict(WorkerEventPayload, raw_payload)
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Event payload failed protocol validation: {e}")
            return {"status": "error", "message": f"Invalid event payload: {e}"}

        if event.worker_id != authenticated_worker_id:
            raise PermissionError("Immediate sender ID mismatch")

        events_schema = None
        dialect = "json-schema"

        if authenticated_worker_id == "ghost":
            job_state = await self.storage.get_job_state(event.target_job_id or event.target_task_id)
            if job_state:
                contract = self.engine.blueprint_contracts.get(job_state["blueprint_name"], {})
                events_schema = contract.get("events_schema")
                dialect = contract.get("schema_dialect", "json-schema")
        else:
            worker_info = await self.storage.get_worker_info(authenticated_worker_id)
            if worker_info and (event.target_task_id or event.target_job_id):
                job_state = await self.storage.get_job_state(event.target_job_id or event.target_task_id)
                if job_state:
                    task_type = job_state.get("current_task_info", {}).get("type")

                    for skill_data in worker_info.get("supported_skills", []):
                        if skill_data.get("name") == task_type or skill_data.get("type") == task_type:
                            skill = from_dict(SkillInfo, skill_data)
                            events_schema = skill.events_schema
                            dialect = skill.schema_dialect
                            break

        if events_schema and dialect == "json-schema":
            if event.event_type in events_schema:
                is_valid, error_msg = validate_data(event.payload, events_schema[event.event_type])
                if not is_valid:
                    return {"status": "error", "code": ERROR_CODE_CONTRACT_VIOLATION, "message": error_msg}
        elif events_schema and dialect != "json-schema":
            logger.debug(f"Skipping event validation for dialect '{dialect}'")

        new_chain = list(event.bubbling_chain or [])
        if (
            authenticated_worker_id != "ghost"
            and authenticated_worker_id not in new_chain
            and authenticated_worker_id != event.origin_worker_id
        ):
            new_chain.append(authenticated_worker_id)

        event = event._replace(bubbling_chain=new_chain)

        if event.event_type == EVENT_TYPE_PROGRESS:
            target_id = event.target_job_id or event.target_task_id
            if target_id:
                progress = event.payload.get("progress", 0.0)
                message = event.payload.get("message", "")

                async def _update_progress(state: dict[str, Any]) -> dict[str, Any]:
                    state["progress"] = progress
                    state["progress_message"] = message
                    return state

                with contextlib.suppress(Exception):
                    await self.storage.update_job_state_atomic(target_id, _update_progress)

        if event.target_job_id:
            job_state = await self.storage.get_job_state(event.target_job_id)
            if job_state and job_state.get("status") == JOB_STATUS_WAITING_FOR_WORKER:
                event_transitions = job_state.get("current_task_event_transitions", {})
                if next_state := event_transitions.get(event.event_type):
                    if "state_history" not in job_state:
                        job_state["state_history"] = {}
                    job_state["state_history"].update(event.payload)
                    job_state["current_task_id"] = f"superseded_by_event:{event.event_id}"
                    job_state["current_state"] = next_state
                    job_state["status"] = JOB_STATUS_RUNNING
                    await self.storage.save_job_state(event.target_job_id, job_state)
                    await self.storage.remove_job_from_watch(event.target_job_id)
                    await self.storage.enqueue_job(event.target_job_id)

        # Use event timestamp if available, otherwise fallback to current time
        event_ts = event.timestamp or int(time())

        await self.history_storage.log_job_event(
            {
                "job_id": event.target_job_id or "system",
                "state": "running",
                "event_type": f"worker_event:{event.event_type}",
                "worker_id": event.origin_worker_id,
                "timestamp": event_ts,
                "context_snapshot": {
                    "event_id": event.event_id,
                    "payload": event.payload,
                    "sender_id": authenticated_worker_id,
                },
            }
        )

        if self.engine.on_worker_event:
            for callback in self.engine.on_worker_event:
                create_task(callback(authenticated_worker_id, event))

        if event.target_job_id:
            job_state = await self.storage.get_job_state(event.target_job_id)
            if job_state and job_state.get("webhook_url"):
                webhook_payload = WebhookPayload(
                    event=f"worker_event:{event.event_type}",
                    job_id=event.target_job_id,
                    status=job_state.get("status", "running"),
                    result=event.payload,
                    security=to_dict(event.security) if event.security else None,
                    metadata=event.metadata,
                    timestamp=event.timestamp,
                )
                await self.engine.webhook_sender.send(job_state["webhook_url"], webhook_payload)

        return {"status": "event_accepted"}

    async def issue_access_token(self, worker_id: str) -> TokenResponse:
        raw_token = token_urlsafe(32)
        token_hash = sha256(raw_token.encode()).hexdigest()
        await self.storage.save_worker_access_token(worker_id, token_hash, 3600)
        return TokenResponse(access_token=raw_token, expires_in=3600, worker_id=worker_id)

    async def update_worker_heartbeat(
        self, worker_id: str, update_data: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        try:
            ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
            if update_data:
                await self._verify_zero_trust(update_data, worker_id)
                response_data: dict[str, Any] = {"status": "ok"}
                new_hash = update_data.get("skills_hash")
                current_worker = await self.storage.get_worker_info(worker_id)
                if current_worker and new_hash and current_worker.get("skills_hash") == new_hash:
                    if not current_worker.get("supported_skills"):
                        response_data[HB_RESP_REQUIRE_FULL_SYNC] = True
                    update_data.pop("supported_skills", None)
                updated_worker = await self.storage.update_worker_status(worker_id, update_data, ttl)
                if updated_worker:
                    await self.history_storage.log_worker_event(
                        {
                            "worker_id": worker_id,
                            "event_type": "status_update",
                            "worker_info_snapshot": updated_worker,
                            "timestamp": update_data.get("timestamp"),
                        }
                    )
                    current_tasks = update_data.get("current_tasks", []) or []
                    if current_tasks:
                        cancel_ids = []
                        for tid in current_tasks:
                            js = await self.storage.get_job_state(tid)
                            if (
                                not js
                                or js.get("status") in TERMINAL_STATES
                                or js.get("status") == JOB_STATUS_CANCELLED
                            ):
                                cancel_ids.append(tid)
                        if cancel_ids:
                            response_data[HB_RESP_CANCEL_TASKS] = cancel_ids
                return response_data
            else:
                refreshed = await self.storage.refresh_worker_ttl(worker_id, ttl)
                return {"status": "ttl_refreshed"} if refreshed else None
        except Exception as e:
            logger.exception(f"Error in heartbeat: {e}")
            raise e

from asyncio import CancelledError, Task, create_task, sleep
from logging import getLogger
from time import monotonic
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

# Conditional import for OpenTelemetry
try:
    from opentelemetry import trace
    from opentelemetry.propagate import inject
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    tracer = trace.get_tracer(__name__)
except ImportError:
    logger = getLogger(__name__)
    logger.info("OpenTelemetry not found. Tracing will be disabled.")

    class NoOpTracer:
        def start_as_current_span(self, *args, **kwargs):
            class NoOpSpan:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass

                def set_attribute(self, *args, **kwargs):
                    pass

            return NoOpSpan()

    class NoOpPropagate:
        def inject(self, *args, **kwargs):
            pass

        @staticmethod
        def extract(*args, **kwargs):
            return None

    class NoOpTraceContextTextMapPropagator:
        @staticmethod
        def extract(*args, **kwargs):
            return None

    trace = NoOpTracer()
    inject = NoOpPropagate().inject
    TraceContextTextMapPropagator = NoOpTraceContextTextMapPropagator()  # Instantiate the class

from .app_keys import S3_SERVICE_KEY
from .constants import (
    JOB_STATUS_ERROR,
    JOB_STATUS_FAILED,
    JOB_STATUS_FINISHED,
    JOB_STATUS_PENDING,
    JOB_STATUS_QUARANTINED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_FOR_PARALLEL,
    JOB_STATUS_WAITING_FOR_WORKER,
)
from .context import ActionFactory
from .data_types import ClientConfig, JobContext
from .history.base import HistoryStorageBase

if TYPE_CHECKING:
    from .engine import OrchestratorEngine

logger = getLogger(
    __name__
)  # Re-declare logger after potential redefinition in except block if opentelemetry was missing

TERMINAL_STATES = {JOB_STATUS_FINISHED, JOB_STATUS_FAILED, JOB_STATUS_ERROR, JOB_STATUS_QUARANTINED}


class JobExecutor:
    def __init__(
        self,
        engine: "OrchestratorEngine",
        history_storage: "HistoryStorageBase",
    ):
        self.engine = engine
        self.storage = engine.storage
        self.history_storage = history_storage
        self.dispatcher = engine.dispatcher
        self._running = False
        self._processing_messages: set[str] = set()

    async def _process_job(self, job_id: str, message_id: str) -> None:
        """The core logic for processing a single job dequeued from storage."""
        if message_id in self._processing_messages:
            return

        self._processing_messages.add(message_id)
        try:
            start_time = monotonic()
            job_state = await self.storage.get_job_state(job_id)
            if not job_state:
                logger.error(f"Job {job_id} not found in storage, cannot process.")
                return

            if job_state.get("status") in TERMINAL_STATES:
                logger.warning(f"Job {job_id} is already in a terminal state '{job_state['status']}', skipping.")
                return

            if "retry_count" not in job_state:
                job_state["retry_count"] = 0

            await self.history_storage.log_job_event(
                {
                    "job_id": job_id,
                    "state": job_state.get("current_state"),
                    "event_type": "state_started",
                    "attempt_number": job_state.get("retry_count", 0) + 1,
                    "context_snapshot": job_state,
                },
            )

            parent_context = TraceContextTextMapPropagator().extract(
                carrier=job_state.get("tracing_context", {}),
            )

            with tracer.start_as_current_span(
                f"JobExecutor:{job_state['blueprint_name']}:{job_state['current_state']}",
                context=parent_context,
            ) as span:
                span.set_attribute("job.id", job_id)
                span.set_attribute("job.current_state", job_state["current_state"])

                tracing_context: dict[str, str] = {}
                inject(tracing_context)
                job_state["tracing_context"] = tracing_context

                blueprint = self.engine.blueprints.get(job_state["blueprint_name"])
                if not blueprint:
                    duration_ms = int((monotonic() - start_time) * 1000)
                    await self._handle_failure(
                        job_state,
                        RuntimeError(
                            f"Blueprint '{job_state['blueprint_name']}' not found",
                        ),
                        duration_ms,
                    )
                    return

                action_factory = ActionFactory(
                    job_id,
                    default_dispatch_timeout=job_state.get("dispatch_timeout"),
                    default_result_timeout=job_state.get("result_timeout"),
                )
                client_config_dict = job_state.get("client_config", {})
                client_config = ClientConfig(
                    token=client_config_dict.get("token", ""),
                    plan=client_config_dict.get("plan", "unknown"),
                    params=client_config_dict.get("params", {}),
                )

                s3_service = self.engine.app.get(S3_SERVICE_KEY)
                task_files = s3_service.get_task_files(job_id) if s3_service else None

                context = JobContext(
                    job_id=job_id,
                    current_state=job_state["current_state"],
                    initial_data=job_state["initial_data"],
                    state_history=job_state.get("state_history", {}),
                    client=client_config,
                    actions=action_factory,
                    data_stores=SimpleNamespace(**blueprint.data_stores),
                    tracing_context=tracing_context,
                    aggregation_results=job_state.get("aggregation_results"),
                    task_files=task_files,
                )

                try:
                    # Aggregator handlers take priority for states that are targets of parallel execution.
                    is_aggregator_state = job_state.get("aggregation_target") == job_state.get("current_state")
                    if is_aggregator_state and job_state.get("current_state") in blueprint.aggregator_handlers:
                        handler = blueprint.aggregator_handlers[job_state["current_state"]]
                    else:
                        handler = blueprint.find_handler(context.current_state, context)

                    param_names = blueprint.get_handler_params(handler)
                    params_to_inject: dict[str, Any] = {}

                    if "context" in param_names:
                        params_to_inject["context"] = context
                        if "actions" in param_names:
                            params_to_inject["actions"] = action_factory
                        if "task_files" in param_names:
                            params_to_inject["task_files"] = task_files
                    else:
                        context_as_dict = context._asdict()
                        for param_name in param_names:
                            if param_name == "task_files":
                                params_to_inject[param_name] = task_files
                            elif param_name in context_as_dict:
                                params_to_inject[param_name] = context_as_dict[param_name]
                            elif param_name in context.state_history:
                                params_to_inject[param_name] = context.state_history[param_name]
                            elif param_name in context.initial_data:
                                params_to_inject[param_name] = context.initial_data[param_name]

                    await handler(**params_to_inject)

                    duration_ms = int((monotonic() - start_time) * 1000)

                    if action_factory.next_state:
                        await self._handle_transition(
                            job_state,
                            action_factory.next_state,
                            duration_ms,
                        )
                    elif action_factory.task_to_dispatch:
                        await self._handle_dispatch(
                            job_state,
                            action_factory.task_to_dispatch,
                            duration_ms,
                        )
                    elif action_factory.parallel_tasks_to_dispatch:
                        await self._handle_parallel_dispatch(
                            job_state,
                            action_factory.parallel_tasks_to_dispatch,
                            duration_ms,
                        )
                    elif action_factory.sub_blueprint_to_run:
                        await self._handle_run_blueprint(
                            job_state,
                            action_factory.sub_blueprint_to_run,
                            duration_ms,
                        )
                    elif job_state["current_state"] in blueprint.end_states:
                        status = JOB_STATUS_FINISHED if job_state["current_state"] == "finished" else JOB_STATUS_FAILED
                        await self._handle_terminal_reached(job_state, status, duration_ms)

                except Exception as e:
                    duration_ms = int((monotonic() - start_time) * 1000)
                    await self._handle_failure(job_state, e, duration_ms)
        finally:
            await self.storage.ack_job(message_id)
            if message_id in self._processing_messages:
                self._processing_messages.remove(message_id)

    async def _handle_terminal_reached(
        self,
        job_state: dict[str, Any],
        status: str,
        duration_ms: int,
    ) -> None:
        job_id = job_state["id"]
        current_state = job_state["current_state"]
        logger.info(f"Job {job_id} reached terminal state '{current_state}' with status '{status}'")

        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": current_state,
                "event_type": "job_completed",
                "duration_ms": duration_ms,
                "context_snapshot": job_state,
            },
        )

        job_state["status"] = status
        await self.storage.save_job_state(job_id, job_state)

        # Clean up S3 files if service is available and auto-cleanup is enabled
        s3_service = self.engine.app.get(S3_SERVICE_KEY)
        if s3_service and self.engine.config.S3_AUTO_CLEANUP:
            task_files = s3_service.get_task_files(job_id)
            if task_files:
                create_task(task_files.cleanup())

        await self._check_and_resume_parent(job_state)
        event_type = "job_finished" if status == JOB_STATUS_FINISHED else "job_failed"
        await self.engine.send_job_webhook(job_state, event_type)

    async def _handle_transition(
        self,
        job_state: dict[str, Any],
        next_state: str,
        duration_ms: int,
    ) -> None:
        job_id = job_state["id"]
        previous_state = job_state["current_state"]
        logger.info(f"Job {job_id} transitioning from {previous_state} to {next_state}")

        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": previous_state,
                "event_type": "state_finished",
                "duration_ms": duration_ms,
                "previous_state": previous_state,
                "next_state": next_state,
                "context_snapshot": job_state,
            },
        )

        job_state["retry_count"] = 0
        job_state["current_state"] = next_state
        job_state["status"] = JOB_STATUS_RUNNING
        await self.storage.save_job_state(job_id, job_state)
        await self.storage.enqueue_job(job_id)

    async def _handle_dispatch(
        self,
        job_state: dict[str, Any],
        task_info: dict[str, Any],
        duration_ms: int,
    ) -> None:
        job_id = job_state["id"]
        current_state = job_state["current_state"]

        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": current_state,
                "event_type": "task_dispatched",
                "duration_ms": duration_ms,
                "context_snapshot": {**job_state, "task_info": task_info},
            },
        )

        if task_info.get("type") == "human_approval":
            job_state["status"] = "waiting_for_human"
            job_state["current_task_transitions"] = task_info.get("transitions", {})
            await self.storage.save_job_state(job_id, job_state)
            logger.info(f"Job {job_id} is now paused, awaiting human approval.")
        else:
            logger.info(f"Job {job_id} dispatching task: {task_info}")
            now = monotonic()

            dispatch_timeout = task_info.get("dispatch_timeout_seconds")
            result_timeout = task_info.get("result_timeout_seconds")
            execution_timeout = task_info.get("timeout_seconds")

            # Absolute deadlines from now
            dispatch_deadline = now + dispatch_timeout if dispatch_timeout is not None else None
            result_deadline = now + result_timeout if result_timeout is not None else None

            # Initial watch: wait for the EARLIEST event (either too late to start or too late for result)
            deadlines = [d for d in (dispatch_deadline, result_deadline) if d is not None]
            if deadlines:
                timeout_at = min(deadlines)
            else:
                # Global fallback if no specific timeouts are set
                timeout_at = now + (execution_timeout or self.engine.config.WORKER_TIMEOUT_SECONDS)

            job_state["status"] = JOB_STATUS_WAITING_FOR_WORKER
            job_state["task_dispatched_at"] = now
            job_state["current_task_info"] = task_info
            job_state["current_task_transitions"] = task_info.get("transitions", {})

            # Store deadlines for WorkerService and Watcher
            job_state["dispatch_deadline"] = dispatch_deadline
            job_state["result_deadline"] = result_deadline
            job_state["execution_timeout_seconds"] = execution_timeout

            await self.storage.save_job_state(job_id, job_state)
            await self.storage.add_job_to_watch(job_id, timeout_at)
            await self.dispatcher.dispatch(job_state, task_info)

    async def _handle_run_blueprint(
        self,
        parent_job_state: dict[str, Any],
        sub_blueprint_info: dict[str, Any],
        duration_ms: int,
    ) -> None:
        parent_job_id = parent_job_state["id"]
        child_job_id = str(uuid4())

        await self.history_storage.log_job_event(
            {
                "job_id": parent_job_id,
                "state": parent_job_state.get("current_state"),
                "event_type": "sub_blueprint_started",
                "duration_ms": duration_ms,
                "next_state": sub_blueprint_info.get("blueprint_name"),
                "context_snapshot": parent_job_state,
            },
        )

        child_job_state = {
            "id": child_job_id,
            "blueprint_name": sub_blueprint_info["blueprint_name"],
            "current_state": "start",
            "initial_data": sub_blueprint_info["initial_data"],
            "status": JOB_STATUS_PENDING,
            "parent_job_id": parent_job_id,
            "dispatch_timeout": sub_blueprint_info.get("dispatch_timeout"),
            "result_timeout": sub_blueprint_info.get("result_timeout"),
        }
        await self.storage.save_job_state(child_job_id, child_job_state)
        await self.storage.enqueue_job(child_job_id)

        parent_job_state["status"] = "waiting_for_sub_job"
        parent_job_state["child_job_id"] = child_job_id
        parent_job_state["current_task_transitions"] = sub_blueprint_info.get(
            "transitions",
            {},
        )
        await self.storage.save_job_state(parent_job_id, parent_job_state)
        logger.info(f"Job {parent_job_id} paused, starting sub-job {child_job_id}.")

    async def _handle_parallel_dispatch(
        self,
        job_state: dict[str, Any],
        parallel_info: dict[str, Any],
        duration_ms: int,
    ) -> None:
        job_id = job_state["id"]
        tasks_to_dispatch = parallel_info["tasks"]
        aggregate_into = parallel_info["aggregate_into"]

        logger.info(
            f"Job {job_id} dispatching {len(tasks_to_dispatch)} tasks in parallel, "
            f"aggregating into '{aggregate_into}'.",
        )

        branch_task_ids = [str(uuid4()) for _ in tasks_to_dispatch]

        # Update job state for parallel execution
        job_state["status"] = JOB_STATUS_WAITING_FOR_PARALLEL
        job_state["aggregation_target"] = aggregate_into
        job_state["active_branches"] = branch_task_ids
        job_state["aggregation_results"] = {}
        await self.storage.save_job_state(job_id, job_state)

        # Dispatch each task as a "branch"
        for i, task_info in enumerate(tasks_to_dispatch):
            branch_id = branch_task_ids[i]

            # We need to create a "shadow" task_info that includes the branch_id
            # This is because the original task_info from the blueprint doesn't have it.
            # We also inject the job's tracing context for distributed tracing.
            full_task_info = {
                "task_id": branch_id,
                "job_id": job_id,
                "tracing_context": job_state.get("tracing_context", {}),
                **task_info,
            }

            now = monotonic()
            dispatch_timeout = task_info.get("dispatch_timeout_seconds")
            result_timeout = task_info.get("result_timeout_seconds")
            execution_timeout = task_info.get("timeout_seconds")

            # Absolute deadlines for the branch
            dispatch_deadline = now + dispatch_timeout if dispatch_timeout is not None else None
            result_deadline = now + result_timeout if result_timeout is not None else None

            deadlines = [d for d in (dispatch_deadline, result_deadline) if d is not None]
            if deadlines:
                timeout_at = min(deadlines)
            else:
                timeout_at = now + (execution_timeout or self.engine.config.WORKER_TIMEOUT_SECONDS)

            # Note: For parallel branches, we store deadlines in the task info itself
            # or rely on the Watcher to check the branch's specific watch entry.
            # We add branch-specific info for the Watcher to use if needed.

            await self.storage.add_job_to_watch(
                f"{job_id}:{branch_id}",
                timeout_at,
            )  # Watch each branch
            await self.dispatcher.dispatch(job_state, full_task_info)

    async def _handle_failure(
        self,
        job_state: dict[str, Any],
        error: Exception,
        duration_ms: int,
    ) -> None:
        """Handles failures that occur *during the execution of a handler*.

        This is different from a task failure reported by a worker. This logic
        retries the handler execution itself and, if it repeatedly fails,
        moves the job to quarantine.
        """
        job_id = job_state["id"]
        current_retries = job_state.get("retry_count", 0)
        max_retries = self.engine.config.JOB_MAX_RETRIES
        current_state = job_state.get("current_state")

        logger.exception(
            f"Error executing handler for job {job_id}. Attempt {current_retries + 1}/{max_retries}.",
        )

        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": current_state,
                "event_type": "state_failed",
                "duration_ms": duration_ms,
                "attempt_number": current_retries + 1,
                "context_snapshot": {**job_state, "error_message": str(error)},
            },
        )

        if current_retries < max_retries:
            job_state["retry_count"] = current_retries + 1
            job_state["status"] = "awaiting_retry"
            job_state["error_message"] = str(error)
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.enqueue_job(job_id)
            logger.warning(
                f"Job {job_id} failed in-handler, will be retried. Attempt {job_state['retry_count']}.",
            )
        else:
            logger.critical(
                f"Job {job_id} has failed handler execution {max_retries + 1} times. Moving to quarantine.",
            )
            job_state["status"] = JOB_STATUS_QUARANTINED
            job_state["error_message"] = str(error)
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.quarantine_job(job_id)

            s3_service = self.engine.app.get(S3_SERVICE_KEY)
            if s3_service and self.engine.config.S3_AUTO_CLEANUP:
                task_files = s3_service.get_task_files(job_id)
                if task_files:
                    create_task(task_files.cleanup())

            await self._check_and_resume_parent(job_state)
            await self.engine.send_job_webhook(job_state, "job_quarantined")
            from . import metrics

            metrics.jobs_failed_total.inc(
                {metrics.LABEL_BLUEPRINT: job_state.get("blueprint_name", "unknown")},
            )

    async def _check_and_resume_parent(self, child_job_state: dict[str, Any]) -> None:
        """Checks if a completed job was a sub-job. If so, it resumes the parent
        job, passing the success/failure outcome of the child.
        """
        parent_job_id = child_job_state.get("parent_job_id")
        if not parent_job_id:
            return  # Not a sub-job.

        child_job_id = child_job_state["id"]
        logger.info(
            f"Sub-job {child_job_id} finished. Resuming parent job {parent_job_id}.",
        )
        parent_job_state = await self.storage.get_job_state(parent_job_id)
        if not parent_job_state:
            logger.error(
                f"Parent job {parent_job_id} not found for child {child_job_id}.",
            )
            return

        child_outcome = "success" if child_job_state["current_state"] == JOB_STATUS_FINISHED else "failure"
        transitions = parent_job_state.get("current_task_transitions", {})
        next_state = transitions.get(child_outcome, "failed")

        if "state_history" not in parent_job_state:
            parent_job_state["state_history"] = {}
        parent_job_state["state_history"][f"sub_job_{child_job_id}_result"] = {
            "outcome": child_outcome,
            "final_state": child_job_state.get("current_state"),
            "error_message": child_job_state.get("error_message"),
        }

        parent_job_state["current_state"] = next_state
        parent_job_state["status"] = JOB_STATUS_RUNNING
        await self.storage.save_job_state(parent_job_id, parent_job_state)
        await self.storage.enqueue_job(parent_job_id)

    @staticmethod
    def _handle_task_completion(task: Task) -> None:
        """Callback to handle completion of a job processing task."""
        try:
            # This will re-raise any exception caught in the task
            task.result()
        except CancelledError:
            # Task was cancelled, which is a normal part of shutdown.
            pass
        except Exception:
            # Log any other exceptions that occurred in the task.
            logger.exception("Unhandled exception in job processing task")

    async def run(self) -> None:
        import asyncio

        logger.info("JobExecutor started.")
        self._running = True
        semaphore = asyncio.Semaphore(self.engine.config.EXECUTOR_MAX_CONCURRENT_JOBS)

        while self._running:
            try:
                await semaphore.acquire()

                block_time = self.engine.config.REDIS_STREAM_BLOCK_MS
                result = await self.storage.dequeue_job(block=block_time if block_time > 0 else None)

                if result:
                    job_id, message_id = result
                    task = create_task(self._process_job(job_id, message_id))
                    task.add_done_callback(self._handle_task_completion)
                    task.add_done_callback(lambda _: semaphore.release())
                else:
                    semaphore.release()
                    if block_time <= 0:
                        await sleep(0.1)

            except CancelledError:
                break
            except Exception:
                logger.exception("Error in JobExecutor main loop.")
                semaphore.release()
                await sleep(1)
        logger.info("JobExecutor stopped.")

    def stop(self) -> None:
        self._running = False

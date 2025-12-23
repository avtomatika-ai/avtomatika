from asyncio import Task, create_task, gather, get_running_loop, wait_for
from asyncio import TimeoutError as AsyncTimeoutError
from logging import getLogger
from typing import Callable, Dict
from uuid import uuid4

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.web import AppKey
from aioprometheus import render

from . import metrics
from .blueprint import StateMachineBlueprint
from .client_config_loader import load_client_configs_to_redis
from .compression import compression_middleware
from .config import Config
from .dispatcher import Dispatcher
from .executor import JobExecutor
from .health_checker import HealthChecker
from .history.base import HistoryStorageBase
from .history.noop import NoOpHistoryStorage
from .logging_config import setup_logging
from .quota import quota_middleware_factory
from .ratelimit import rate_limit_middleware_factory
from .reputation import ReputationCalculator
from .security import client_auth_middleware_factory, worker_auth_middleware_factory
from .storage.base import StorageBackend
from .telemetry import setup_telemetry
from .watcher import Watcher
from .worker_config_loader import load_worker_configs_to_redis
from .ws_manager import WebSocketManager

# Application keys for storing components
ENGINE_KEY = AppKey("engine", "OrchestratorEngine")
HTTP_SESSION_KEY = AppKey("http_session", ClientSession)
DISPATCHER_KEY = AppKey("dispatcher", Dispatcher)
EXECUTOR_KEY = AppKey("executor", JobExecutor)
WATCHER_KEY = AppKey("watcher", Watcher)
REPUTATION_CALCULATOR_KEY = AppKey("reputation_calculator", ReputationCalculator)
HEALTH_CHECKER_KEY = AppKey("health_checker", HealthChecker)
EXECUTOR_TASK_KEY = AppKey("executor_task", Task)
WATCHER_TASK_KEY = AppKey("watcher_task", Task)
REPUTATION_CALCULATOR_TASK_KEY = AppKey("reputation_calculator_task", Task)
HEALTH_CHECKER_TASK_KEY = AppKey("health_checker_task", Task)


metrics.init_metrics()


logger = getLogger(__name__)


async def status_handler(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def metrics_handler(_request: web.Request) -> web.Response:
    return web.Response(body=render(), content_type="text/plain")


class OrchestratorEngine:
    def __init__(self, storage: StorageBackend, config: Config):
        setup_logging(config.LOG_LEVEL, config.LOG_FORMAT)
        setup_telemetry()
        self.storage = storage
        self.config = config
        self.blueprints: Dict[str, StateMachineBlueprint] = {}
        self.history_storage: HistoryStorageBase = NoOpHistoryStorage()
        self.ws_manager = WebSocketManager()
        self.app = web.Application(middlewares=[compression_middleware])
        self.app[ENGINE_KEY] = self
        self._setup_done = False

    def register_blueprint(self, blueprint: StateMachineBlueprint):
        if self._setup_done:
            raise RuntimeError("Cannot register blueprints after engine setup.")
        if blueprint.name in self.blueprints:
            raise ValueError(
                f"Blueprint with name '{blueprint.name}' is already registered.",
            )
        blueprint.validate()
        self.blueprints[blueprint.name] = blueprint

    def setup(self):
        if self._setup_done:
            return
        self._setup_routes()
        self.app.on_startup.append(self.on_startup)
        self.app.on_shutdown.append(self.on_shutdown)
        self._setup_done = True

    async def _setup_history_storage(self):
        from importlib import import_module

        uri = self.config.HISTORY_DATABASE_URI
        storage_class = None
        storage_args = []

        if not uri:
            logger.info("History storage is disabled (HISTORY_DATABASE_URI is not set).")
            self.history_storage = NoOpHistoryStorage()
            return

        elif uri.startswith("sqlite:"):
            try:
                from urllib.parse import urlparse

                module = import_module(".history.sqlite", package="avtomatika")
                storage_class = module.SQLiteHistoryStorage
                parsed_uri = urlparse(uri)
                db_path = parsed_uri.path
                storage_args = [db_path]
            except ImportError as e:
                logger.error(f"Could not import SQLiteHistoryStorage, perhaps aiosqlite is not installed? Error: {e}")
                self.history_storage = NoOpHistoryStorage()
                return

        elif uri.startswith("postgresql:"):
            try:
                module = import_module(".history.postgres", package="avtomatika")
                storage_class = module.PostgresHistoryStorage
                storage_args = [uri]
            except ImportError as e:
                logger.error(f"Could not import PostgresHistoryStorage, perhaps asyncpg is not installed? Error: {e}")
                self.history_storage = NoOpHistoryStorage()
                return
        else:
            logger.warning(f"Unsupported HISTORY_DATABASE_URI scheme: {uri}. Disabling history storage.")
            self.history_storage = NoOpHistoryStorage()
            return

        if storage_class:
            self.history_storage = storage_class(*storage_args)
            try:
                await self.history_storage.initialize()
            except Exception as e:
                logger.error(
                    f"Failed to initialize history storage {storage_class.__name__}, disabling it. Error: {e}",
                    exc_info=True,
                )
                self.history_storage = NoOpHistoryStorage()

    async def on_startup(self, app: web.Application):
        try:
            from opentelemetry.instrumentation.aiohttp_client import (
                AioHttpClientInstrumentor,
            )

            AioHttpClientInstrumentor().instrument()
        except ImportError:
            logger.info(
                "opentelemetry-instrumentation-aiohttp-client not found. AIOHTTP client instrumentation is disabled."
            )
        await self._setup_history_storage()

        # Load client configs if the path is provided
        if self.config.CLIENTS_CONFIG_PATH:
            from os.path import exists

            if exists(self.config.CLIENTS_CONFIG_PATH):
                await load_client_configs_to_redis(self.storage, self.config.CLIENTS_CONFIG_PATH)
            else:
                logger.warning(
                    f"CLIENTS_CONFIG_PATH is set to '{self.config.CLIENTS_CONFIG_PATH}', but the file was not found."
                )
        else:
            logger.warning(
                "CLIENTS_CONFIG_PATH is not set. The system will rely on a single global CLIENT_TOKEN if configured, "
                "or deny access if no token is found."
            )

        # Load individual worker configs if the path is provided
        if self.config.WORKERS_CONFIG_PATH:
            from os.path import exists

            if exists(self.config.WORKERS_CONFIG_PATH):
                await load_worker_configs_to_redis(self.storage, self.config.WORKERS_CONFIG_PATH)
            else:
                logger.warning(
                    f"WORKERS_CONFIG_PATH is set to '{self.config.WORKERS_CONFIG_PATH}', but the file was not found."
                )
        else:
            logger.warning(
                "WORKERS_CONFIG_PATH is not set. "
                "Individual worker authentication will be disabled. "
                "The system will fall back to the global WORKER_TOKEN if set."
            )

        app[HTTP_SESSION_KEY] = ClientSession()
        self.dispatcher = Dispatcher(self.storage, self.config)
        app[DISPATCHER_KEY] = self.dispatcher
        app[EXECUTOR_KEY] = JobExecutor(self, self.history_storage)
        app[WATCHER_KEY] = Watcher(self)
        app[REPUTATION_CALCULATOR_KEY] = ReputationCalculator(self)
        app[HEALTH_CHECKER_KEY] = HealthChecker(self)

        app[EXECUTOR_TASK_KEY] = create_task(app[EXECUTOR_KEY].run())
        app[WATCHER_TASK_KEY] = create_task(app[WATCHER_KEY].run())
        app[REPUTATION_CALCULATOR_TASK_KEY] = create_task(app[REPUTATION_CALCULATOR_KEY].run())
        app[HEALTH_CHECKER_TASK_KEY] = create_task(app[HEALTH_CHECKER_KEY].run())

    async def on_shutdown(self, app: web.Application):
        logger.info("Shutdown sequence started.")
        app[EXECUTOR_KEY].stop()
        app[WATCHER_KEY].stop()
        app[REPUTATION_CALCULATOR_KEY].stop()
        app[HEALTH_CHECKER_KEY].stop()
        logger.info("Background task running flags set to False.")

        if hasattr(self.history_storage, "close"):
            logger.info("Closing history storage...")
            await self.history_storage.close()
            logger.info("History storage closed.")

        logger.info("Closing WebSocket connections...")
        await self.ws_manager.close_all()

        logger.info("Cancelling background tasks...")
        app[HEALTH_CHECKER_TASK_KEY].cancel()
        app[WATCHER_TASK_KEY].cancel()
        app[REPUTATION_CALCULATOR_TASK_KEY].cancel()
        app[EXECUTOR_TASK_KEY].cancel()
        logger.info("Background tasks cancelled.")

        logger.info("Gathering background tasks with a 10s timeout...")
        try:
            await wait_for(
                gather(
                    app[HEALTH_CHECKER_TASK_KEY],
                    app[WATCHER_TASK_KEY],
                    app[REPUTATION_CALCULATOR_TASK_KEY],
                    app[EXECUTOR_TASK_KEY],
                    return_exceptions=True,
                ),
                timeout=10.0,
            )
            logger.info("Background tasks gathered successfully.")
        except AsyncTimeoutError:
            logger.error("Timed out waiting for background tasks to shut down.")

        logger.info("Closing HTTP session...")
        await app[HTTP_SESSION_KEY].close()
        logger.info("HTTP session closed.")
        logger.info("Shutdown sequence finished.")

    def _create_job_handler(self, blueprint: StateMachineBlueprint) -> Callable:
        async def handler(request: web.Request) -> web.Response:
            try:
                initial_data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON body"}, status=400)

            client_config = request["client_config"]
            carrier = {str(k): v for k, v in request.headers.items()}

            job_id = str(uuid4())
            job_state = {
                "id": job_id,
                "blueprint_name": blueprint.name,
                "current_state": blueprint.start_state,
                "initial_data": initial_data,
                "state_history": {},
                "status": "pending",
                "tracing_context": carrier,
                "client_config": client_config,
            }
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.enqueue_job(job_id)
            metrics.jobs_total.inc({metrics.LABEL_BLUEPRINT: blueprint.name})
            return web.json_response({"status": "accepted", "job_id": job_id}, status=202)

        return handler

    async def _get_job_status_handler(self, request: web.Request) -> web.Response:
        job_id = request.match_info.get("job_id")
        if not job_id:
            return web.json_response({"error": "job_id is required in path"}, status=400)
        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            return web.json_response({"error": "Job not found"}, status=404)
        return web.json_response(job_state, status=200)

    async def _cancel_job_handler(self, request: web.Request) -> web.Response:
        job_id = request.match_info.get("job_id")
        if not job_id:
            return web.json_response({"error": "job_id is required in path"}, status=400)

        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            return web.json_response({"error": "Job not found"}, status=404)

        if job_state.get("status") != "waiting_for_worker":
            return web.json_response(
                {"error": "Job is not in a state that can be cancelled (must be waiting for a worker)."},
                status=409,
            )

        worker_id = job_state.get("task_worker_id")
        if not worker_id:
            return web.json_response(
                {"error": "Cannot cancel job: worker_id not found in job state."},
                status=500,
            )

        worker_info = await self.storage.get_worker_info(worker_id)
        task_id = job_state.get("current_task_id")
        if not task_id:
            return web.json_response(
                {"error": "Cannot cancel job: task_id not found in job state."},
                status=500,
            )

        # Set Redis flag as a reliable fallback/primary mechanism
        await self.storage.set_task_cancellation_flag(task_id)

        # Attempt WebSocket-based cancellation if supported
        if worker_info and worker_info.get("capabilities", {}).get("websockets"):
            command = {"command": "cancel_task", "task_id": task_id, "job_id": job_id}
            sent = await self.ws_manager.send_command(worker_id, command)
            if sent:
                return web.json_response({"status": "cancellation_request_sent"})
            else:
                logger.warning(f"Failed to send WebSocket cancellation for task {task_id}, but Redis flag is set.")
                # Proceed to return success, as the Redis flag will handle it

        return web.json_response({"status": "cancellation_request_accepted"})

    async def _get_job_history_handler(self, request: web.Request) -> web.Response:
        job_id = request.match_info.get("job_id")
        if not job_id:
            return web.json_response({"error": "job_id is required in path"}, status=400)
        history = await self.history_storage.get_job_history(job_id)
        return web.json_response(history)

    async def _get_blueprint_graph_handler(self, request: web.Request) -> web.Response:
        blueprint_name = request.match_info.get("blueprint_name")
        if not blueprint_name:
            return web.json_response({"error": "blueprint_name is required in path"}, status=400)

        blueprint = self.blueprints.get(blueprint_name)
        if not blueprint:
            return web.json_response({"error": "Blueprint not found"}, status=404)

        try:
            graph_dot = blueprint.render_graph()
            return web.Response(text=graph_dot, content_type="text/vnd.graphviz")
        except FileNotFoundError:
            error_msg = "Graphviz is not installed on the server. Cannot generate graph."
            logger.error(error_msg)
            return web.json_response({"error": error_msg}, status=501)

    async def _get_workers_handler(self, request: web.Request) -> web.Response:
        workers = await self.storage.get_available_workers()
        return web.json_response(workers)

    async def _get_jobs_handler(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "100"))
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            return web.json_response({"error": "Invalid limit/offset parameter"}, status=400)

        jobs = await self.history_storage.get_jobs(limit=limit, offset=offset)
        return web.json_response(jobs)

    async def _get_dashboard_handler(self, request: web.Request) -> web.Response:
        worker_count = await self.storage.get_active_worker_count()
        queue_length = await self.storage.get_job_queue_length()
        job_summary = await self.history_storage.get_job_summary()

        dashboard_data = {
            "workers": {"total": worker_count},
            "jobs": {"queued": queue_length, **job_summary},
        }
        return web.json_response(dashboard_data)

    async def _task_result_handler(self, request: web.Request) -> web.Response:
        import logging

        try:
            data = await request.json()
            job_id = data.get("job_id")
            task_id = data.get("task_id")
            result = data.get("result", {})
            result_status = result.get("status", "success")
            error_message = result.get("error")
            payload_worker_id = data.get("worker_id")
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        # Security check: Ensure the worker_id from the payload matches the authenticated worker
        authenticated_worker_id = request.get("worker_id")
        if not authenticated_worker_id:
            # This should not happen if the auth middleware is working correctly
            return web.json_response({"error": "Could not identify authenticated worker."}, status=500)

        if payload_worker_id and payload_worker_id != authenticated_worker_id:
            return web.json_response(
                {
                    "error": f"Forbidden: Authenticated worker '{authenticated_worker_id}' "
                    f"cannot submit results for another worker '{payload_worker_id}'.",
                },
                status=403,
            )

        if not job_id or not task_id:
            return web.json_response({"error": "job_id and task_id are required"}, status=400)

        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            return web.json_response({"error": "Job not found"}, status=404)

        # Handle parallel task completion
        if job_state.get("status") == "waiting_for_parallel_tasks":
            await self.storage.remove_job_from_watch(f"{job_id}:{task_id}")
            job_state.setdefault("aggregation_results", {})[task_id] = result
            job_state.setdefault("active_branches", []).remove(task_id)

            if not job_state["active_branches"]:
                logger.info(f"All parallel branches for job {job_id} have completed.")
                job_state["status"] = "running"
                job_state["current_state"] = job_state["aggregation_target"]
                await self.storage.save_job_state(job_id, job_state)
                await self.storage.enqueue_job(job_id)
            else:
                logger.info(
                    f"Branch {task_id} for job {job_id} completed. "
                    f"Waiting for {len(job_state['active_branches'])} more.",
                )
                await self.storage.save_job_state(job_id, job_state)

            return web.json_response({"status": "parallel_branch_result_accepted"}, status=200)

        await self.storage.remove_job_from_watch(job_id)

        import time

        now = time.monotonic()
        dispatched_at = job_state.get("task_dispatched_at", now)
        duration_ms = int((now - dispatched_at) * 1000)

        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": job_state.get("current_state"),
                "event_type": "task_finished",
                "duration_ms": duration_ms,
                "worker_id": authenticated_worker_id,  # Use authenticated worker_id
                "context_snapshot": {**job_state, "result": result},
            },
        )

        job_state["tracing_context"] = {str(k): v for k, v in request.headers.items()}

        if result_status == "failure":
            error_details = result.get("error", {})
            error_type = "TRANSIENT_ERROR"
            error_message = "No error details provided."

            if isinstance(error_details, dict):
                error_type = error_details.get("code", "TRANSIENT_ERROR")
                error_message = error_details.get("message", "No error message provided.")
            elif isinstance(error_details, str):
                # Fallback for old format where `error` was just a string
                error_message = error_details

            logging.warning(f"Task {task_id} for job {job_id} failed with error type '{error_type}'.")

            if error_type == "PERMANENT_ERROR":
                job_state["status"] = "quarantined"
                job_state["error_message"] = f"Task failed with permanent error: {error_message}"
                await self.storage.save_job_state(job_id, job_state)
                await self.storage.quarantine_job(job_id)
            elif error_type == "INVALID_INPUT_ERROR":
                job_state["status"] = "failed"
                job_state["error_message"] = f"Task failed due to invalid input: {error_message}"
                await self.storage.save_job_state(job_id, job_state)
            else:  # TRANSIENT_ERROR or any other/unspecified error
                await self._handle_task_failure(job_state, task_id, error_message)

            return web.json_response({"status": "result_accepted_failure"}, status=200)

        if result_status == "cancelled":
            logging.info(f"Task {task_id} for job {job_id} was cancelled by worker.")
            job_state["status"] = "cancelled"
            await self.storage.save_job_state(job_id, job_state)
            # Optionally, trigger a specific 'cancelled' transition if defined in the blueprint
            transitions = job_state.get("current_task_transitions", {})
            if next_state := transitions.get("cancelled"):
                job_state["current_state"] = next_state
                job_state["status"] = "running"  # It's running the cancellation handler now
                await self.storage.save_job_state(job_id, job_state)
                await self.storage.enqueue_job(job_id)
            return web.json_response({"status": "result_accepted_cancelled"}, status=200)

        transitions = job_state.get("current_task_transitions", {})
        if next_state := transitions.get(result_status):
            logging.info(f"Job {job_id} transitioning based on worker status '{result_status}' to state '{next_state}'")

            worker_data = result.get("data")
            if worker_data and isinstance(worker_data, dict):
                if "state_history" not in job_state:
                    job_state["state_history"] = {}
                job_state["state_history"].update(worker_data)

            job_state["current_state"] = next_state
            job_state["status"] = "running"
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.enqueue_job(job_id)
        else:
            logging.error(f"Job {job_id} failed. Worker returned unhandled status '{result_status}'.")
            job_state["status"] = "failed"
            job_state["error_message"] = f"Worker returned unhandled status: {result_status}"
            await self.storage.save_job_state(job_id, job_state)

        return web.json_response({"status": "result_accepted_success"}, status=200)

    async def _handle_task_failure(self, job_state: dict, task_id: str, error_message: str | None):
        import logging

        job_id = job_state["id"]
        retry_count = job_state.get("retry_count", 0)
        max_retries = self.config.JOB_MAX_RETRIES

        if retry_count < max_retries:
            job_state["retry_count"] = retry_count + 1
            logging.info(f"Retrying task for job {job_id}. Attempt {retry_count + 1}/{max_retries}.")

            task_info = job_state.get("current_task_info")
            if not task_info:
                logging.error(f"Cannot retry job {job_id}: missing 'current_task_info' in job state.")
                job_state["status"] = "failed"
                job_state["error_message"] = "Cannot retry: original task info not found."
                await self.storage.save_job_state(job_id, job_state)
                return

            now = get_running_loop().time()
            timeout_seconds = task_info.get("timeout_seconds", self.config.WORKER_TIMEOUT_SECONDS)
            timeout_at = now + timeout_seconds

            job_state["status"] = "waiting_for_worker"
            job_state["task_dispatched_at"] = now
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.add_job_to_watch(job_id, timeout_at)

            await self.dispatcher.dispatch(job_state, task_info)
        else:
            logging.critical(f"Job {job_id} has failed {max_retries + 1} times. Moving to quarantine.")
            job_state["status"] = "quarantined"
            job_state["error_message"] = f"Task failed after {max_retries + 1} attempts: {error_message}"
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.quarantine_job(job_id)

    async def _human_approval_webhook_handler(self, request: web.Request) -> web.Response:
        job_id = request.match_info.get("job_id")
        if not job_id:
            return web.json_response({"error": "job_id is required in path"}, status=400)
        try:
            data = await request.json()
            decision = data.get("decision")
            if not decision:
                return web.json_response({"error": "decision is required in body"}, status=400)
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)
        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            return web.json_response({"error": "Job not found"}, status=404)
        if job_state.get("status") not in ["waiting_for_worker", "waiting_for_human"]:
            return web.json_response({"error": "Job is not in a state that can be approved"}, status=409)
        transitions = job_state.get("current_task_transitions", {})
        next_state = transitions.get(decision)
        if not next_state:
            return web.json_response({"error": f"Invalid decision '{decision}' for this job"}, status=400)
        job_state["current_state"] = next_state
        job_state["status"] = "running"
        await self.storage.save_job_state(job_id, job_state)
        await self.storage.enqueue_job(job_id)
        return web.json_response({"status": "approval_received", "job_id": job_id})

    async def _get_quarantined_jobs_handler(self, request: web.Request) -> web.Response:
        """Returns a list of all job IDs in the quarantine queue."""
        jobs = await self.storage.get_quarantined_jobs()
        return web.json_response(jobs)

    async def _reload_worker_configs_handler(self, request: web.Request) -> web.Response:
        """Handles the dynamic reloading of worker configurations."""
        logger.info("Received request to reload worker configurations.")
        if not self.config.WORKERS_CONFIG_PATH:
            return web.json_response(
                {"error": "WORKERS_CONFIG_PATH is not set, cannot reload configs."},
                status=400,
            )

        await load_worker_configs_to_redis(self.storage, self.config.WORKERS_CONFIG_PATH)
        return web.json_response({"status": "worker_configs_reloaded"})

    async def _flush_db_handler(self, request: web.Request) -> web.Response:
        logger.warning("Received request to flush the database.")
        await self.storage.flush_all()
        await load_client_configs_to_redis(self.storage)
        return web.json_response({"status": "db_flushed"}, status=200)

    @staticmethod
    async def _docs_handler(request: web.Request) -> web.Response:
        from importlib import resources

        try:
            content = resources.read_text("avtomatika", "api.html")
            return web.Response(text=content, content_type="text/html")
        except FileNotFoundError:
            logger.error("api.html not found within the avtomatika package.")
            return web.json_response({"error": "Documentation file not found on server."}, status=500)

    def _setup_routes(self):
        public_app = web.Application()
        public_app.router.add_get("/status", status_handler)
        public_app.router.add_get("/metrics", metrics_handler)
        public_app.router.add_post("/webhooks/approval/{job_id}", self._human_approval_webhook_handler)
        public_app.router.add_post("/debug/flush_db", self._flush_db_handler)
        public_app.router.add_get("/docs", self._docs_handler)
        public_app.router.add_get("/jobs/quarantined", self._get_quarantined_jobs_handler)
        self.app.add_subapp("/_public/", public_app)

        auth_middleware = client_auth_middleware_factory(self.storage)
        quota_middleware = quota_middleware_factory(self.storage)
        api_middlewares = [auth_middleware, quota_middleware]

        protected_app = web.Application(middlewares=api_middlewares)
        versioned_apps: Dict[str, web.Application] = {}
        has_unversioned_routes = False

        for bp in self.blueprints.values():
            if not bp.api_endpoint:
                continue
            endpoint = bp.api_endpoint if bp.api_endpoint.startswith("/") else f"/{bp.api_endpoint}"
            if bp.api_version:
                if bp.api_version not in versioned_apps:
                    versioned_apps[bp.api_version] = web.Application(middlewares=api_middlewares)
                versioned_apps[bp.api_version].router.add_post(endpoint, self._create_job_handler(bp))
            else:
                protected_app.router.add_post(endpoint, self._create_job_handler(bp))
                has_unversioned_routes = True

        all_protected_apps = list(versioned_apps.values())
        if has_unversioned_routes:
            all_protected_apps.append(protected_app)

        for app in all_protected_apps:
            self._register_common_routes(app)
        if has_unversioned_routes:
            self.app.add_subapp("/api/", protected_app)
        for version, app in versioned_apps.items():
            self.app.add_subapp(f"/api/{version}", app)

        worker_auth_middleware = worker_auth_middleware_factory(self.storage, self.config)
        worker_middlewares = [worker_auth_middleware]
        if self.config.RATE_LIMITING_ENABLED:
            worker_rate_limiter = rate_limit_middleware_factory(storage=self.storage, limit=5, period=60)
            worker_middlewares.append(worker_rate_limiter)

        worker_app = web.Application(middlewares=worker_middlewares)
        worker_app.router.add_post("/workers/register", self._register_worker_handler)
        worker_app.router.add_get("/workers/{worker_id}/tasks/next", self._handle_get_next_task)
        worker_app.router.add_patch("/workers/{worker_id}", self._worker_update_handler)
        worker_app.router.add_post("/tasks/result", self._task_result_handler)
        worker_app.router.add_get("/ws/{worker_id}", self._websocket_handler)
        self.app.add_subapp("/_worker/", worker_app)

    def _register_common_routes(self, app):
        app.router.add_get("/jobs/{job_id}", self._get_job_status_handler)
        app.router.add_post("/jobs/{job_id}/cancel", self._cancel_job_handler)
        if not isinstance(self.history_storage, NoOpHistoryStorage):
            app.router.add_get("/jobs/{job_id}/history", self._get_job_history_handler)
        app.router.add_get("/blueprints/{blueprint_name}/graph", self._get_blueprint_graph_handler)
        app.router.add_get("/workers", self._get_workers_handler)
        app.router.add_get("/jobs", self._get_jobs_handler)
        app.router.add_get("/dashboard", self._get_dashboard_handler)
        app.router.add_post("/admin/reload-workers", self._reload_worker_configs_handler)

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        worker_id = request.match_info.get("worker_id")
        if not worker_id:
            raise web.HTTPBadRequest(text="worker_id is required")

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        await self.ws_manager.register(worker_id, ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = msg.json()
                        await self.ws_manager.handle_message(worker_id, data)
                    except Exception as e:
                        logger.error(f"Error processing WebSocket message from {worker_id}: {e}")
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket connection for {worker_id} closed with exception {ws.exception()}")
                    break
        finally:
            await self.ws_manager.unregister(worker_id)
        return ws

    async def _handle_get_next_task(self, request: web.Request) -> web.Response:
        worker_id = request.match_info.get("worker_id")
        if not worker_id:
            return web.json_response({"error": "worker_id is required in path"}, status=400)

        logger.debug(f"Worker {worker_id} is requesting a new task.")
        task = await self.storage.dequeue_task_for_worker(worker_id, self.config.WORKER_POLL_TIMEOUT_SECONDS)

        if task:
            logger.info(f"Sending task {task.get('task_id')} to worker {worker_id}")
            return web.json_response(task, status=200)
        logger.debug(f"No tasks for worker {worker_id}, responding 204.")
        return web.Response(status=204)

    async def _worker_update_handler(self, request: web.Request) -> web.Response:
        """
        Handles both full updates and lightweight heartbeats for a worker.

        If the request has a JSON body, it updates the worker's data.
        In either case, it refreshes the worker's TTL, serving as a heartbeat.
        """
        worker_id = request.match_info.get("worker_id")
        if not worker_id:
            return web.json_response({"error": "worker_id is required in path"}, status=400)

        ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
        update_data = None

        # Check for body content without consuming it if it's not JSON
        if request.can_read_body:
            try:
                update_data = await request.json()
            except Exception:
                # This can happen if the body is present but not valid JSON.
                # We can treat it as a lightweight heartbeat or return an error.
                # For robustness, let's treat it as a lightweight ping but log a warning.
                logger.warning(
                    f"Received PATCH from worker {worker_id} with non-JSON body. Treating as TTL-only heartbeat."
                )

        if update_data:
            # Full update path
            updated_worker = await self.storage.update_worker_status(worker_id, update_data, ttl)
            if not updated_worker:
                return web.json_response({"error": "Worker not found"}, status=404)

            await self.history_storage.log_worker_event(
                {
                    "worker_id": worker_id,
                    "event_type": "status_update",
                    "worker_info_snapshot": updated_worker,
                },
            )
            return web.json_response(updated_worker, status=200)
        else:
            # Lightweight TTL-only heartbeat path
            refreshed = await self.storage.refresh_worker_ttl(worker_id, ttl)
            if not refreshed:
                return web.json_response({"error": "Worker not found"}, status=404)
            return web.json_response({"status": "ttl_refreshed"})

    async def _register_worker_handler(self, request: web.Request) -> web.Response:
        # The worker_registration_data is attached by the auth middleware
        # to avoid reading the request body twice.
        worker_data = request.get("worker_registration_data")
        if not worker_data:
            return web.json_response({"error": "Worker data not found in request"}, status=500)

        worker_id = worker_data.get("worker_id")
        # This check is redundant if the middleware works, but good for safety
        if not worker_id:
            return web.json_response({"error": "Missing required field: worker_id"}, status=400)

        ttl = self.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
        await self.storage.register_worker(worker_id, worker_data, ttl)

        logger.info(
            f"Worker '{worker_id}' registered with info: {worker_data}",
        )

        await self.history_storage.log_worker_event(
            {
                "worker_id": worker_id,
                "event_type": "registered",
                "worker_info_snapshot": worker_data,
            },
        )
        return web.json_response({"status": "registered"}, status=200)

    def run(self):
        self.setup()
        print(
            f"Starting OrchestratorEngine API server on {self.config.API_HOST}:{self.config.API_PORT} in blocking mode."
        )
        web.run_app(self.app, host=self.config.API_HOST, port=self.config.API_PORT)

    async def start(self):
        """Starts the orchestrator engine non-blockingly."""
        self.setup()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.config.API_HOST, self.config.API_PORT)
        await self.site.start()
        print(f"OrchestratorEngine API server running on http://{self.config.API_HOST}:{self.config.API_PORT}")

    async def stop(self):
        """Stops the orchestrator engine."""
        print("Stopping OrchestratorEngine API server...")
        if hasattr(self, "site"):
            await self.site.stop()
        if hasattr(self, "runner"):
            await self.runner.cleanup()
        print("OrchestratorEngine API server stopped.")

from asyncio import TimeoutError as AsyncTimeoutError
from asyncio import create_task, gather, get_running_loop, wait_for
from logging import getLogger
from typing import Any, Optional
from uuid import uuid4

from aiohttp import ClientSession, web
from orjson import dumps

from . import metrics
from .api.routes import setup_routes
from .app_keys import (
    DISPATCHER_KEY,
    ENGINE_KEY,
    EXECUTOR_KEY,
    EXECUTOR_TASK_KEY,
    HEALTH_CHECKER_KEY,
    HEALTH_CHECKER_TASK_KEY,
    HTTP_SESSION_KEY,
    REPUTATION_CALCULATOR_KEY,
    REPUTATION_CALCULATOR_TASK_KEY,
    S3_SERVICE_KEY,
    SCHEDULER_KEY,
    SCHEDULER_TASK_KEY,
    WATCHER_KEY,
    WATCHER_TASK_KEY,
    WORKER_SERVICE_KEY,
    WS_MANAGER_KEY,
)
from .blueprint import StateMachineBlueprint
from .client_config_loader import load_client_configs_to_redis
from .compression import compression_middleware
from .config import Config
from .constants import JOB_STATUS_FAILED, JOB_STATUS_PENDING, JOB_STATUS_QUARANTINED, JOB_STATUS_WAITING_FOR_WORKER
from .dispatcher import Dispatcher
from .executor import JobExecutor
from .health_checker import HealthChecker
from .history.base import HistoryStorageBase
from .history.noop import NoOpHistoryStorage
from .logging_config import setup_logging
from .reputation import ReputationCalculator
from .s3 import S3Service
from .scheduler import Scheduler
from .services.worker_service import WorkerService
from .storage.base import StorageBackend
from .telemetry import setup_telemetry
from .utils.webhook_sender import WebhookPayload, WebhookSender
from .watcher import Watcher
from .worker_config_loader import load_worker_configs_to_redis
from .ws_manager import WebSocketManager

metrics.init_metrics()

logger = getLogger(__name__)


def json_dumps(obj: Any) -> str:
    return dumps(obj).decode("utf-8")


def json_response(data: Any, **kwargs: Any) -> web.Response:
    return web.json_response(data, dumps=json_dumps, **kwargs)


class OrchestratorEngine:
    def __init__(self, storage: StorageBackend, config: Config):
        setup_logging(config.LOG_LEVEL, config.LOG_FORMAT, config.TZ)
        setup_telemetry()
        self.storage = storage
        self.config = config
        self.blueprints: dict[str, StateMachineBlueprint] = {}
        self.history_storage: HistoryStorageBase = NoOpHistoryStorage()
        self.ws_manager = WebSocketManager(self.storage)
        self.app = web.Application(middlewares=[compression_middleware])
        self.app[ENGINE_KEY] = self
        self.worker_service: Optional[WorkerService] = None
        self._setup_done = False
        self.webhook_sender: WebhookSender
        self.dispatcher: Dispatcher
        self.runner: web.AppRunner
        self.site: web.TCPSite

        from rxon import HttpListener

        self.rxon_listener = HttpListener(self.app)

    def register_blueprint(self, blueprint: StateMachineBlueprint) -> None:
        if self._setup_done:
            raise RuntimeError("Cannot register blueprints after engine setup.")
        if blueprint.name in self.blueprints:
            raise ValueError(
                f"Blueprint with name '{blueprint.name}' is already registered.",
            )
        blueprint.validate()
        self.blueprints[blueprint.name] = blueprint

    def setup(self) -> None:
        if self._setup_done:
            return
        setup_routes(self.app, self)
        self.app.on_startup.append(self.on_startup)
        self.app.on_shutdown.append(self.on_shutdown)
        self._setup_done = True

    async def _setup_history_storage(self) -> None:
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
                storage_args = [db_path, self.config.TZ]
            except ImportError as e:
                logger.error(f"Could not import SQLiteHistoryStorage, perhaps aiosqlite is not installed? Error: {e}")
                self.history_storage = NoOpHistoryStorage()
                return

        elif uri.startswith("postgresql:"):
            try:
                module = import_module(".history.postgres", package="avtomatika")
                storage_class = module.PostgresHistoryStorage
                storage_args = [uri, self.config.TZ]
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

    async def handle_rxon_message(self, message_type: str, payload: Any, context: dict) -> Any:
        """Core handler for RXON protocol messages via any listener."""
        from rxon.security import extract_cert_identity

        from .security import verify_worker_auth

        request = context.get("raw_request")
        token = context.get("token")
        cert_identity = extract_cert_identity(request) if request else None

        worker_id_hint = context.get("worker_id_hint")

        if not worker_id_hint:
            if message_type == "poll" and isinstance(payload, str):
                worker_id_hint = payload
            elif isinstance(payload, dict) and "worker_id" in payload:
                worker_id_hint = payload["worker_id"]
            elif hasattr(payload, "worker_id"):
                worker_id_hint = payload.worker_id

        try:
            auth_worker_id = await verify_worker_auth(self.storage, self.config, token, cert_identity, worker_id_hint)
        except PermissionError as e:
            raise web.HTTPUnauthorized(text=str(e)) from e
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e)) from e

        if self.worker_service is None:
            raise web.HTTPInternalServerError(text="WorkerService is not initialized.")

        if message_type == "register":
            return await self.worker_service.register_worker(payload)

        elif message_type == "poll":
            return await self.worker_service.get_next_task(auth_worker_id)

        elif message_type == "result":
            return await self.worker_service.process_task_result(payload, auth_worker_id)

        elif message_type == "heartbeat":
            return await self.worker_service.update_worker_heartbeat(auth_worker_id, payload)

        elif message_type == "sts_token":
            if cert_identity is None:
                raise web.HTTPForbidden(text="Unauthorized: mTLS certificate required to issue access token.")
            return await self.worker_service.issue_access_token(auth_worker_id)

        elif message_type == "websocket":
            ws = payload
            await self.ws_manager.register(auth_worker_id, ws)
            try:
                from aiohttp import WSMsgType

                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        try:
                            data = msg.json()
                            await self.ws_manager.handle_message(auth_worker_id, data)
                        except Exception as e:
                            logger.error(f"Error processing WebSocket message from {auth_worker_id}: {e}")
                    elif msg.type == WSMsgType.ERROR:
                        break
            finally:
                await self.ws_manager.unregister(auth_worker_id)
            return None

    async def on_startup(self, app: web.Application) -> None:
        # Fail Fast: Check Storage Connection
        if not await self.storage.ping():
            logger.critical("Failed to connect to Storage Backend (Redis). Exiting.")
            raise RuntimeError("Storage Backend is unavailable.")

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
        # Start history background worker
        await self.history_storage.start()

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
        self.webhook_sender = WebhookSender(app[HTTP_SESSION_KEY])
        self.webhook_sender.start()
        self.dispatcher = Dispatcher(self.storage, self.config)
        app[DISPATCHER_KEY] = self.dispatcher
        app[EXECUTOR_KEY] = JobExecutor(self, self.history_storage)
        app[WATCHER_KEY] = Watcher(self)
        app[REPUTATION_CALCULATOR_KEY] = ReputationCalculator(self)
        app[HEALTH_CHECKER_KEY] = HealthChecker(self)
        app[SCHEDULER_KEY] = Scheduler(self)
        app[WS_MANAGER_KEY] = self.ws_manager
        app[S3_SERVICE_KEY] = S3Service(self.config, self.history_storage)

        self.worker_service = WorkerService(self.storage, self.history_storage, self.config, self)
        app[WORKER_SERVICE_KEY] = self.worker_service

        app[EXECUTOR_TASK_KEY] = create_task(app[EXECUTOR_KEY].run())
        app[WATCHER_TASK_KEY] = create_task(app[WATCHER_KEY].run())
        app[REPUTATION_CALCULATOR_TASK_KEY] = create_task(app[REPUTATION_CALCULATOR_KEY].run())
        app[HEALTH_CHECKER_TASK_KEY] = create_task(app[HEALTH_CHECKER_KEY].run())
        app[SCHEDULER_TASK_KEY] = create_task(app[SCHEDULER_KEY].run())

        await self.rxon_listener.start(self.handle_rxon_message)

    async def on_shutdown(self, app: web.Application) -> None:
        logger.info("Shutdown sequence started.")
        await self.rxon_listener.stop()

        app[EXECUTOR_KEY].stop()
        app[WATCHER_KEY].stop()
        app[REPUTATION_CALCULATOR_KEY].stop()
        app[HEALTH_CHECKER_KEY].stop()
        app[SCHEDULER_KEY].stop()
        logger.info("Background task running flags set to False.")

        if hasattr(self.history_storage, "close"):
            logger.info("Closing history storage...")
            await self.history_storage.close()
            logger.info("History storage closed.")

        logger.info("Closing WebSocket connections...")
        await self.ws_manager.close_all()

        logger.info("Stopping WebhookSender...")
        await self.webhook_sender.stop()

        if S3_SERVICE_KEY in app:
            logger.info("Closing S3 Service...")
            await app[S3_SERVICE_KEY].close()

        logger.info("Cancelling background tasks...")
        app[HEALTH_CHECKER_TASK_KEY].cancel()
        app[WATCHER_TASK_KEY].cancel()
        app[REPUTATION_CALCULATOR_TASK_KEY].cancel()
        app[EXECUTOR_TASK_KEY].cancel()
        # Scheduler task manages its own loop cancellation in stop(), but just in case:
        app[SCHEDULER_TASK_KEY].cancel()
        logger.info("Background tasks cancelled.")

        logger.info("Gathering background tasks with a 10s timeout...")
        try:
            await wait_for(
                gather(
                    app[HEALTH_CHECKER_TASK_KEY],
                    app[WATCHER_TASK_KEY],
                    app[REPUTATION_CALCULATOR_TASK_KEY],
                    app[EXECUTOR_TASK_KEY],
                    app[SCHEDULER_TASK_KEY],
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

    async def create_background_job(
        self,
        blueprint_name: str,
        initial_data: dict[str, Any],
        source: str = "internal",
        tracing_context: dict[str, str] | None = None,
        data_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Creates a job directly, bypassing the HTTP API layer.
        Useful for internal schedulers and triggers.
        """
        blueprint = self.blueprints.get(blueprint_name)
        if not blueprint:
            raise ValueError(f"Blueprint '{blueprint_name}' not found.")

        job_id = str(uuid4())
        # Use a special internal client config
        client_config = {
            "token": "internal-scheduler",
            "plan": "system",
            "params": {"source": source},
        }

        job_state = {
            "id": job_id,
            "blueprint_name": blueprint.name,
            "current_state": blueprint.start_state,
            "initial_data": initial_data,
            "state_history": {},
            "status": JOB_STATUS_PENDING,
            "tracing_context": tracing_context or {},
            "client_config": client_config,
            "data_metadata": data_metadata or {},
        }
        await self.storage.save_job_state(job_id, job_state)
        await self.storage.enqueue_job(job_id)
        metrics.jobs_total.inc({metrics.LABEL_BLUEPRINT: blueprint.name})

        # Log the creation in history as well (so we can track scheduled jobs)
        await self.history_storage.log_job_event(
            {
                "job_id": job_id,
                "state": "pending",
                "event_type": "job_created",
                "context_snapshot": job_state,
                "metadata": {"source": source, "scheduled": True},
            }
        )

        logger.info(f"Created background job {job_id} for blueprint '{blueprint_name}' (source: {source})")
        return job_id

    async def handle_task_failure(self, job_state: dict[str, Any], task_id: str, error_message: str | None) -> None:
        """Handles a transient task failure by retrying or quarantining."""
        job_id = job_state["id"]
        retry_count = job_state.get("retry_count", 0)
        max_retries = self.config.JOB_MAX_RETRIES

        if retry_count < max_retries:
            job_state["retry_count"] = retry_count + 1
            logger.info(f"Retrying task for job {job_id}. Attempt {retry_count + 1}/{max_retries}.")

            task_info = job_state.get("current_task_info")
            if not task_info:
                logger.error(f"Cannot retry job {job_id}: missing 'current_task_info' in job state.")
                job_state["status"] = JOB_STATUS_FAILED
                job_state["error_message"] = "Cannot retry: original task info not found."
                await self.storage.save_job_state(job_id, job_state)
                await self.send_job_webhook(job_state, "job_failed")
                return

            now = get_running_loop().time()
            timeout_seconds = task_info.get("timeout_seconds", self.config.WORKER_TIMEOUT_SECONDS)
            timeout_at = now + timeout_seconds

            job_state["status"] = JOB_STATUS_WAITING_FOR_WORKER
            job_state["task_dispatched_at"] = now
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.add_job_to_watch(job_id, timeout_at)

            await self.dispatcher.dispatch(job_state, task_info)
        else:
            logger.critical(f"Job {job_id} has failed {max_retries + 1} times. Moving to quarantine.")
            job_state["status"] = JOB_STATUS_QUARANTINED
            job_state["error_message"] = f"Task failed after {max_retries + 1} attempts: {error_message}"
            await self.storage.save_job_state(job_id, job_state)
            await self.storage.quarantine_job(job_id)
            await self.send_job_webhook(job_state, "job_quarantined")

    async def send_job_webhook(self, job_state: dict[str, Any], event: str) -> None:
        """Sends a webhook notification for a job event."""
        webhook_url = job_state.get("webhook_url")
        if not webhook_url:
            return

        payload = WebhookPayload(
            event=event,
            job_id=job_state["id"],
            status=job_state["status"],
            result=job_state.get("state_history"),  # Or specific result
            error=job_state.get("error_message"),
        )

        # Run in background to not block the main flow
        await self.webhook_sender.send(webhook_url, payload)

    def run(self) -> None:
        self.setup()
        ssl_context = None
        if self.config.TLS_ENABLED:
            from rxon.security import create_server_ssl_context

            ssl_context = create_server_ssl_context(
                cert_path=self.config.TLS_CERT_PATH,
                key_path=self.config.TLS_KEY_PATH,
                ca_path=self.config.TLS_CA_PATH,
                require_client_cert=self.config.TLS_REQUIRE_CLIENT_CERT,
            )
            print(f"TLS enabled. mTLS required: {self.config.TLS_REQUIRE_CLIENT_CERT}")

        print(
            f"Starting OrchestratorEngine API server on {self.config.API_HOST}:{self.config.API_PORT} in blocking mode."
        )
        web.run_app(self.app, host=self.config.API_HOST, port=self.config.API_PORT, ssl_context=ssl_context)

    async def start(self):
        """Starts the orchestrator engine non-blockingly."""
        self.setup()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        ssl_context = None
        if self.config.TLS_ENABLED:
            from rxon.security import create_server_ssl_context

            ssl_context = create_server_ssl_context(
                cert_path=self.config.TLS_CERT_PATH,
                key_path=self.config.TLS_KEY_PATH,
                ca_path=self.config.TLS_CA_PATH,
                require_client_cert=self.config.TLS_REQUIRE_CLIENT_CERT,
            )

        self.site = web.TCPSite(self.runner, self.config.API_HOST, self.config.API_PORT, ssl_context=ssl_context)
        await self.site.start()
        protocol = "https" if self.config.TLS_ENABLED else "http"
        print(f"OrchestratorEngine API server running on {protocol}://{self.config.API_HOST}:{self.config.API_PORT}")

    async def stop(self):
        """Stops the orchestrator engine."""
        print("Stopping OrchestratorEngine API server...")
        if hasattr(self, "site"):
            await self.site.stop()
        if hasattr(self, "runner"):
            await self.runner.cleanup()
        print("OrchestratorEngine API server stopped.")

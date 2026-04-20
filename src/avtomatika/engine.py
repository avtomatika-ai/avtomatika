# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from asyncio import CancelledError, create_task, gather, get_running_loop, sleep, wait_for
from collections.abc import Awaitable, Callable
from importlib import import_module
from logging import getLogger
from os.path import exists
from time import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import orjson
from aiohttp import ClientSession, WSMsgType, web

try:
    from opentelemetry.instrumentation.aiohttp_client import (
        AioHttpClientInstrumentor,
    )
except ImportError:
    AioHttpClientInstrumentor = None

from rxon import HttpListener
from rxon.constants import PROTOCOL_VERSION
from rxon.schema import extract_skill_contract
from rxon.security import create_server_ssl_context
from rxon.utils import to_dict

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
from .blueprint import Blueprint
from .blueprint_loader import load_blueprints_from_dir
from .client_config_loader import load_client_configs_to_redis
from .compression import compression_middleware
from .config import Config
from .constants import (
    COMMAND_CANCEL_TASK,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_QUARANTINED,
    JOB_STATUS_WAITING_FOR_WORKER,
)
from .dispatcher import Dispatcher
from .executor import TERMINAL_STATES, JobExecutor
from .health_checker import HealthChecker
from .history.base import HistoryStorageBase
from .history.noop import NoOpHistoryStorage
from .logging_config import setup_logging, stop_logging
from .ratelimit import rate_limit_middleware_factory
from .reputation import ReputationCalculator
from .s3 import S3Service
from .scheduler import Scheduler
from .security import verify_worker_auth
from .services.worker_service import WorkerService
from .storage.base import StorageBackend
from .telemetry import setup_telemetry
from .utils.schema import orchestrator_extract_json_schema
from .utils.webhook_sender import WebhookPayload, WebhookSender
from .watcher import Watcher
from .worker_config_loader import load_worker_configs_to_redis
from .ws_manager import WebSocketManager

metrics.init_metrics()

logger = getLogger(__name__)


class OrchestratorEngine:
    def __init__(self, storage: StorageBackend, config: Config):
        setup_logging(config.LOG_LEVEL, config.LOG_FORMAT, config.TZ)
        setup_telemetry()
        self.storage = storage
        self.config = config
        self.blueprints: dict[str, Blueprint] = {}
        self.blueprint_contracts: dict[str, dict[str, Any]] = {}
        self.history_storage: HistoryStorageBase = NoOpHistoryStorage()
        self.ws_manager = WebSocketManager(self.storage)

        self.on_worker_event: list[Callable[[str, Any], Awaitable[None]]] = []
        self.on_job_finished: list[Callable[[str, str, dict[str, Any]], Awaitable[None]]] = []

        middlewares = [compression_middleware]
        if config.RATE_LIMITING_ENABLED:
            overrides = {
                "heartbeat": config.RATE_LIMIT_HEARTBEAT_LIMIT,
                "tasks/next": config.RATE_LIMIT_POLL_LIMIT,
            }
            middlewares.append(
                rate_limit_middleware_factory(
                    self.storage,
                    config.RATE_LIMIT_LIMIT,
                    config.RATE_LIMIT_PERIOD,
                    overrides=overrides,
                )
            )

        self.app = web.Application(middlewares=middlewares)
        self.app[ENGINE_KEY] = self
        self.worker_service: WorkerService | None = None
        self._setup_done = False
        self.webhook_sender: WebhookSender
        self.dispatcher: Dispatcher
        self.runner: web.AppRunner
        self.site: web.TCPSite

        self.rxon_listener = HttpListener(self.app)

    def register_blueprint(self, blueprint: Blueprint) -> None:
        if self._setup_done:
            raise RuntimeError("Cannot register blueprints after engine setup.")
        if blueprint.name in self.blueprints:
            raise ValueError(
                f"Blueprint with name '{blueprint.name}' is already registered.",
            )
        blueprint.validate()
        self.blueprints[blueprint.name] = blueprint

        # REST API acts as a Web Shell for the logic
        contract = extract_skill_contract(blueprint, extractor=orchestrator_extract_json_schema)
        self.blueprint_contracts[blueprint.name] = contract

        # Save to shared storage for cluster-wide matching if loop is running
        try:
            get_running_loop()
            create_task(self.storage.save_blueprint_contract(blueprint.name, contract))
        except RuntimeError:
            # Loop not running yet, contracts will be synced in on_startup
            pass

    def setup(self) -> None:
        if self._setup_done:
            return

        if self.config.BLUEPRINTS_DIR:
            load_blueprints_from_dir(self, self.config.BLUEPRINTS_DIR)

        setup_routes(self.app, self)
        self.app.on_startup.append(self.on_startup)
        self.app.on_shutdown.append(self.on_shutdown)
        self._setup_done = True

    async def _setup_history_storage(self) -> None:
        uri = self.config.HISTORY_DATABASE_URI
        storage_class = None
        storage_args = []

        if not uri:
            logger.info("History storage is disabled (HISTORY_DATABASE_URI is not set).")
            self.history_storage = NoOpHistoryStorage()
            return

        elif uri.startswith("sqlite:"):
            try:
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
                storage_args = [str(uri), str(self.config.TZ)]
            except ImportError as e:
                logger.error(f"Could not import PostgresHistoryStorage, perhaps asyncpg is not installed? Error: {e}")
                self.history_storage = NoOpHistoryStorage()
                return
        else:
            logger.warning(f"Unsupported HISTORY_DATABASE_URI scheme: {uri}. Disabling history storage.")
            self.history_storage = NoOpHistoryStorage()
            return

        if storage_class:
            # Explicitly unpacking args to avoid any ambiguity
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
        token = context.get("token")
        cert_identity = context.get("cert_identity")

        worker_id_hint = context.get("worker_id_hint")

        if not worker_id_hint:
            if message_type == "poll" and isinstance(payload, str):
                worker_id_hint = payload
            elif isinstance(payload, dict) and "worker_id" in payload:
                worker_id_hint = payload["worker_id"]
            elif hasattr(payload, "worker_id"):
                worker_id_hint = payload.worker_id

        auth_worker_id = await verify_worker_auth(self.storage, self.config, token, cert_identity, worker_id_hint)

        if self.worker_service is None:
            raise web.HTTPInternalServerError(text="WorkerService is not initialized.")

        if message_type == "register":
            # Protocol Version Check
            worker_version = context.get("protocol_version")
            warning = None
            if worker_version and worker_version != PROTOCOL_VERSION:
                warning = f"Protocol version mismatch! Orchestrator: {PROTOCOL_VERSION}, Worker: {worker_version}."
                logger.warning(f"Worker {worker_id_hint}: {warning}")

            await self.worker_service.register_worker(payload, auth_worker_id)
            return {"status": "registered", "version": PROTOCOL_VERSION, "warning": warning}

        elif message_type == "poll":
            return await self.worker_service.get_next_task(auth_worker_id)

        elif message_type == "result":
            return await self.worker_service.process_task_result(payload, auth_worker_id)

        elif message_type == "heartbeat":
            return await self.worker_service.update_worker_heartbeat(auth_worker_id, payload)

        elif message_type == "event":
            return await self.worker_service.process_worker_event(auth_worker_id, payload)

        elif message_type == "sts_token":
            if cert_identity is None:
                raise web.HTTPForbidden(text="Unauthorized: mTLS certificate required to issue access token.")
            return await self.worker_service.issue_access_token(auth_worker_id)

        elif message_type == "websocket_auth":
            # Just verify that verify_worker_auth succeeded (which it already did above)
            return True

        elif message_type == "websocket":
            ws = payload
            await self.ws_manager.register(auth_worker_id, ws)
            try:
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        try:
                            data = orjson.loads(msg.data)
                            await self.worker_service.process_worker_event(auth_worker_id, data)
                        except Exception as e:
                            logger.error(f"Error processing WebSocket message from {auth_worker_id}: {e}")
                    elif msg.type == WSMsgType.ERROR:
                        break
            finally:
                await self.ws_manager.unregister(auth_worker_id)
            return None

    async def on_startup(self, app: web.Application) -> None:
        if not await self.storage.ping():
            logger.critical("Failed to connect to Storage Backend (Redis). Exiting.")
            raise RuntimeError("Storage Backend is unavailable.")

        await self.storage.initialize()
        await self._setup_history_storage()

        if AioHttpClientInstrumentor:
            try:
                AioHttpClientInstrumentor().instrument()
            except Exception:
                logger.debug("Failed to instrument AIOHTTP client.")

        await self.history_storage.start()

        # Ensure all registered blueprint contracts are in storage
        for bp_name, contract in self.blueprint_contracts.items():
            await self.storage.save_blueprint_contract(bp_name, contract)

        if self.config.CLIENTS_CONFIG_PATH:
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

        if self.config.WORKERS_CONFIG_PATH:
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
        self._loop_lag_task = create_task(self._monitor_loop_lag())

        await self.rxon_listener.start(self.handle_rxon_message)

    async def _monitor_loop_lag(self) -> None:
        """Monitors event loop lag and updates the Prometheus metric."""

        try:
            while True:
                start = time()
                await sleep(1.0)
                lag = time() - start - 1.0
                metrics.loop_lag_seconds.set({}, max(0.0, lag))
        except CancelledError:
            pass

    async def on_shutdown(self, app: web.Application) -> None:
        logger.info("Shutdown sequence started.")
        await self.rxon_listener.stop()

        if hasattr(self, "_loop_lag_task"):
            self._loop_lag_task.cancel()

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
        if getattr(self, "_loop_lag_task", None):
            self._loop_lag_task.cancel()

        for key in [
            HEALTH_CHECKER_TASK_KEY,
            WATCHER_TASK_KEY,
            REPUTATION_CALCULATOR_TASK_KEY,
            EXECUTOR_TASK_KEY,
            SCHEDULER_TASK_KEY,
        ]:
            if task := app.get(key):
                task.cancel()

        logger.info("Background tasks signaled to stop.")

        logger.info("Gathering background tasks with a 10s timeout...")
        tasks_to_wait = [
            app[key]
            for key in [
                HEALTH_CHECKER_TASK_KEY,
                WATCHER_TASK_KEY,
                REPUTATION_CALCULATOR_TASK_KEY,
                EXECUTOR_TASK_KEY,
                SCHEDULER_TASK_KEY,
            ]
            if key in app
        ]
        try:
            if tasks_to_wait:
                await wait_for(
                    gather(*tasks_to_wait, return_exceptions=True),
                    timeout=10.0,
                )
            logger.info("Background tasks gathered successfully.")
        except TimeoutError:
            logger.error("Timed out waiting for background tasks to shut down.")

        logger.info("Closing HTTP session...")
        await app[HTTP_SESSION_KEY].close()
        logger.info("HTTP session closed.")
        logger.info("Shutdown sequence finished.")

        stop_logging()

    async def create_background_job(
        self,
        blueprint_name: str,
        initial_data: dict[str, Any],
        source: str = "internal",
        tracing_context: dict[str, str] | None = None,
        data_metadata: dict[str, Any] | None = None,
        dispatch_timeout: int | None = None,
        result_timeout: int | None = None,
        security: Any | None = None,
        metadata: dict[str, Any] | None = None,
        parent_job_id: str | None = None,
    ) -> str:
        """Creates a job directly, bypassing the HTTP API layer.
        Useful for internal schedulers and triggers.
        """
        blueprint = self.blueprints.get(blueprint_name)
        if not blueprint:
            raise ValueError(f"Blueprint '{blueprint_name}' not found.")

        job_id = str(uuid4())
        client_config = {
            "token": "internal-scheduler",
            "plan": "system",
            "params": {"source": source},
        }

        now = time()

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
            "dispatch_timeout": dispatch_timeout,
            "result_timeout": result_timeout,
            "security": to_dict(security) if security else None,
            "metadata": metadata or {},
            "parent_job_id": parent_job_id,
            "timestamp": now,
        }
        await self.storage.save_job_state(job_id, job_state)

        if dispatch_timeout:
            await self.storage.add_job_to_watch(job_id, now + dispatch_timeout)

        await self.storage.enqueue_job(job_id)
        metrics.jobs_total.inc({metrics.LABEL_BLUEPRINT: blueprint.name})

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

            now_mono = get_running_loop().time()

            # Recalculate timeout for retry, respecting existing deadlines
            dispatch_deadline = job_state.get("dispatch_deadline")
            result_deadline = job_state.get("result_deadline")
            execution_timeout = task_info.get("timeout_seconds") or self.config.WORKER_TIMEOUT_SECONDS

            deadlines = [d for d in (dispatch_deadline, result_deadline) if d is not None]
            timeout_at = min(deadlines) if deadlines else now_mono + execution_timeout

            job_state["status"] = JOB_STATUS_WAITING_FOR_WORKER
            job_state["task_dispatched_at"] = now_mono
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
            security=job_state.get("security"),
            metadata=job_state.get("metadata"),
            timestamp=job_state.get("timestamp"),
        )

        # Run in background to not block the main flow
        await self.webhook_sender.send(webhook_url, payload)

    async def handle_job_timeout(self, job_state: dict[str, Any]) -> None:
        """Handles job/task timeout by either retrying or failing the job."""
        job_id = job_state["id"]
        status = job_state.get("status")

        if status in TERMINAL_STATES:
            return

        now = time()
        picked_up = job_state.get("task_picked_up_at")
        dispatch_deadline = job_state.get("dispatch_deadline")
        result_deadline = job_state.get("result_deadline")

        blueprint_name = job_state.get("blueprint_name", "unknown")
        timeout_type = "dispatch" if picked_up is None else "execution"

        error_message = "Worker task timed out."
        if picked_up is None:
            if dispatch_deadline and now >= dispatch_deadline:
                error_message = "Worker task timed out while waiting in queue (dispatch timeout)."
            elif result_deadline and now >= result_deadline:
                error_message = "Worker task timed out: result deadline reached before pickup."
        else:
            error_message = "Worker task timed out: execution/result deadline reached."

        logger.warning(f"Job {job_id} ({blueprint_name}) timed out ({timeout_type}): {error_message}")

        metrics.jobs_timeouts_total.inc({metrics.LABEL_BLUEPRINT: blueprint_name, "type": timeout_type})

        worker_id = job_state.get("task_worker_id")
        if worker_id:
            await self.history_storage.log_job_event(
                {
                    "job_id": job_id,
                    "state": job_state.get("current_state"),
                    "event_type": "task_finished",
                    "worker_id": worker_id,
                    "context_snapshot": {
                        "status": "failure",
                        "error": "timeout",
                        "message": error_message,
                    },
                }
            )

        if self.config.S3_AUTO_CLEANUP:
            s3_service = self.app.get(S3_SERVICE_KEY)
            if s3_service:
                task_files = s3_service.get_task_files(job_id)
                if task_files:
                    create_task(task_files.cleanup())

        # If it timed out during execution, we give it a chance to be retried on another worker
        if picked_up is not None:
            task_id = str(job_state.get("current_task_id") or job_id)
            await self.handle_task_failure(job_state, task_id, error_message)
        else:
            # If it timed out in queue (PENDING or waiting for worker), it's usually a permanent failure
            # because no worker is available to handle it in time.
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = error_message
            await self.storage.save_job_state(job_id, job_state)
            await self.send_job_webhook(job_state, "job_failed")
            metrics.jobs_failed_total.inc({metrics.LABEL_BLUEPRINT: blueprint_name})

    async def cancel_job(self, job_id: str) -> bool:
        """Cancels a job and its active tasks."""
        job_state = await self.storage.get_job_state(job_id)
        if not job_state:
            return False

        if job_state.get("status") in TERMINAL_STATES:
            return False

        task_id = job_state.get("current_task_id")
        worker_id = job_state.get("task_worker_id")

        if task_id:
            await self.storage.set_task_cancellation_flag(task_id)

        job_state["status"] = JOB_STATUS_CANCELLED
        await self.storage.save_job_state(job_id, job_state)

        if worker_id:
            worker_info = await self.storage.get_worker_info(worker_id)
            if worker_info and worker_info.get("capabilities", {}).get("websockets"):
                command = {"command": COMMAND_CANCEL_TASK, "task_id": task_id, "job_id": job_id}
                await self.ws_manager.send_command(worker_id, command)

        logger.info(f"Job {job_id} has been cancelled.")
        return True

    def run(self) -> None:
        self.setup()
        ssl_context = None
        if self.config.TLS_ENABLED:
            ssl_context = create_server_ssl_context(
                cert_path=self.config.TLS_CERT_PATH,
                key_path=self.config.TLS_KEY_PATH,
                ca_path=self.config.TLS_CA_PATH,
                require_client_cert=self.config.TLS_REQUIRE_CLIENT_CERT,
            )
            logger.info(f"TLS enabled. mTLS required: {self.config.TLS_REQUIRE_CLIENT_CERT}")

        logger.info(
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
            ssl_context = create_server_ssl_context(
                cert_path=self.config.TLS_CERT_PATH,
                key_path=self.config.TLS_KEY_PATH,
                ca_path=self.config.TLS_CA_PATH,
                require_client_cert=self.config.TLS_REQUIRE_CLIENT_CERT,
            )

        self.site = web.TCPSite(self.runner, self.config.API_HOST, self.config.API_PORT, ssl_context=ssl_context)
        await self.site.start()
        protocol = "https" if self.config.TLS_ENABLED else "http"
        logger.info(
            f"OrchestratorEngine API server running on {protocol}://{self.config.API_HOST}:{self.config.API_PORT}"
        )

    async def stop(self):
        """Stops the orchestrator engine."""
        logger.info("Stopping OrchestratorEngine API server...")
        if hasattr(self, "site"):
            await self.site.stop()
        if hasattr(self, "runner"):
            await self.runner.cleanup()
        logger.info("OrchestratorEngine API server stopped.")

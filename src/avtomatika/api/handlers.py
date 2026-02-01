from importlib import resources
from logging import getLogger
from typing import Any, Callable
from uuid import uuid4

from aiohttp import web
from aioprometheus import render
from orjson import OPT_INDENT_2, dumps, loads

from .. import metrics
from ..app_keys import (
    ENGINE_KEY,
)
from ..blueprint import StateMachineBlueprint
from ..client_config_loader import load_client_configs_to_redis
from ..constants import (
    COMMAND_CANCEL_TASK,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_FOR_HUMAN,
    JOB_STATUS_WAITING_FOR_WORKER,
)
from ..worker_config_loader import load_worker_configs_to_redis

logger = getLogger(__name__)


def json_dumps(obj) -> str:
    return dumps(obj).decode("utf-8")


def json_response(data, **kwargs) -> web.Response:
    return web.json_response(data, dumps=json_dumps, **kwargs)


async def status_handler(_request: web.Request) -> web.Response:
    return json_response({"status": "ok"})


async def metrics_handler(_request: web.Request) -> web.Response:
    return web.Response(body=render(), content_type="text/plain")


def create_job_handler_factory(blueprint: StateMachineBlueprint) -> Callable[[web.Request], Any]:
    async def handler(request: web.Request) -> web.Response:
        engine = request.app[ENGINE_KEY]
        try:
            request_body = await request.json(loads=loads)
            initial_data = request_body.get("initial_data", {})
            # Backward compatibility: if initial_data key is missing, assume body is initial_data
            if (
                "initial_data" not in request_body
                and request_body
                and not any(k in request_body for k in ("webhook_url",))
            ):
                initial_data = request_body

            webhook_url = request_body.get("webhook_url")
        except Exception:
            return json_response({"error": "Invalid JSON body"}, status=400)

        client_config = request["client_config"]
        carrier = {str(k): v for k, v in request.headers.items()}

        job_id = str(uuid4())
        job_state = {
            "id": job_id,
            "blueprint_name": blueprint.name,
            "current_state": blueprint.start_state,
            "initial_data": initial_data,
            "state_history": {},
            "status": JOB_STATUS_PENDING,
            "tracing_context": carrier,
            "client_config": client_config,
            "webhook_url": webhook_url,
        }
        await engine.storage.save_job_state(job_id, job_state)
        await engine.storage.enqueue_job(job_id)
        metrics.jobs_total.inc({metrics.LABEL_BLUEPRINT: blueprint.name})
        return json_response({"status": "accepted", "job_id": job_id}, status=202)

    return handler


async def get_job_status_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    job_id = request.match_info.get("job_id")
    if not job_id:
        return json_response({"error": "job_id is required in path"}, status=400)
    job_state = await engine.storage.get_job_state(job_id)
    if not job_state:
        return json_response({"error": "Job not found"}, status=404)
    return json_response(job_state, status=200)


async def cancel_job_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    job_id = request.match_info.get("job_id")
    if not job_id:
        return json_response({"error": "job_id is required in path"}, status=400)

    job_state = await engine.storage.get_job_state(job_id)
    if not job_state:
        return json_response({"error": "Job not found"}, status=404)

    if job_state.get("status") != JOB_STATUS_WAITING_FOR_WORKER:
        return json_response(
            {"error": "Job is not in a state that can be cancelled (must be waiting for a worker)."},
            status=409,
        )

    worker_id = job_state.get("task_worker_id")
    if not worker_id:
        return json_response(
            {"error": "Cannot cancel job: worker_id not found in job state."},
            status=500,
        )

    worker_info = await engine.storage.get_worker_info(worker_id)
    task_id = job_state.get("current_task_id")
    if not task_id:
        return json_response(
            {"error": "Cannot cancel job: task_id not found in job state."},
            status=500,
        )

    # Set Redis flag as a reliable fallback/primary mechanism
    await engine.storage.set_task_cancellation_flag(task_id)

    # Attempt WebSocket-based cancellation if supported
    if worker_info and worker_info.get("capabilities", {}).get("websockets"):
        command = {"command": COMMAND_CANCEL_TASK, "task_id": task_id, "job_id": job_id}
        sent = await engine.ws_manager.send_command(worker_id, command)
        if sent:
            return json_response({"status": "cancellation_request_sent"})
        else:
            logger.warning(f"Failed to send WebSocket cancellation for task {task_id}, but Redis flag is set.")
            # Proceed to return success, as the Redis flag will handle it

    return json_response({"status": "cancellation_request_accepted"})


async def get_job_history_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    job_id = request.match_info.get("job_id")
    if not job_id:
        return json_response({"error": "job_id is required in path"}, status=400)
    history = await engine.history_storage.get_job_history(job_id)
    return json_response(history)


async def get_blueprint_graph_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    blueprint_name = request.match_info.get("blueprint_name")
    if not blueprint_name:
        return json_response({"error": "blueprint_name is required in path"}, status=400)

    blueprint = engine.blueprints.get(blueprint_name)
    if not blueprint:
        return json_response({"error": "Blueprint not found"}, status=404)

    try:
        graph_dot = blueprint.render_graph()
        return web.Response(text=graph_dot, content_type="text/vnd.graphviz")
    except FileNotFoundError:
        error_msg = "Graphviz is not installed on the server. Cannot generate graph."
        logger.error(error_msg)
        return json_response({"error": error_msg}, status=501)


async def get_workers_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    workers = await engine.storage.get_available_workers()
    return json_response(workers)


async def get_jobs_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    try:
        limit = int(request.query.get("limit", "100"))
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        return json_response({"error": "Invalid limit/offset parameter"}, status=400)

    jobs = await engine.history_storage.get_jobs(limit=limit, offset=offset)
    return json_response(jobs)


async def get_dashboard_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    worker_count = await engine.storage.get_active_worker_count()
    queue_length = await engine.storage.get_job_queue_length()
    job_summary = await engine.history_storage.get_job_summary()

    dashboard_data = {
        "workers": {"total": worker_count},
        "jobs": {"queued": queue_length, **job_summary},
    }
    return json_response(dashboard_data)


async def human_approval_webhook_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    job_id = request.match_info.get("job_id")
    if not job_id:
        return json_response({"error": "job_id is required in path"}, status=400)
    try:
        data = await request.json(loads=loads)
        decision = data.get("decision")
        if not decision:
            return json_response({"error": "decision is required in body"}, status=400)
    except Exception:
        return json_response({"error": "Invalid JSON body"}, status=400)
    job_state = await engine.storage.get_job_state(job_id)
    if not job_state:
        return json_response({"error": "Job not found"}, status=404)
    if job_state.get("status") not in [JOB_STATUS_WAITING_FOR_WORKER, JOB_STATUS_WAITING_FOR_HUMAN]:
        return json_response({"error": "Job is not in a state that can be approved"}, status=409)
    transitions = job_state.get("current_task_transitions", {})
    next_state = transitions.get(decision)
    if not next_state:
        return json_response({"error": f"Invalid decision '{decision}' for this job"}, status=400)
    job_state["current_state"] = next_state
    job_state["status"] = JOB_STATUS_RUNNING
    await engine.storage.save_job_state(job_id, job_state)
    await engine.storage.enqueue_job(job_id)
    return json_response({"status": "approval_received", "job_id": job_id})


async def get_quarantined_jobs_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    jobs = await engine.storage.get_quarantined_jobs()
    return json_response(jobs)


async def reload_worker_configs_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    logger.info("Received request to reload worker configurations.")
    if not engine.config.WORKERS_CONFIG_PATH:
        return json_response(
            {"error": "WORKERS_CONFIG_PATH is not set, cannot reload configs."},
            status=400,
        )

    await load_worker_configs_to_redis(engine.storage, engine.config.WORKERS_CONFIG_PATH)
    return json_response({"status": "worker_configs_reloaded"})


async def flush_db_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    logger.warning("Received request to flush the database.")
    await engine.storage.flush_all()
    await load_client_configs_to_redis(engine.storage)
    return json_response({"status": "db_flushed"}, status=200)


async def docs_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    try:
        content = resources.read_text("avtomatika", "api.html")
    except FileNotFoundError:
        logger.error("api.html not found within the avtomatika package.")
        return json_response({"error": "Documentation file not found on server."}, status=500)

    blueprint_endpoints = []
    for bp in engine.blueprints.values():
        if not bp.api_endpoint:
            continue

        version_prefix = f"/{bp.api_version}" if bp.api_version else ""
        endpoint_path = bp.api_endpoint if bp.api_endpoint.startswith("/") else f"/{bp.api_endpoint}"
        full_path = f"/api{version_prefix}{endpoint_path}"

        blueprint_endpoints.append(
            {
                "id": f"post-create-{bp.name.replace('_', '-')}",
                "name": f"Create {bp.name.replace('_', ' ').title()} Job",
                "method": "POST",
                "path": full_path,
                "description": f"Creates and starts a new instance (Job) of the `{bp.name}` blueprint.",
                "request": {"body": {"initial_data": {}}},
                "responses": [
                    {
                        "code": "202 Accepted",
                        "description": "Job successfully accepted for processing.",
                        "body": {"status": "accepted", "job_id": "..."},
                    }
                ],
            }
        )

    if blueprint_endpoints:
        endpoints_json = dumps(blueprint_endpoints, option=OPT_INDENT_2).decode("utf-8")
        marker = "group: 'Protected API',\n                endpoints: ["
        content = content.replace(marker, f"{marker}\n{endpoints_json.strip('[]')},")

    return web.Response(text=content, content_type="text/html")

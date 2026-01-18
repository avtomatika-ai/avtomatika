from importlib import resources
from logging import getLogger
from typing import Any, Callable
from uuid import uuid4

from aiohttp import WSMsgType, web
from aioprometheus import render
from orjson import OPT_INDENT_2, dumps, loads

from .. import metrics
from ..app_keys import (
    ENGINE_KEY,
)
from ..blueprint import StateMachineBlueprint
from ..client_config_loader import load_client_configs_to_redis
from ..constants import (
    ERROR_CODE_INVALID_INPUT,
    ERROR_CODE_PERMANENT,
    ERROR_CODE_TRANSIENT,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_QUARANTINED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_FOR_HUMAN,
    JOB_STATUS_WAITING_FOR_PARALLEL,
    JOB_STATUS_WAITING_FOR_WORKER,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILURE,
    TASK_STATUS_SUCCESS,
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
        command = {"command": "cancel_task", "task_id": task_id, "job_id": job_id}
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


async def task_result_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    try:
        data = await request.json(loads=loads)
        job_id = data.get("job_id")
        task_id = data.get("task_id")
        result = data.get("result", {})
        result_status = result.get("status", TASK_STATUS_SUCCESS)
        error_message = result.get("error")
        payload_worker_id = data.get("worker_id")
    except Exception:
        return json_response({"error": "Invalid JSON body"}, status=400)

    # Security check: Ensure the worker_id from the payload matches the authenticated worker
    authenticated_worker_id = request.get("worker_id")
    if not authenticated_worker_id:
        return json_response({"error": "Could not identify authenticated worker."}, status=500)

    if payload_worker_id and payload_worker_id != authenticated_worker_id:
        return json_response(
            {
                "error": f"Forbidden: Authenticated worker '{authenticated_worker_id}' "
                f"cannot submit results for another worker '{payload_worker_id}'.",
            },
            status=403,
        )

    if not job_id or not task_id:
        return json_response({"error": "job_id and task_id are required"}, status=400)

    job_state = await engine.storage.get_job_state(job_id)
    if not job_state:
        return json_response({"error": "Job not found"}, status=404)

    # Handle parallel task completion
    if job_state.get("status") == JOB_STATUS_WAITING_FOR_PARALLEL:
        await engine.storage.remove_job_from_watch(f"{job_id}:{task_id}")
        job_state.setdefault("aggregation_results", {})[task_id] = result
        job_state.setdefault("active_branches", []).remove(task_id)

        if not job_state["active_branches"]:
            logger.info(f"All parallel branches for job {job_id} have completed.")
            job_state["status"] = JOB_STATUS_RUNNING
            job_state["current_state"] = job_state["aggregation_target"]
            await engine.storage.save_job_state(job_id, job_state)
            await engine.storage.enqueue_job(job_id)
        else:
            logger.info(
                f"Branch {task_id} for job {job_id} completed. Waiting for {len(job_state['active_branches'])} more.",
            )
            await engine.storage.save_job_state(job_id, job_state)

        return json_response({"status": "parallel_branch_result_accepted"}, status=200)

    await engine.storage.remove_job_from_watch(job_id)

    import time

    now = time.monotonic()
    dispatched_at = job_state.get("task_dispatched_at", now)
    duration_ms = int((now - dispatched_at) * 1000)

    await engine.history_storage.log_job_event(
        {
            "job_id": job_id,
            "state": job_state.get("current_state"),
            "event_type": "task_finished",
            "duration_ms": duration_ms,
            "worker_id": authenticated_worker_id,
            "context_snapshot": {**job_state, "result": result},
        },
    )

    job_state["tracing_context"] = {str(k): v for k, v in request.headers.items()}

    if result_status == TASK_STATUS_FAILURE:
        error_details = result.get("error", {})
        error_type = ERROR_CODE_TRANSIENT
        error_message = "No error details provided."

        if isinstance(error_details, dict):
            error_type = error_details.get("code", ERROR_CODE_TRANSIENT)
            error_message = error_details.get("message", "No error message provided.")
        elif isinstance(error_details, str):
            error_message = error_details

        logger.warning(f"Task {task_id} for job {job_id} failed with error type '{error_type}'.")

        if error_type == ERROR_CODE_PERMANENT:
            job_state["status"] = JOB_STATUS_QUARANTINED
            job_state["error_message"] = f"Task failed with permanent error: {error_message}"
            await engine.storage.save_job_state(job_id, job_state)
            await engine.storage.quarantine_job(job_id)
        elif error_type == ERROR_CODE_INVALID_INPUT:
            job_state["status"] = JOB_STATUS_FAILED
            job_state["error_message"] = f"Task failed due to invalid input: {error_message}"
            await engine.storage.save_job_state(job_id, job_state)
        else:  # TRANSIENT_ERROR
            await engine.handle_task_failure(job_state, task_id, error_message)

        return json_response({"status": "result_accepted_failure"}, status=200)

    if result_status == TASK_STATUS_CANCELLED:
        logger.info(f"Task {task_id} for job {job_id} was cancelled by worker.")
        job_state["status"] = JOB_STATUS_CANCELLED
        await engine.storage.save_job_state(job_id, job_state)
        transitions = job_state.get("current_task_transitions", {})
        if next_state := transitions.get("cancelled"):
            job_state["current_state"] = next_state
            job_state["status"] = JOB_STATUS_RUNNING
            await engine.storage.save_job_state(job_id, job_state)
            await engine.storage.enqueue_job(job_id)
        return json_response({"status": "result_accepted_cancelled"}, status=200)

    transitions = job_state.get("current_task_transitions", {})
    if next_state := transitions.get(result_status):
        logger.info(f"Job {job_id} transitioning based on worker status '{result_status}' to state '{next_state}'")

        worker_data = result.get("data")
        if worker_data and isinstance(worker_data, dict):
            if "state_history" not in job_state:
                job_state["state_history"] = {}
            job_state["state_history"].update(worker_data)

        job_state["current_state"] = next_state
        job_state["status"] = JOB_STATUS_RUNNING
        await engine.storage.save_job_state(job_id, job_state)
        await engine.storage.enqueue_job(job_id)
    else:
        logger.error(f"Job {job_id} failed. Worker returned unhandled status '{result_status}'.")
        job_state["status"] = JOB_STATUS_FAILED
        job_state["error_message"] = f"Worker returned unhandled status: {result_status}"
        await engine.storage.save_job_state(job_id, job_state)

    return json_response({"status": "result_accepted_success"}, status=200)


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


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    engine = request.app[ENGINE_KEY]
    worker_id = request.match_info.get("worker_id")
    if not worker_id:
        raise web.HTTPBadRequest(text="worker_id is required")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    await engine.ws_manager.register(worker_id, ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = msg.json()
                    await engine.ws_manager.handle_message(worker_id, data)
                except Exception as e:
                    logger.error(f"Error processing WebSocket message from {worker_id}: {e}")
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket connection for {worker_id} closed with exception {ws.exception()}")
                break
    finally:
        await engine.ws_manager.unregister(worker_id)
    return ws


async def handle_get_next_task(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    worker_id = request.match_info.get("worker_id")
    if not worker_id:
        return json_response({"error": "worker_id is required in path"}, status=400)

    logger.debug(f"Worker {worker_id} is requesting a new task.")
    task = await engine.storage.dequeue_task_for_worker(worker_id, engine.config.WORKER_POLL_TIMEOUT_SECONDS)

    if task:
        logger.info(f"Sending task {task.get('task_id')} to worker {worker_id}")
        return json_response(task, status=200)
    logger.debug(f"No tasks for worker {worker_id}, responding 204.")
    return web.Response(status=204)


async def worker_update_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    worker_id = request.match_info.get("worker_id")
    if not worker_id:
        return json_response({"error": "worker_id is required in path"}, status=400)

    ttl = engine.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
    update_data = None

    if request.can_read_body:
        try:
            update_data = await request.json(loads=loads)
        except Exception:
            logger.warning(
                f"Received PATCH from worker {worker_id} with non-JSON body. Treating as TTL-only heartbeat."
            )

    if update_data:
        updated_worker = await engine.storage.update_worker_status(worker_id, update_data, ttl)
        if not updated_worker:
            return json_response({"error": "Worker not found"}, status=404)

        await engine.history_storage.log_worker_event(
            {
                "worker_id": worker_id,
                "event_type": "status_update",
                "worker_info_snapshot": updated_worker,
            },
        )
        return json_response(updated_worker, status=200)
    else:
        refreshed = await engine.storage.refresh_worker_ttl(worker_id, ttl)
        if not refreshed:
            return json_response({"error": "Worker not found"}, status=404)
        return json_response({"status": "ttl_refreshed"})


async def register_worker_handler(request: web.Request) -> web.Response:
    engine = request.app[ENGINE_KEY]
    worker_data = request.get("worker_registration_data")
    if not worker_data:
        return json_response({"error": "Worker data not found in request"}, status=500)

    worker_id = worker_data.get("worker_id")
    if not worker_id:
        return json_response({"error": "Missing required field: worker_id"}, status=400)

    ttl = engine.config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS * 2
    await engine.storage.register_worker(worker_id, worker_data, ttl)

    logger.info(
        f"Worker '{worker_id}' registered with info: {worker_data}",
    )

    await engine.history_storage.log_worker_event(
        {
            "worker_id": worker_id,
            "event_type": "registered",
            "worker_info_snapshot": worker_data,
        },
    )
    return json_response({"status": "registered"}, status=200)

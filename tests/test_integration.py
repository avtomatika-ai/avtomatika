import asyncio
import hashlib
import json
import os
from unittest.mock import AsyncMock

import pytest
import zstandard
from aiohttp import web

from avtomatika.blueprint import StateMachineBlueprint
from avtomatika.client_config_loader import load_client_configs_to_redis
from avtomatika.constants import AUTH_HEADER_CLIENT, AUTH_HEADER_WORKER
from avtomatika.engine import ENGINE_KEY, OrchestratorEngine
from avtomatika.storage.redis import RedisStorage
from tests.conftest import STORAGE_KEY

# --- Test Blueprints ---

child_bp = StateMachineBlueprint(name="child_flow")


@child_bp.handler_for("start", is_start=True)
async def child_start(context, actions):
    await asyncio.sleep(0.1)
    actions.transition_to("finished")


@child_bp.handler_for("finished", is_end=True)
async def child_finished(context, actions):
    pass


parent_bp = StateMachineBlueprint(name="parent_flow", api_endpoint="/jobs/parent_flow", api_version="v1")


@parent_bp.handler_for("start", is_start=True)
async def parent_start(context, actions):
    actions.transition_to("running_child")


@parent_bp.handler_for("running_child")
async def run_child(context, actions):
    actions.run_blueprint(
        blueprint_name="child_flow",
        initial_data={"from_parent": "hello"},
        transitions={"success": "child_finished", "failure": "child_failed"},
    )


@parent_bp.handler_for("child_finished")
async def parent_final_step(context, actions):
    actions.transition_to("finished")


@parent_bp.handler_for("finished", is_end=True)
async def parent_finished(context, actions):
    pass


@parent_bp.handler_for("child_failed", is_end=True)
async def parent_failed(context, actions):
    pass


unversioned_bp = StateMachineBlueprint(name="unversioned_flow", api_endpoint="/jobs/unversioned_flow")


@unversioned_bp.handler_for("start", is_start=True)
async def unversioned_start(context, actions):
    actions.transition_to("finished")


@unversioned_bp.handler_for("finished", is_end=True)
async def unversioned_finished(context, actions):
    pass


data_store_bp = StateMachineBlueprint(name="data_store_test", api_endpoint="/jobs/data_store_test", api_version="v1")
data_store_bp.add_data_store("my_store", {"initial": "value"})


@data_store_bp.handler_for("start", is_start=True)
async def ds_start(context, actions):
    initial_value = await context.data_stores.my_store.get("initial")
    await context.data_stores.my_store.set("new_key", f"value_from_{initial_value}")
    actions.transition_to("verify")


@data_store_bp.handler_for("verify")
async def ds_verify(context, actions):
    new_value = await context.data_stores.my_store.get("new_key")
    if new_value == "value_from_value":
        actions.transition_to("finished")
    else:
        actions.transition_to("failed")


@data_store_bp.handler_for("finished", is_end=True)
async def ds_finished(context, actions):
    pass


@data_store_bp.handler_for("failed", is_end=True)
async def ds_failed(context, actions):
    pass


# --- Tests ---


@pytest.mark.parametrize(
    "app",
    [{"extra_blueprints": [child_bp, parent_bp]}],
    indirect=True,
)
@pytest.mark.asyncio
async def test_sub_blueprint_flow(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    headers = {AUTH_HEADER_CLIENT: "user_token_vip"}
    await storage.initialize_client_quota("user_token_vip", 5)
    resp = await client.post("/api/v1/jobs/parent_flow", json={}, headers=headers)
    assert resp.status == 202
    parent_job_id = (await resp.json())["job_id"]

    final_parent_state = None
    child_job_id = None
    for _ in range(20):
        await asyncio.sleep(0.1)
        parent_state = await storage.get_job_state(parent_job_id)
        assert parent_state is not None
        if not child_job_id:
            child_job_id = parent_state.get("child_job_id")
        if parent_state.get("current_state") == "finished":
            final_parent_state = parent_state
            break

    assert final_parent_state is not None, "Parent job did not finish in time"
    assert final_parent_state["current_state"] == "finished"
    assert child_job_id is not None, "Child job was never created"
    final_child_state = await storage.get_job_state(child_job_id)
    assert final_child_state is not None
    assert final_child_state["current_state"] == "finished"
    assert final_child_state["parent_job_id"] == parent_job_id


@pytest.mark.parametrize("app", [{"extra_blueprints": [data_store_bp]}], indirect=True)
@pytest.mark.asyncio
async def test_data_store_flow(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    headers = {AUTH_HEADER_CLIENT: "user_token_vip"}
    await storage.initialize_client_quota("user_token_vip", 5)
    resp = await client.post("/api/v1/jobs/data_store_test", json={}, headers=headers)
    assert resp.status == 202
    job_id = (await resp.json())["job_id"]

    final_state = None
    for _ in range(10):
        await asyncio.sleep(0.1)
        state = await storage.get_job_state(job_id)
        assert state is not None
        if state.get("current_state") == "finished":
            final_state = state
            break

    assert final_state is not None, "Job did not finish in time"
    data_store_instance = data_store_bp.data_stores["my_store"]
    final_value = await data_store_instance.get("new_key")
    assert final_value == "value_from_value"


@pytest.mark.asyncio
async def test_zstd_compression_middleware(aiohttp_client, app):
    large_payload = {"data": "a" * 1000}

    async def large_response_handler(request):
        return web.json_response(large_payload)

    app.router.add_get("/_test/large_response", large_response_handler)

    client = await aiohttp_client(app, auto_decompress=False)

    headers = {"Accept-Encoding": "zstd"}
    async with client.get("/_test/large_response", headers=headers) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Encoding") == "zstd"

        compressed_data = await resp.read()
        decompressed_data = zstandard.decompress(compressed_data)
        payload = json.loads(decompressed_data)

        assert payload == large_payload


@pytest.mark.asyncio
async def test_gzip_compression_middleware(aiohttp_client, app):
    large_payload = {"data": "a" * 1000}

    async def large_response_handler(request):
        return web.Response(
            text=json.dumps(large_payload),
            content_type="application/json",
        )

    app.router.add_get("/_test/large_response_gzip", large_response_handler)

    client = await aiohttp_client(app)

    headers = {"Accept-Encoding": "gzip"}
    resp = await client.get("/_test/large_response_gzip", headers=headers)

    assert resp.status == 200
    assert resp.headers.get("Content-Encoding") == "gzip"

    decompressed_data = await resp.read()
    payload = json.loads(decompressed_data)

    assert payload == large_payload


@pytest.mark.parametrize(
    "app",
    [{"extra_blueprints": [parent_bp]}],
    indirect=True,
)
@pytest.mark.asyncio
async def test_quota_middleware(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    token = "user_token_regular"
    headers = {AUTH_HEADER_CLIENT: token}
    await storage.initialize_client_quota(token, 2)

    resp = await client.post("/api/v1/jobs/parent_flow", json={}, headers=headers)
    assert resp.status == 202
    resp = await client.post("/api/v1/jobs/parent_flow", json={}, headers=headers)
    assert resp.status == 202

    resp = await client.post("/api/v1/jobs/parent_flow", json={}, headers=headers)
    assert resp.status == 429


context_bp = StateMachineBlueprint("context_test_bp", api_endpoint="/jobs/context_test", api_version="v1")


@context_bp.handler_for("start", is_start=True)
async def context_start_handler(context, actions):
    client_plan = context.client.plan
    client_lang = context.client.params.get("languages", ["default"])[0]
    context.state_history["test_output"] = f"{client_plan}:{client_lang}"
    actions.transition_to("finished")


@context_bp.handler_for("finished", is_end=True)
async def context_finished_handler(context, actions):
    pass


@pytest.mark.parametrize(
    "app",
    [{"extra_blueprints": [context_bp]}],
    indirect=True,
)
@pytest.mark.asyncio
async def test_client_config_in_context(aiohttp_client, app):
    storage = app[STORAGE_KEY]

    client = await aiohttp_client(app)

    token = "user_token_vip"
    headers = {AUTH_HEADER_CLIENT: token}
    await storage.initialize_client_quota(token, 5)

    resp = await client.post("/api/v1/jobs/context_test", json={}, headers=headers)
    assert resp.status == 202
    job_id = (await resp.json())["job_id"]

    final_state = None
    for _ in range(10):
        await asyncio.sleep(0.1)
        state = await storage.get_job_state(job_id)
        if state and state.get("current_state") == "finished":
            final_state = state
            break

    assert final_state is not None
    assert final_state["state_history"]["test_output"] == "premium:en"


@pytest.mark.asyncio
async def test_worker_registration_with_full_data(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    await storage.flush_all()

    worker_payload = {
        "worker_id": "video-worker-gpu-01",
        "worker_type": "gpu_worker",
        "supported_skills": [
            "ai_text_from_idea",
            "ai_images_from_script",
            "video_montage",
        ],
        "resources": {
            "max_concurrent_tasks": 1,
            "gpu_info": {"model": "NVIDIA T4", "vram_gb": 16},
            "cpu_cores": 8,
        },
        "installed_software": {"ffmpeg": "5.1", "cuda": "11.8"},
        "installed_models": [
            {"name": "stable-diffusion-1.5", "version": "1.0"},
            {"name": "whisper-large-v3", "version": "3.0"},
        ],
        "multi_orchestrator_info": {
            "mode": "FAILOVER",
            "orchestrators": ["http://localhost:8080"],
        },
    }

    headers = {AUTH_HEADER_WORKER: app[ENGINE_KEY].config.GLOBAL_WORKER_TOKEN}
    resp = await client.post(
        "/_worker/workers/register",
        json=worker_payload,
        headers=headers,
    )
    assert resp.status == 200
    resp_json = await resp.json()
    assert resp_json["status"] == "registered"

    workers = await storage.get_available_workers()
    assert len(workers) == 1
    stored_data = workers[0]

    assert stored_data["worker_id"] == worker_payload["worker_id"]
    assert stored_data["supported_skills"] == worker_payload["supported_skills"]
    assert stored_data["resources"]["gpu_info"]["model"] == worker_payload["resources"]["gpu_info"]["model"]
    assert len(stored_data["installed_models"]) == 2
    assert stored_data["multi_orchestrator_info"]["mode"] == "FAILOVER"


@pytest.mark.asyncio
async def test_empty_heartbeat_refreshes_ttl(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    await storage.flush_all()
    worker_id = "worker-for-ttl-test"

    worker_payload = {
        "worker_id": worker_id,
        "worker_type": "test",
        "supported_skills": ["test"],
    }
    headers = {AUTH_HEADER_WORKER: app[ENGINE_KEY].config.GLOBAL_WORKER_TOKEN}
    resp = await client.post(
        "/_worker/workers/register",
        json=worker_payload,
        headers=headers,
    )
    assert resp.status == 200

    workers = await storage.get_available_workers()
    assert len(workers) == 1
    assert workers[0]["worker_id"] == worker_id

    # Send a lightweight heartbeat with an empty body
    resp = await client.patch(f"/_worker/workers/{worker_id}", headers=headers)
    assert resp.status == 200
    assert (await resp.json())["status"] == "ttl_refreshed"

    workers_after_hb = await storage.get_available_workers()
    assert len(workers_after_hb) == 1


@pytest.mark.parametrize("app", [{"extra_blueprints": [unversioned_bp]}], indirect=True)
@pytest.mark.asyncio
async def test_unversioned_route(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    headers = {AUTH_HEADER_CLIENT: "user_token_vip"}
    await storage.initialize_client_quota("user_token_vip", 5)

    resp = await client.post("/api/jobs/unversioned_flow", json={}, headers=headers)
    assert resp.status == 202
    job_id = (await resp.json())["job_id"]

    final_state = None
    for _ in range(10):
        await asyncio.sleep(0.1)
        status_resp = await client.get(f"/api/jobs/{job_id}", headers=headers)
        assert status_resp.status == 200
        state = await status_resp.json()
        if state and state.get("current_state") == "finished":
            final_state = state
            break

    assert final_state is not None, "Unversioned job did not finish"
    assert final_state["current_state"] == "finished"


cancellation_bp = StateMachineBlueprint("cancellation_bp", api_endpoint="/jobs/cancel_me", api_version="v1")


@cancellation_bp.handler_for("start", is_start=True)
async def cancel_start(context, actions):
    actions.dispatch_task(
        task_type="long_running_task",
        transitions={"success": "finished", "cancelled": "cancelled"},
    )


@cancellation_bp.handler_for("finished", is_end=True)
async def cancel_finished(context, actions):
    pass


@cancellation_bp.handler_for("cancelled", is_end=True)
async def cancel_cancelled(context, actions):
    pass


@pytest.mark.parametrize(
    "app",
    [{"extra_blueprints": [cancellation_bp]}],
    indirect=True,
)
@pytest.mark.asyncio
async def test_task_cancellation_via_websocket_mocked(aiohttp_client, app):
    engine = app[ENGINE_KEY]

    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    engine: OrchestratorEngine = app[ENGINE_KEY]

    ws_manager_spy = AsyncMock(spec=engine.ws_manager)
    ws_manager_spy.send_command = AsyncMock(return_value=True)
    engine.ws_manager = ws_manager_spy

    await storage.flush_all()

    # After flushing, we need to reload the client configs for auth to work
    current_dir = os.path.dirname(os.path.abspath(__file__))
    clients_toml_path = os.path.join(current_dir, "clients.toml")
    await load_client_configs_to_redis(storage, config_path=clients_toml_path)

    job_id = "job-to-be-cancelled"
    worker_id = "worker-that-supports-websockets"
    task_id = "task-to-be-cancelled"

    await storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "status": "waiting_for_worker",
            "task_worker_id": worker_id,
            "current_task_id": task_id,
        },
    )

    await storage.register_worker(
        worker_id,
        {
            "worker_id": worker_id,
            "worker_type": "ws_worker",
            "supported_skills": ["any_task"],
            "capabilities": {"websockets": True},
        },
        ttl=60,
    )

    headers = {AUTH_HEADER_CLIENT: "user_token_vip"}
    await storage.initialize_client_quota("user_token_vip", 5)

    cancel_resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert cancel_resp.status == 200
    assert (await cancel_resp.json())["status"] == "cancellation_request_accepted"

    ws_manager_spy.send_command.assert_called_once()
    call_args, _ = ws_manager_spy.send_command.call_args
    assert call_args[0] == worker_id
    assert call_args[1] == {
        "command": "cancel_task",
        "task_id": task_id,
        "job_id": job_id,
    }


@pytest.mark.parametrize(
    "app",
    [
        {
            "extra_blueprints": [cancellation_bp],
            "workers_config_path": os.path.join(os.path.dirname(os.path.abspath(__file__)), "workers.toml"),
        }
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_worker_individual_token_auth(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    await storage.flush_all()

    worker_id = "worker-with-individual-token"
    individual_token = "individual-secret-for-worker-1"
    hashed_individual_token = hashlib.sha256(individual_token.encode()).hexdigest()
    await storage.set_worker_token(worker_id, hashed_individual_token)

    headers = {AUTH_HEADER_WORKER: individual_token}
    payload = {"worker_id": worker_id, "worker_type": "test", "supported_skills": ["test"]}

    resp = await client.post("/_worker/workers/register", json=payload, headers=headers)

    assert resp.status == 200
    assert (await resp.json())["status"] == "registered"


@pytest.mark.asyncio
async def test_worker_individual_token_auth_failure(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    await storage.flush_all()

    worker_id = "worker-with-individual-token"
    correct_token = "individual-secret-for-worker-1"
    wrong_token = "this-is-not-the-right-token"
    await storage.set_worker_token(worker_id, correct_token)

    headers = {AUTH_HEADER_WORKER: wrong_token}
    payload = {"worker_id": worker_id, "worker_type": "test", "supported_skills": ["test"]}

    resp = await client.post("/_worker/workers/register", json=payload, headers=headers)

    assert resp.status == 401
    error = await resp.json()
    assert "Invalid individual worker token" in error["error"]


@pytest.mark.asyncio
async def test_worker_global_token_fallback(aiohttp_client, app):
    global_token = "this-is-a-global-token"
    app[ENGINE_KEY].config.GLOBAL_WORKER_TOKEN = global_token

    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    await storage.flush_all()

    worker_id = "worker-using-global-token"

    headers = {AUTH_HEADER_WORKER: global_token}
    payload = {"worker_id": worker_id, "worker_type": "test", "supported_skills": ["test"]}

    resp = await client.post("/_worker/workers/register", json=payload, headers=headers)

    assert resp.status == 200
    assert (await resp.json())["status"] == "registered"


@pytest.mark.asyncio
async def test_worker_no_token_failure(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage: RedisStorage = app[STORAGE_KEY]
    await storage.flush_all()

    worker_id = "worker-with-no-token"
    payload = {"worker_id": worker_id, "worker_type": "test", "supported_skills": ["test"]}

    resp = await client.post("/_worker/workers/register", json=payload)  # No headers

    assert resp.status == 401
    error = await resp.json()
    assert f"Missing {AUTH_HEADER_WORKER} header" in error["error"]


@pytest.mark.asyncio
async def test_progress_update_handling(aiohttp_client, app):
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    worker_id = "progress-worker"
    job_id = "job-456"
    await storage.save_job_state(job_id, {"id": job_id, "status": "running", "blueprint_name": "test_bp"})
    async with client.ws_connect(
        f"/_worker/ws/{worker_id}", headers={AUTH_HEADER_WORKER: app[ENGINE_KEY].config.GLOBAL_WORKER_TOKEN}
    ) as ws:
        progress_payload = {
            "type": "event",
            "event": "progress_update",
            "task_id": "task-123",
            "job_id": job_id,
            "progress": 0.75,
            "message": "Almost there!",
        }
        await ws.send_json(progress_payload)

        await asyncio.sleep(0.1)

    updated_state = await storage.get_job_state(job_id)
    assert updated_state is not None
    assert updated_state["progress"] == 0.75
    assert updated_state["progress_message"] == "Almost there!"

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from avtomatika.api.handlers import (
    cancel_job_handler,
    docs_handler,
    get_blueprint_graph_handler,
    get_jobs_handler,
    human_approval_webhook_handler,
    register_worker_handler,
    task_result_handler,
    websocket_handler,
    worker_update_handler,
)
from avtomatika.app_keys import ENGINE_KEY
from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def engine(storage, config):
    engine = OrchestratorEngine(storage, config)
    # Mock components that are usually set up in on_startup
    engine.ws_manager = AsyncMock()
    engine.ws_manager.register = AsyncMock()
    engine.ws_manager.unregister = AsyncMock()
    engine.ws_manager.handle_message = AsyncMock()
    engine.ws_manager.send_command = AsyncMock()

    # Mock WebhookSender
    engine.webhook_sender = AsyncMock()
    engine.webhook_sender.send = AsyncMock()
    engine.webhook_sender.start = MagicMock()  # start is synchronous in some contexts or async? Check class.
    # In class it is def start(self) -> None (sync), but creates task.
    # Let's check engine.py usage. engine.py calls it as sync: self.webhook_sender.start()

    return engine


@pytest.fixture
def request_mock(engine):
    req = MagicMock()
    req.app = {ENGINE_KEY: engine}
    req.headers = {}
    return req


@pytest.mark.asyncio
async def test_cancel_job_not_found(engine, request_mock):
    request_mock.match_info.get.return_value = "non-existent-job"
    response = await cancel_job_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_cancel_job_wrong_state(engine, request_mock):
    job_id = "job-in-wrong-state"
    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})
    request_mock.match_info.get.return_value = job_id
    response = await cancel_job_handler(request_mock)
    assert response.status == 409


@pytest.mark.asyncio
async def test_cancel_job_no_worker_id(engine, request_mock):
    job_id = "job-no-worker-id"
    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "waiting_for_worker"})
    request_mock.match_info.get.return_value = job_id
    response = await cancel_job_handler(request_mock)
    assert response.status == 500


@pytest.mark.asyncio
async def test_cancel_job_no_task_id(engine, request_mock):
    job_id = "job-no-task-id"
    await engine.storage.save_job_state(
        job_id, {"id": job_id, "status": "waiting_for_worker", "task_worker_id": "worker-1"}
    )
    request_mock.match_info.get.return_value = job_id
    response = await cancel_job_handler(request_mock)
    assert response.status == 500


@pytest.mark.asyncio
async def test_cancel_job_ws_fails(engine, request_mock, caplog):
    job_id = "job-ws-fails"
    worker_id = "worker-1"
    task_id = "task-1"
    await engine.storage.save_job_state(
        job_id, {"id": job_id, "status": "waiting_for_worker", "task_worker_id": worker_id, "current_task_id": task_id}
    )
    await engine.storage.register_worker(worker_id, {"worker_id": worker_id, "capabilities": {"websockets": True}}, 60)

    # Setup WS manager mock
    engine.ws_manager.send_command.return_value = False

    request_mock.match_info.get.return_value = job_id
    response = await cancel_job_handler(request_mock)

    assert response.status == 200
    assert "Failed to send WebSocket cancellation" in caplog.text
    engine.ws_manager.send_command.assert_called_once_with(
        worker_id, {"command": "cancel_task", "task_id": task_id, "job_id": job_id}
    )


@pytest.mark.asyncio
async def test_get_blueprint_graph_not_found(engine, request_mock):
    request_mock.match_info.get.return_value = "non-existent-blueprint"
    response = await get_blueprint_graph_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_get_blueprint_graph_file_not_found(engine, request_mock):
    bp = MagicMock()
    bp.name = "test_bp"
    bp.render_graph.side_effect = FileNotFoundError
    engine.register_blueprint(bp)
    request_mock.match_info.get.return_value = "test_bp"
    response = await get_blueprint_graph_handler(request_mock)
    assert response.status == 501


@pytest.mark.asyncio
async def test_task_result_job_not_found(engine, request_mock):
    async def get_json(*args, **kwargs):
        return {"job_id": "non-existent-job", "task_id": "task-1"}

    request_mock.json = get_json

    # Mocking get method on MagicMock object to simulate dictionary access or method call
    def mock_get(key, default=None):
        if key == "worker_id":
            return "worker-1"
        return default

    request_mock.get.side_effect = mock_get

    response = await task_result_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_task_result_permanent_failure(engine, request_mock):
    job_id = "job-permanent-failure"
    task_id = "task-1"
    worker_id = "worker-1"

    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

    async def get_json(*args, **kwargs):
        return {
            "job_id": job_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "result": {"status": "failure", "error": {"code": "PERMANENT_ERROR", "message": "test error"}},
        }

    request_mock.json = get_json

    def mock_get(key, default=None):
        if key == "worker_id":
            return worker_id
        return default

    request_mock.get.side_effect = mock_get

    response = await task_result_handler(request_mock)
    assert response.status == 200

    job_state = await engine.storage.get_job_state(job_id)
    assert job_state["status"] == "quarantined"
    assert job_state["error_message"] == "Task failed with permanent error: test error"


@pytest.mark.asyncio
async def test_task_result_invalid_input_failure(engine, request_mock):
    job_id = "job-invalid-input-failure"
    task_id = "task-1"
    worker_id = "worker-1"

    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

    async def get_json(*args, **kwargs):
        return {
            "job_id": job_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "result": {"status": "failure", "error": {"code": "INVALID_INPUT_ERROR", "message": "test error"}},
        }

    request_mock.json = get_json

    def mock_get(key, default=None):
        if key == "worker_id":
            return worker_id
        return default

    request_mock.get.side_effect = mock_get

    response = await task_result_handler(request_mock)
    assert response.status == 200

    job_state = await engine.storage.get_job_state(job_id)
    assert job_state["status"] == "failed"
    assert job_state["error_message"] == "Task failed due to invalid input: test error"


@pytest.mark.asyncio
async def test_task_result_cancelled(engine, request_mock):
    job_id = "job-cancelled"
    task_id = "task-1"
    worker_id = "worker-1"

    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

    async def get_json(*args, **kwargs):
        return {
            "job_id": job_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "result": {"status": "cancelled"},
        }

    request_mock.json = get_json

    def mock_get(key, default=None):
        if key == "worker_id":
            return worker_id
        return default

    request_mock.get.side_effect = mock_get

    response = await task_result_handler(request_mock)
    assert response.status == 200

    job_state = await engine.storage.get_job_state(job_id)
    assert job_state["status"] == "cancelled"


@pytest.mark.asyncio
async def test_task_result_unhandled_status(engine, request_mock):
    job_id = "job-unhandled-status"
    task_id = "task-1"
    worker_id = "worker-1"

    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

    async def get_json(*args, **kwargs):
        return {
            "job_id": job_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "result": {"status": "unhandled"},
        }

    request_mock.json = get_json

    def mock_get(key, default=None):
        if key == "worker_id":
            return worker_id
        return default

    request_mock.get.side_effect = mock_get

    response = await task_result_handler(request_mock)
    assert response.status == 200

    job_state = await engine.storage.get_job_state(job_id)
    assert job_state["status"] == "failed"
    assert job_state["error_message"] == "Worker returned unhandled status: unhandled"


@pytest.mark.asyncio
async def test_human_approval_job_not_found(engine, request_mock):
    request_mock.match_info.get.return_value = "non-existent-job"

    async def get_json(*args, **kwargs):
        return {"decision": "approved"}

    request_mock.json = get_json

    async def mock_get_job_state(job_id):
        return None

    # Patch storage directly on the engine instance used by fixture
    engine.storage.get_job_state = mock_get_job_state

    response = await human_approval_webhook_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_human_approval_wrong_state(engine, request_mock):
    job_id = "job-in-wrong-state"

    async def mock_get_job_state(job_id):
        return {"id": job_id, "status": "running"}

    engine.storage.get_job_state = mock_get_job_state
    request_mock.match_info.get.return_value = job_id

    async def get_json(*args, **kwargs):
        return {"decision": "approved"}

    request_mock.json = get_json

    response = await human_approval_webhook_handler(request_mock)
    assert response.status == 409


@pytest.mark.asyncio
async def test_human_approval_invalid_decision(engine, request_mock):
    job_id = "job-invalid-decision"

    async def mock_get_job_state(job_id):
        return {
            "id": job_id,
            "status": "waiting_for_human",
            "current_task_transitions": {"approved": "next_state"},
        }

    engine.storage.get_job_state = mock_get_job_state
    request_mock.match_info.get.return_value = job_id

    async def get_json(*args, **kwargs):
        return {"decision": "rejected"}

    request_mock.json = get_json

    response = await human_approval_webhook_handler(request_mock)
    assert response.status == 400


@pytest.mark.asyncio
async def test_websocket_handler_no_worker_id(engine, request_mock):
    request_mock.match_info.get.return_value = None
    with pytest.raises(web.HTTPBadRequest):
        await websocket_handler(request_mock)


@pytest.mark.asyncio
async def test_websocket_handler_invalid_json(engine, request_mock, caplog):
    worker_id = "worker-1"
    request_mock.match_info.get.return_value = worker_id

    ws = web.WebSocketResponse()
    mock_prepare = AsyncMock()
    mock_receive = AsyncMock(
        side_effect=[
            MagicMock(type=web.WSMsgType.TEXT, json=MagicMock(side_effect=ValueError("Invalid JSON"))),
            StopAsyncIteration,
        ]
    )

    with (
        patch("aiohttp.web.WebSocketResponse", return_value=ws),
        patch.object(ws, "prepare", mock_prepare),
        patch.object(ws, "receive", mock_receive),
    ):
        await websocket_handler(request_mock)
        assert f"Error processing WebSocket message from {worker_id}" in caplog.text
        mock_prepare.assert_called_once()
        mock_receive.assert_called()


@pytest.mark.asyncio
async def test_register_worker_no_data(engine, request_mock):
    request_mock.get.return_value = None
    response = await register_worker_handler(request_mock)
    assert response.status == 500


@pytest.mark.asyncio
async def test_register_worker_no_worker_id(engine, request_mock):
    request_mock.get.return_value = {"worker_type": "test"}
    response = await register_worker_handler(request_mock)
    assert response.status == 400


@pytest.mark.asyncio
async def test_worker_update_not_found(engine, request_mock):
    request_mock.match_info.get.return_value = "non-existent-worker"
    payload = {"status": "idle"}

    async def get_json(*args, **kwargs):
        return payload

    request_mock.json = get_json
    request_mock.can_read_body = True
    engine.storage.update_worker_status = AsyncMock(return_value=None)

    response = await worker_update_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_worker_update_handler_empty_body(engine, request_mock):
    """Tests that a PATCH request with an empty body only refreshes TTL."""
    request_mock.match_info.get.return_value = "worker-1"
    request_mock.can_read_body = False  # Simulate empty body

    engine.storage.refresh_worker_ttl = AsyncMock(return_value=True)
    engine.storage.update_worker_status = AsyncMock()

    response = await worker_update_handler(request_mock)

    assert response.status == 200
    assert json.loads(response.body.decode()) == {"status": "ttl_refreshed"}
    engine.storage.refresh_worker_ttl.assert_called_once()
    engine.storage.update_worker_status.assert_not_called()


@pytest.mark.asyncio
async def test_get_jobs_invalid_params(engine, request_mock):
    request_mock.query = {"limit": "abc", "offset": "def"}
    response = await get_jobs_handler(request_mock)
    assert response.status == 400


@pytest.mark.asyncio
async def test_docs_handler_injection(engine, request_mock):
    from avtomatika.blueprint import StateMachineBlueprint

    bp = StateMachineBlueprint(name="test_bp", api_endpoint="/jobs/test", api_version="v1")

    @bp.handler_for("start", is_start=True)
    async def start(context, actions):
        pass

    engine.register_blueprint(bp)

    response = await docs_handler(request_mock)
    assert response.status == 200
    text = response.text
    assert "Create Test Bp Job" in text
    assert "/api/v1/jobs/test" in text


@pytest.mark.asyncio
async def test_docs_handler_not_found(engine, request_mock):
    with patch("importlib.resources.read_text", side_effect=FileNotFoundError):
        response = await docs_handler(request_mock)
        assert response.status == 500

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from rxon.constants import COMMAND_CANCEL_TASK

from avtomatika.api.handlers import (
    cancel_job_handler,
    docs_handler,
    get_blueprint_graph_handler,
    get_jobs_handler,
    human_approval_webhook_handler,
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
    engine.webhook_sender.start = MagicMock()

    return engine


@pytest.fixture
def request_mock(engine):
    req = MagicMock(spec=web.Request)

    # app needs to be accessible via []
    app_mock = MagicMock()
    app_dict = {
        ENGINE_KEY: engine,
    }
    app_mock.__getitem__.side_effect = app_dict.__getitem__
    app_mock.get.side_effect = app_dict.get
    req.app = app_mock

    req.headers = {}
    req.match_info = MagicMock()
    req.query = {}
    req.can_read_body = True
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
    assert response.status == 200


@pytest.mark.asyncio
async def test_cancel_job_no_worker_id(engine, request_mock):
    job_id = "job-no-worker-id"
    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "waiting_for_worker"})
    request_mock.match_info.get.return_value = job_id
    response = await cancel_job_handler(request_mock)
    assert response.status == 200


@pytest.mark.asyncio
async def test_cancel_job_no_task_id(engine, request_mock):
    job_id = "job-no-task-id"
    await engine.storage.save_job_state(
        job_id, {"id": job_id, "status": "waiting_for_worker", "task_worker_id": "worker-1"}
    )
    request_mock.match_info.get.return_value = job_id
    response = await cancel_job_handler(request_mock)
    assert response.status == 200


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
    engine.ws_manager.send_command.assert_called_once_with(
        worker_id, {"command": COMMAND_CANCEL_TASK, "task_id": task_id, "job_id": job_id}
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

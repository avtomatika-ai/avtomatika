import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from src.avtomatika.config import Config
from src.avtomatika.engine import (
    DISPATCHER_KEY,
    ENGINE_KEY,
    EXECUTOR_KEY,
    EXECUTOR_TASK_KEY,
    HEALTH_CHECKER_KEY,
    HEALTH_CHECKER_TASK_KEY,
    HTTP_SESSION_KEY,
    REPUTATION_CALCULATOR_KEY,
    REPUTATION_CALCULATOR_TASK_KEY,
    WATCHER_KEY,
    WATCHER_TASK_KEY,
    OrchestratorEngine,
)
from src.avtomatika.history.noop import NoOpHistoryStorage
from src.avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def engine(storage, config):
    return OrchestratorEngine(storage, config)


@pytest.mark.asyncio
async def test_engine_initialization(engine):
    assert engine.storage is not None
    assert engine.config is not None
    assert engine.blueprints == {}
    assert not engine._setup_done


def test_register_blueprint(engine):
    bp = MagicMock()
    bp.name = "test_bp"
    engine.register_blueprint(bp)
    assert "test_bp" in engine.blueprints
    bp.validate.assert_called_once()


def test_register_blueprint_after_setup_raises_error(engine):
    engine.setup()
    with pytest.raises(RuntimeError):
        bp = MagicMock()
        bp.name = "test_bp"
        engine.register_blueprint(bp)


def test_register_duplicate_blueprint_raises_error(engine):
    bp1 = MagicMock()
    bp1.name = "test_bp"
    engine.register_blueprint(bp1)
    bp2 = MagicMock()
    bp2.name = "test_bp"
    with pytest.raises(ValueError):
        engine.register_blueprint(bp2)


def test_setup(engine):
    with patch.object(engine, "_setup_routes") as mock_setup_routes:
        engine.setup()
        assert engine._setup_done
        mock_setup_routes.assert_called_once()
        assert len(engine.app.on_startup) == 2
        assert len(engine.app.on_shutdown) == 1


def test_setup_multiple_calls(engine):
    with patch.object(engine, "_setup_routes") as mock_setup_routes:
        engine.setup()
        engine.setup()
        mock_setup_routes.assert_called_once()


@pytest.mark.asyncio
async def test_on_startup(engine, monkeypatch):
    # Set the config path to ensure the conditional logic is triggered
    monkeypatch.setattr(engine.config, "WORKERS_CONFIG_PATH", "/fake/path.toml")
    monkeypatch.setattr(engine.config, "CLIENTS_CONFIG_PATH", "/fake/path.toml")

    app = web.Application()
    app[ENGINE_KEY] = engine
    engine.app = app
    loop = asyncio.get_running_loop()

    load_clients_called = False

    async def mock_load_clients(*args, **kwargs):
        nonlocal load_clients_called
        load_clients_called = True

    load_workers_called = False

    async def mock_load_workers(*args, **kwargs):
        nonlocal load_workers_called
        load_workers_called = True

    with (
        patch("src.avtomatika.engine.ClientSession"),
        patch("src.avtomatika.engine.Dispatcher"),
        patch("src.avtomatika.engine.JobExecutor", autospec=True),
        patch("src.avtomatika.engine.Watcher", autospec=True),
        patch("src.avtomatika.engine.ReputationCalculator", autospec=True),
        patch("src.avtomatika.engine.HealthChecker", autospec=True),
        patch("src.avtomatika.engine.load_client_configs_to_redis", mock_load_clients),
        patch("src.avtomatika.engine.load_worker_configs_to_redis", mock_load_workers),
        patch("os.path.exists", return_value=True),  # Mock that the config file exists
        patch.object(loop, "create_task") as mock_create_task,
    ):
        await engine.on_startup(app)
        assert load_clients_called
        assert load_workers_called
        assert HTTP_SESSION_KEY in app
        assert DISPATCHER_KEY in app
        assert EXECUTOR_KEY in app
        assert WATCHER_KEY in app
        assert REPUTATION_CALCULATOR_KEY in app
        assert HEALTH_CHECKER_KEY in app
        assert mock_create_task.call_count == 4


@pytest.mark.asyncio
async def test_on_shutdown(engine):
    app = web.Application()
    app[EXECUTOR_KEY] = MagicMock()
    app[WATCHER_KEY] = MagicMock()
    app[REPUTATION_CALCULATOR_KEY] = MagicMock()
    app[HEALTH_CHECKER_KEY] = MagicMock()

    app[HTTP_SESSION_KEY] = MagicMock(close=AsyncMock())

    # Create real Future objects for the tasks
    loop = asyncio.get_event_loop()
    app[HEALTH_CHECKER_TASK_KEY] = loop.create_future()
    app[WATCHER_TASK_KEY] = loop.create_future()
    app[REPUTATION_CALCULATOR_TASK_KEY] = loop.create_future()
    app[EXECUTOR_TASK_KEY] = loop.create_future()

    engine.history_storage = MagicMock(close=AsyncMock())

    engine.ws_manager = MagicMock(close_all=AsyncMock())

    async def mock_gather(*args, **kwargs):
        return []

    with patch("asyncio.gather", mock_gather):
        await engine.on_shutdown(app)

    app[EXECUTOR_KEY].stop.assert_called_once()
    app[WATCHER_KEY].stop.assert_called_once()
    app[REPUTATION_CALCULATOR_KEY].stop.assert_called_once()
    app[HEALTH_CHECKER_KEY].stop.assert_called_once()
    engine.history_storage.close.assert_called_once()
    app[HTTP_SESSION_KEY].close.assert_called_once()
    engine.ws_manager.close_all.assert_called_once()

    # We need to check the cancel method on the future, not the mock
    assert app[HEALTH_CHECKER_TASK_KEY].cancelled()
    assert app[WATCHER_TASK_KEY].cancelled()
    assert app[REPUTATION_CALCULATOR_TASK_KEY].cancelled()
    assert app[EXECUTOR_TASK_KEY].cancelled()


@pytest.mark.asyncio
async def test_setup_history_storage_noop_by_default(engine):
    await engine._setup_history_storage()
    assert isinstance(engine.history_storage, NoOpHistoryStorage)


@pytest.mark.asyncio
async def test_setup_history_storage_sqlite(engine, monkeypatch):
    monkeypatch.setattr(engine.config, "HISTORY_DATABASE_URI", "sqlite:/:memory:")
    with patch("importlib.import_module") as mock_import:
        mock_storage_class = MagicMock()
        mock_initialize = AsyncMock()
        mock_storage_class.return_value.initialize = mock_initialize
        mock_import.return_value = MagicMock(SQLiteHistoryStorage=mock_storage_class)

        await engine._setup_history_storage()

        mock_import.assert_called_once_with(".history.sqlite", package="avtomatika")
        mock_initialize.assert_called_once()


@pytest.mark.asyncio
async def test_setup_history_storage_postgres(engine, monkeypatch):
    monkeypatch.setattr(engine.config, "HISTORY_DATABASE_URI", "postgresql://user:pass@host:port/db")
    with patch("importlib.import_module") as mock_import:
        mock_storage_class = MagicMock()
        mock_initialize = AsyncMock()
        mock_storage_class.return_value.initialize = mock_initialize
        mock_import.return_value = MagicMock(PostgresHistoryStorage=mock_storage_class)

        await engine._setup_history_storage()

        mock_import.assert_called_once_with(".history.postgres", package="avtomatika")
        mock_storage_class.assert_called_once_with("postgresql://user:pass@host:port/db")
        assert engine.history_storage is mock_storage_class.return_value
        mock_initialize.assert_called_once()


@pytest.mark.asyncio
async def test_setup_history_storage_unsupported_scheme(engine, monkeypatch):
    monkeypatch.setattr(engine.config, "HISTORY_DATABASE_URI", "mysql://user:pass@host:port/db")
    await engine._setup_history_storage()
    assert isinstance(engine.history_storage, NoOpHistoryStorage)


@pytest.mark.asyncio
async def test_setup_history_storage_initialization_failure(engine, monkeypatch):
    monkeypatch.setattr(engine.config, "HISTORY_DATABASE_URI", "sqlite:/:memory:")
    with patch("importlib.import_module") as mock_import:
        mock_storage_class = MagicMock()
        mock_storage_class.__name__ = "MockStorage"
        mock_storage_class.return_value.initialize = AsyncMock(side_effect=Exception("Boom!"))
        mock_import.return_value = MagicMock(SQLiteHistoryStorage=mock_storage_class)

        await engine._setup_history_storage()

        assert isinstance(engine.history_storage, NoOpHistoryStorage)

        mock_storage_class.return_value.initialize.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_job_not_found(engine):
    request = MagicMock()
    request.match_info.get.return_value = "non-existent-job"
    response = await engine._cancel_job_handler(request)
    assert response.status == 404


@pytest.mark.asyncio
async def test_cancel_job_wrong_state(engine):
    job_id = "job-in-wrong-state"
    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})
    request = MagicMock()
    request.match_info.get.return_value = job_id
    response = await engine._cancel_job_handler(request)
    assert response.status == 409


@pytest.mark.asyncio
async def test_cancel_job_no_worker_id(engine):
    job_id = "job-no-worker-id"
    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "waiting_for_worker"})
    request = MagicMock()
    request.match_info.get.return_value = job_id
    response = await engine._cancel_job_handler(request)
    assert response.status == 500


@pytest.mark.asyncio
async def test_cancel_job_no_task_id(engine):
    job_id = "job-no-task-id"
    await engine.storage.save_job_state(
        job_id, {"id": job_id, "status": "waiting_for_worker", "task_worker_id": "worker-1"}
    )
    request = MagicMock()
    request.match_info.get.return_value = job_id
    response = await engine._cancel_job_handler(request)
    assert response.status == 500


@pytest.mark.asyncio
async def test_cancel_job_ws_fails(engine, caplog):
    job_id = "job-ws-fails"
    worker_id = "worker-1"
    task_id = "task-1"
    await engine.storage.save_job_state(
        job_id, {"id": job_id, "status": "waiting_for_worker", "task_worker_id": worker_id, "current_task_id": task_id}
    )
    await engine.storage.register_worker(worker_id, {"worker_id": worker_id, "capabilities": {"websockets": True}}, 60)
    engine.ws_manager.send_command = AsyncMock(return_value=False)
    request = MagicMock()
    request.match_info.get.return_value = job_id
    response = await engine._cancel_job_handler(request)
    assert response.status == 200
    assert "Failed to send WebSocket cancellation" in caplog.text
    engine.ws_manager.send_command.assert_called_once_with(
        worker_id, {"command": "cancel_task", "task_id": task_id, "job_id": job_id}
    )


@pytest.mark.asyncio
async def test_get_blueprint_graph_not_found(engine):
    request = MagicMock()
    request.match_info.get.return_value = "non-existent-blueprint"
    response = await engine._get_blueprint_graph_handler(request)
    assert response.status == 404


@pytest.mark.asyncio
async def test_get_blueprint_graph_file_not_found(engine):
    bp = MagicMock()
    bp.name = "test_bp"
    bp.render_graph.side_effect = FileNotFoundError
    engine.register_blueprint(bp)
    request = MagicMock()
    request.match_info.get.return_value = "test_bp"
    response = await engine._get_blueprint_graph_handler(request)
    assert response.status == 501

    @pytest.mark.asyncio
    async def test_task_result_job_not_found(engine):
        request = MagicMock()

        async def get_json(*args, **kwargs):
            return {"job_id": "non-existent-job", "task_id": "task-1"}

        request.json = get_json

        response = await engine._task_result_handler(request)

        assert response.status == 404

    @pytest.mark.asyncio
    async def test_task_result_permanent_failure(engine):
        job_id = "job-permanent-failure"

        task_id = "task-1"

        await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

        request = MagicMock()

        async def get_json(*args, **kwargs):
            return {
                "job_id": job_id,
                "task_id": task_id,
                "result": {"status": "failure", "error": {"code": "PERMANENT_ERROR", "message": "test error"}},
            }

        request.json = get_json

        request.headers = {}

        response = await engine._task_result_handler(request)

        assert response.status == 200

        job_state = await engine.storage.get_job_state(job_id)

        assert job_state["status"] == "quarantined"

        assert job_state["error_message"] == "Task failed with permanent error: test error"

    @pytest.mark.asyncio
    async def test_task_result_invalid_input_failure(engine):
        job_id = "job-invalid-input-failure"

        task_id = "task-1"

        await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

        request = MagicMock()

        async def get_json(*args, **kwargs):
            return {
                "job_id": job_id,
                "task_id": task_id,
                "result": {"status": "failure", "error": {"code": "INVALID_INPUT_ERROR", "message": "test error"}},
            }

        request.json = get_json

        request.headers = {}

        response = await engine._task_result_handler(request)

        assert response.status == 200

        job_state = await engine.storage.get_job_state(job_id)

        assert job_state["status"] == "failed"

        assert job_state["error_message"] == "Task failed due to invalid input: test error"

    @pytest.mark.asyncio
    async def test_task_result_cancelled(engine):
        job_id = "job-cancelled"

        task_id = "task-1"

        await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

        request = MagicMock()

        async def get_json(*args, **kwargs):
            return {
                "job_id": job_id,
                "task_id": task_id,
                "result": {"status": "cancelled"},
            }

        request.json = get_json

        request.headers = {}

        response = await engine._task_result_handler(request)

        assert response.status == 200

        job_state = await engine.storage.get_job_state(job_id)

        assert job_state["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_task_result_unhandled_status(engine):
        job_id = "job-unhandled-status"

        task_id = "task-1"

        await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running"})

        request = MagicMock()

        async def get_json(*args, **kwargs):
            return {
                "job_id": job_id,
                "task_id": task_id,
                "result": {"status": "unhandled"},
            }

        request.json = get_json

        request.headers = {}

        response = await engine._task_result_handler(request)

        assert response.status == 200

        job_state = await engine.storage.get_job_state(job_id)

        assert job_state["status"] == "failed"

        assert job_state["error_message"] == "Worker returned unhandled status: unhandled"


@pytest.mark.asyncio
async def test_handle_task_failure_no_task_info(engine):
    job_id = "job-no-task-info"
    job_state = {"id": job_id, "retry_count": 0}
    await engine._handle_task_failure(job_state, "task-1", "test error")
    assert job_state["status"] == "failed"
    assert job_state["error_message"] == "Cannot retry: original task info not found."


@pytest.mark.asyncio
async def test_handle_task_failure_max_retries(engine):
    job_id = "job-max-retries"
    job_state = {"id": job_id, "retry_count": 3, "current_task_info": {}}
    engine.config.JOB_MAX_RETRIES = 3
    await engine._handle_task_failure(job_state, "task-1", "test error")
    assert job_state["status"] == "quarantined"
    assert job_state["error_message"] == "Task failed after 4 attempts: test error"


@pytest.mark.asyncio
async def test_human_approval_job_not_found(engine):
    request = MagicMock()
    request.match_info.get.return_value = "non-existent-job"

    async def get_json(*args, **kwargs):
        return {"decision": "approved"}

    request.json = get_json

    async def mock_get_job_state(job_id):
        return None

    engine.storage.get_job_state = mock_get_job_state
    response = await engine._human_approval_webhook_handler(request)
    assert response.status == 404


@pytest.mark.asyncio
async def test_human_approval_wrong_state(engine):
    job_id = "job-in-wrong-state"

    async def mock_get_job_state(job_id):
        return {"id": job_id, "status": "running"}

    engine.storage.get_job_state = mock_get_job_state
    request = MagicMock()
    request.match_info.get.return_value = job_id

    async def get_json(*args, **kwargs):
        return {"decision": "approved"}

    request.json = get_json

    response = await engine._human_approval_webhook_handler(request)
    assert response.status == 409


@pytest.mark.asyncio
async def test_human_approval_invalid_decision(engine):
    job_id = "job-invalid-decision"

    async def mock_get_job_state(job_id):
        return {
            "id": job_id,
            "status": "waiting_for_human",
            "current_task_transitions": {"approved": "next_state"},
        }

    engine.storage.get_job_state = mock_get_job_state
    request = MagicMock()
    request.match_info.get.return_value = job_id

    async def get_json(*args, **kwargs):
        return {"decision": "rejected"}

    request.json = get_json

    response = await engine._human_approval_webhook_handler(request)
    assert response.status == 400


@pytest.mark.asyncio
async def test_websocket_handler_no_worker_id(engine):
    request = MagicMock()
    request.match_info.get.return_value = None
    with pytest.raises(web.HTTPBadRequest):
        await engine._websocket_handler(request)


@pytest.mark.asyncio
async def test_websocket_handler_invalid_json(engine, caplog):
    worker_id = "worker-1"
    request = MagicMock()
    request.match_info.get.return_value = worker_id

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
        await engine._websocket_handler(request)
        assert f"Error processing WebSocket message from {worker_id}" in caplog.text
        mock_prepare.assert_called_once()
        mock_receive.assert_called()


@pytest.mark.asyncio
async def test_register_worker_no_data(engine):
    request = MagicMock()
    request.get.return_value = None
    response = await engine._register_worker_handler(request)
    assert response.status == 500


@pytest.mark.asyncio
async def test_register_worker_no_worker_id(engine):
    request = MagicMock()
    request.get.return_value = {"worker_type": "test"}
    response = await engine._register_worker_handler(request)
    assert response.status == 400


@pytest.mark.asyncio
async def test_worker_update_not_found(engine):
    request = MagicMock()
    request.match_info.get.return_value = "non-existent-worker"
    payload = {"status": "idle"}

    async def get_json(*args, **kwargs):
        return payload

    request.json = get_json
    engine.storage.update_worker_status = AsyncMock(return_value=None)
    response = await engine._worker_update_handler(request)
    assert response.status == 404


@pytest.mark.asyncio
async def test_worker_update_handler_empty_body(engine):
    """Tests that a PATCH request with an empty body only refreshes TTL."""
    import json

    request = MagicMock()
    request.match_info.get.return_value = "worker-1"
    request.can_read_body = False  # Simulate empty body

    engine.storage.refresh_worker_ttl = AsyncMock(return_value=True)
    engine.storage.update_worker_status = AsyncMock()

    response = await engine._worker_update_handler(request)

    assert response.status == 200
    # Use json.loads to compare objects, ignoring whitespace differences
    assert json.loads(response.body.decode()) == {"status": "ttl_refreshed"}
    engine.storage.refresh_worker_ttl.assert_called_once()
    engine.storage.update_worker_status.assert_not_called()


@pytest.mark.asyncio
async def test_get_jobs_invalid_params(engine):
    request = MagicMock()
    request.query = {"limit": "abc", "offset": "def"}
    response = await engine._get_jobs_handler(request)
    assert response.status == 400


@pytest.mark.asyncio
async def test_docs_handler_injection(engine):
    from src.avtomatika.blueprint import StateMachineBlueprint

    bp = StateMachineBlueprint(name="test_bp", api_endpoint="/jobs/test", api_version="v1")

    @bp.handler_for("start", is_start=True)
    async def start(context, actions):
        pass

    engine.register_blueprint(bp)

    # Mock request
    request = MagicMock()

    response = await engine._docs_handler(request)
    assert response.status == 200
    text = response.text
    assert "Create Test Bp Job" in text
    assert "/api/v1/jobs/test" in text


@pytest.mark.asyncio
async def test_docs_handler_not_found(engine):
    with patch("importlib.resources.read_text", side_effect=FileNotFoundError):
        response = await engine._docs_handler(MagicMock())
        assert response.status == 500


def test_run(engine):
    with patch("src.avtomatika.engine.web.run_app") as mock_run_app:
        engine.run()
        mock_run_app.assert_called_once_with(engine.app, host=engine.config.API_HOST, port=engine.config.API_PORT)


@pytest.mark.asyncio
async def test_on_startup_import_error(engine, caplog):
    app = web.Application()
    app[ENGINE_KEY] = engine
    engine.app = app
    with patch("opentelemetry.instrumentation.aiohttp_client.AioHttpClientInstrumentor", side_effect=ImportError):
        await engine.on_startup(app)
        assert "opentelemetry-instrumentation-aiohttp-client not found" in caplog.text

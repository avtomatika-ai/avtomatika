import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from avtomatika.config import Config
from avtomatika.engine import (
    DISPATCHER_KEY,
    ENGINE_KEY,
    EXECUTOR_KEY,
    EXECUTOR_TASK_KEY,
    HEALTH_CHECKER_KEY,
    HEALTH_CHECKER_TASK_KEY,
    HTTP_SESSION_KEY,
    REPUTATION_CALCULATOR_KEY,
    REPUTATION_CALCULATOR_TASK_KEY,
    SCHEDULER_KEY,
    SCHEDULER_TASK_KEY,
    WATCHER_KEY,
    WATCHER_TASK_KEY,
    OrchestratorEngine,
)
from avtomatika.history.noop import NoOpHistoryStorage
from avtomatika.storage.memory import MemoryStorage


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
    with patch("avtomatika.engine.setup_routes") as mock_setup_routes:
        engine.setup()
        assert engine._setup_done
        mock_setup_routes.assert_called_once_with(engine.app, engine)
        assert len(engine.app.on_startup) == 2
        assert len(engine.app.on_shutdown) == 1


def test_setup_multiple_calls(engine):
    with patch("avtomatika.engine.setup_routes") as mock_setup_routes:
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
        patch("avtomatika.engine.ClientSession"),
        patch("avtomatika.engine.Dispatcher"),
        patch("avtomatika.engine.JobExecutor", autospec=True),
        patch("avtomatika.engine.Watcher", autospec=True),
        patch("avtomatika.engine.ReputationCalculator", autospec=True),
        patch("avtomatika.engine.HealthChecker", autospec=True),
        patch("avtomatika.engine.Scheduler", autospec=True),
        patch("avtomatika.engine.load_client_configs_to_redis", mock_load_clients),
        patch("avtomatika.engine.load_worker_configs_to_redis", mock_load_workers),
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
        assert SCHEDULER_KEY in app
        assert mock_create_task.call_count == 5


@pytest.mark.asyncio
async def test_on_shutdown(engine):
    app = web.Application()
    app[EXECUTOR_KEY] = MagicMock()
    app[WATCHER_KEY] = MagicMock()
    app[REPUTATION_CALCULATOR_KEY] = MagicMock()
    app[HEALTH_CHECKER_KEY] = MagicMock()
    app[SCHEDULER_KEY] = MagicMock()

    app[HTTP_SESSION_KEY] = MagicMock(close=AsyncMock())

    # Create real Future objects for the tasks
    loop = asyncio.get_event_loop()
    app[HEALTH_CHECKER_TASK_KEY] = loop.create_future()
    app[WATCHER_TASK_KEY] = loop.create_future()
    app[REPUTATION_CALCULATOR_TASK_KEY] = loop.create_future()
    app[EXECUTOR_TASK_KEY] = loop.create_future()
    app[SCHEDULER_TASK_KEY] = loop.create_future()

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
        mock_storage_class.assert_called_once_with("postgresql://user:pass@host:port/db", "UTC")
        mock_initialize.assert_awaited_once()


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


def test_run(engine):
    with patch("avtomatika.engine.web.run_app") as mock_run_app:
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

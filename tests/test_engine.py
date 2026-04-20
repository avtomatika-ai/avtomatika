# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

import avtomatika.engine as engine_mod
from avtomatika.app_keys import (
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
)
from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.history.noop import NoOpHistoryStorage


def create_task_side_effect(coro, *args, **kwargs):
    coro.close()
    return MagicMock(spec=asyncio.Task)


@pytest.fixture
def config():
    c = Config()
    c.HISTORY_DATABASE_URI = ""
    return c


@pytest.fixture
def storage():
    return AsyncMock()


@pytest.fixture
def engine(storage, config):
    return OrchestratorEngine(storage, config)


@pytest.mark.asyncio
async def test_register_blueprint(engine):
    mock_bp = MagicMock()
    mock_bp.name = "test_bp"
    engine.register_blueprint(mock_bp)
    assert engine.blueprints["test_bp"] == mock_bp


@pytest.mark.asyncio
async def test_setup(engine):
    engine.storage = AsyncMock()
    engine.setup()
    assert engine._setup_done
    assert engine.worker_service is None


@pytest.mark.asyncio
async def test_on_startup(engine, monkeypatch):
    engine.config.WORKERS_CONFIG_PATH = "/fake/path.toml"
    engine.config.CLIENTS_CONFIG_PATH = "/fake/path.toml"

    app = web.Application()
    app[ENGINE_KEY] = engine
    engine.app = app
    loop = asyncio.get_running_loop()

    mock_load_clients = AsyncMock()
    mock_load_workers = AsyncMock()

    with (
        patch("avtomatika.engine.load_client_configs_to_redis", mock_load_clients),
        patch("avtomatika.engine.load_worker_configs_to_redis", mock_load_workers),
        patch("avtomatika.engine.ClientSession"),
        patch("avtomatika.engine.Dispatcher"),
        patch("avtomatika.engine.JobExecutor"),
        patch("avtomatika.engine.Watcher"),
        patch("avtomatika.engine.ReputationCalculator"),
        patch("avtomatika.engine.HealthChecker"),
        patch("avtomatika.engine.Scheduler"),
        patch("avtomatika.engine.S3Service"),
        patch("avtomatika.engine.WorkerService"),
        patch("avtomatika.engine.exists", return_value=True),
        patch.object(loop, "create_task", side_effect=create_task_side_effect),
    ):
        await engine.on_startup(app)
        assert mock_load_clients.called
        assert mock_load_workers.called


@pytest.mark.asyncio
async def test_on_shutdown(engine):
    app = web.Application()
    app[ENGINE_KEY] = engine
    loop = asyncio.get_running_loop()

    app[EXECUTOR_KEY] = AsyncMock()
    app[WATCHER_KEY] = AsyncMock()
    app[REPUTATION_CALCULATOR_KEY] = AsyncMock()
    app[HEALTH_CHECKER_KEY] = AsyncMock()
    app[SCHEDULER_KEY] = AsyncMock()
    app[HTTP_SESSION_KEY] = AsyncMock()

    app[EXECUTOR_TASK_KEY] = loop.create_future()
    app[WATCHER_TASK_KEY] = loop.create_future()
    app[REPUTATION_CALCULATOR_TASK_KEY] = loop.create_future()
    app[HEALTH_CHECKER_TASK_KEY] = loop.create_future()
    app[SCHEDULER_TASK_KEY] = loop.create_future()

    tasks_to_complete = [
        EXECUTOR_TASK_KEY,
        WATCHER_TASK_KEY,
        REPUTATION_CALCULATOR_TASK_KEY,
        HEALTH_CHECKER_TASK_KEY,
        SCHEDULER_TASK_KEY,
    ]
    for k in tasks_to_complete:
        app[k].set_result(None)

    engine.history_storage = AsyncMock()
    engine.ws_manager = AsyncMock()
    engine.webhook_sender = AsyncMock()

    with patch("asyncio.gather", AsyncMock(return_value=[])):
        await engine.on_shutdown(app)

    app[EXECUTOR_KEY].stop.assert_called_once()


@pytest.mark.asyncio
async def test_setup_history_storage_noop_by_default(engine):
    await engine._setup_history_storage()
    assert isinstance(engine.history_storage, NoOpHistoryStorage)


@pytest.mark.asyncio
async def test_setup_history_storage_sqlite(engine, monkeypatch):
    engine.config.HISTORY_DATABASE_URI = "sqlite:/:memory:"

    mock_storage_class = MagicMock()
    mock_initialize = AsyncMock()
    mock_storage_class.return_value.initialize = mock_initialize
    mock_module = MagicMock(SQLiteHistoryStorage=mock_storage_class)

    with patch("importlib.import_module", return_value=mock_module) as mock_import:
        monkeypatch.setattr(engine_mod, "import_module", mock_import)
        await engine._setup_history_storage()
        assert mock_import.called
        mock_initialize.assert_called_once()


@pytest.mark.asyncio
async def test_setup_history_storage_postgres(engine, monkeypatch):
    engine.config.HISTORY_DATABASE_URI = "postgresql://user:pass@host:port/db"

    mock_storage_class = MagicMock()
    mock_initialize = AsyncMock()
    mock_storage_class.return_value.initialize = mock_initialize
    mock_module = MagicMock(PostgresHistoryStorage=mock_storage_class)

    with patch("importlib.import_module", return_value=mock_module) as mock_import:
        monkeypatch.setattr(engine_mod, "import_module", mock_import)
        await engine._setup_history_storage()
        assert mock_import.called
        mock_initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_history_storage_unsupported_scheme(engine):
    engine.config.HISTORY_DATABASE_URI = "mysql://user:pass@host:port/db"
    await engine._setup_history_storage()
    assert isinstance(engine.history_storage, NoOpHistoryStorage)


@pytest.mark.asyncio
async def test_setup_history_storage_initialization_failure(engine, monkeypatch):
    engine.config.HISTORY_DATABASE_URI = "sqlite:/:memory:"

    mock_storage_class = MagicMock()
    mock_storage_class.__name__ = "MockStorage"
    mock_storage_class.return_value.initialize = AsyncMock(side_effect=Exception("Boom!"))
    mock_module = MagicMock(SQLiteHistoryStorage=mock_storage_class)

    with patch("importlib.import_module", return_value=mock_module) as mock_import:
        monkeypatch.setattr(engine_mod, "import_module", mock_import)
        await engine._setup_history_storage()
        assert isinstance(engine.history_storage, NoOpHistoryStorage)


@pytest.mark.asyncio
async def test_on_startup_import_error(engine):
    app = web.Application()
    app[ENGINE_KEY] = engine
    engine.app = app

    engine.storage.ping = AsyncMock(return_value=True)
    engine.history_storage.start = AsyncMock()

    with (
        patch("avtomatika.engine.logger"),
        patch("avtomatika.engine.setup_logging"),
        patch.object(engine_mod, "AioHttpClientInstrumentor", None),
        patch("avtomatika.engine.WebhookSender") as MockSender,
        patch("avtomatika.engine.ClientSession"),
        patch("avtomatika.engine.Dispatcher"),
        patch("avtomatika.engine.JobExecutor"),
        patch("avtomatika.engine.Watcher"),
        patch("avtomatika.engine.ReputationCalculator"),
        patch("avtomatika.engine.HealthChecker"),
        patch("avtomatika.engine.Scheduler"),
        patch("avtomatika.engine.S3Service"),
        patch("avtomatika.engine.WorkerService"),
        patch("avtomatika.engine.create_task", side_effect=create_task_side_effect),
    ):
        mock_sender_instance = MockSender.return_value
        await engine.on_startup(app)
        assert mock_sender_instance.start.called

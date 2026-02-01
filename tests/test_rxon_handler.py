from unittest.mock import AsyncMock

import pytest
from rxon.models import TaskPayload

from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def engine():
    storage = MemoryStorage()
    config = Config()
    engine = OrchestratorEngine(storage, config)
    engine.worker_service = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_handle_rxon_register(engine):
    payload = {"worker_id": "w1", "worker_type": "t1"}
    context = {"token": engine.config.GLOBAL_WORKER_TOKEN}

    await engine.handle_rxon_message("register", payload, context)
    engine.worker_service.register_worker.assert_called_once_with(payload)


@pytest.mark.asyncio
async def test_handle_rxon_poll(engine):
    worker_id = "w1"
    context = {"token": engine.config.GLOBAL_WORKER_TOKEN}

    task = TaskPayload(job_id="j1", task_id="t1", type="type1", params={}, tracing_context={})
    engine.worker_service.get_next_task.return_value = task

    result = await engine.handle_rxon_message("poll", worker_id, context)
    assert result == task
    engine.worker_service.get_next_task.assert_called_once_with(worker_id)


@pytest.mark.asyncio
async def test_handle_rxon_result(engine):
    payload = {"job_id": "j1", "task_id": "t1", "worker_id": "w1", "status": "success"}
    context = {"token": engine.config.GLOBAL_WORKER_TOKEN}

    await engine.handle_rxon_message("result", payload, context)
    engine.worker_service.process_task_result.assert_called_once_with(payload, "w1")


@pytest.mark.asyncio
async def test_handle_rxon_auth_failure(engine):
    payload = {"worker_id": "w1"}
    context = {"token": "wrong-token"}

    from aiohttp import web

    with pytest.raises(web.HTTPUnauthorized):
        await engine.handle_rxon_message("register", payload, context)

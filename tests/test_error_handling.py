from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis as redis
import pytest
import pytest_asyncio

from avtomatika.api.handlers import task_result_handler
from avtomatika.app_keys import ENGINE_KEY
from avtomatika.config import Config
from avtomatika.dispatcher import Dispatcher
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.redis import RedisStorage
from tests.test_blueprints import error_flow_bp


@pytest_asyncio.fixture
async def redis_storage():
    client = redis.FakeRedis(decode_responses=False)
    storage = RedisStorage(client)
    yield storage
    await client.aclose()


@pytest.mark.asyncio
async def test_transient_error_retries_then_quarantines(monkeypatch, redis_storage: RedisStorage):
    """
    Tests that a task failing with a TRANSIENT_ERROR is retried and then quarantined,
    by directly testing the result handler logic.
    """
    config = Config()
    storage = redis_storage
    engine = OrchestratorEngine(storage, config)
    engine.register_blueprint(error_flow_bp)
    engine.dispatcher = Dispatcher(storage, config)  # Manually set up the dispatcher

    # Mock the dispatcher to prevent actual dispatching and allow us to assert it was called
    mock_dispatch = AsyncMock()
    monkeypatch.setattr(engine.dispatcher, "dispatch", mock_dispatch)

    job_id = "job-transient-123"
    task_info = {
        "type": "error_task",
        "params": {"error_type": "TRANSIENT_ERROR"},
        "transitions": {"success": "finished", "failure": "failed"},
    }

    # 1. Manually create the job state as if it's waiting for a worker
    initial_job_state = {
        "id": job_id,
        "status": "waiting_for_worker",
        "blueprint_name": "error_flow",
        "retry_count": 0,
        "current_task_info": task_info,
        "current_task_transitions": task_info.get("transitions", {}),
    }
    await storage.save_job_state(job_id, initial_job_state)

    # 2. Simulate worker failure responses for each retry attempt + the final one
    for i in range(config.JOB_MAX_RETRIES + 1):
        payload_data = {
            "job_id": job_id,
            "task_id": "some_task",
            "result": {"status": "failure", "error": {"code": "TRANSIENT_ERROR", "message": "worker failed"}},
        }
        req = MagicMock()
        req.app = {ENGINE_KEY: engine}
        req.json = AsyncMock(return_value=payload_data)
        req.get.return_value = "test-worker"  # Simulate auth middleware (req.get('worker_id'))

        await task_result_handler(req)

        # Check that dispatch was called for retries
        if i < config.JOB_MAX_RETRIES:
            # We need to give the retry logic a moment to run in the background
            job_state = await storage.get_job_state(job_id)
            # The retry logic will try to dispatch the task again.
            assert job_state["status"] == "waiting_for_worker"
            assert job_state["retry_count"] == i + 1
            mock_dispatch.assert_called_once()
            mock_dispatch.reset_mock()
        else:
            # After the last attempt, it should not dispatch again
            mock_dispatch.assert_not_called()

    # 3. Poll for final quarantine status
    final_state = await storage.get_job_state(job_id)
    assert final_state is not None, "Job was not quarantined as expected"
    assert final_state["status"] == "quarantined"
    assert final_state["retry_count"] == config.JOB_MAX_RETRIES
    assert f"Task failed after {config.JOB_MAX_RETRIES + 1} attempts" in final_state["error_message"]


@pytest.mark.asyncio
async def test_permanent_error_quarantines_immediately(monkeypatch, redis_storage: RedisStorage):
    """
    Tests that a PERMANENT_ERROR quarantines immediately without retries.
    """
    config = Config()
    storage = redis_storage
    engine = OrchestratorEngine(storage, config)
    engine.register_blueprint(error_flow_bp)
    engine.dispatcher = Dispatcher(storage, config)

    mock_dispatch = AsyncMock()
    monkeypatch.setattr(engine.dispatcher, "dispatch", mock_dispatch)

    job_id = "job-permanent-123"
    # 1. Manually create the job state
    initial_job_state = {
        "id": job_id,
        "status": "waiting_for_worker",
        "blueprint_name": "error_flow",
        "current_task_transitions": {"success": "finished", "failure": "failed"},
    }
    await storage.save_job_state(job_id, initial_job_state)

    # 2. Simulate a single permanent failure response
    payload_data = {
        "job_id": job_id,
        "task_id": "some_task",
        "result": {"status": "failure", "error": {"code": "PERMANENT_ERROR", "message": "fatal error"}},
    }

    req = MagicMock()
    req.app = {ENGINE_KEY: engine}
    req.json = AsyncMock(return_value=payload_data)
    req.get.return_value = "test-worker"

    await task_result_handler(req)

    # 3. Check for immediate quarantine status
    final_state = await storage.get_job_state(job_id)
    assert final_state is not None, "Job was not quarantined immediately"
    assert final_state["status"] == "quarantined"
    assert final_state.get("retry_count", 0) == 0
    assert "Task failed with permanent error" in final_state.get("error_message", "")
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_input_error_fails_immediately(monkeypatch, redis_storage: RedisStorage):
    """
    Tests that an INVALID_INPUT_ERROR fails the job immediately without retries.
    """
    config = Config()
    storage = redis_storage
    engine = OrchestratorEngine(storage, config)
    engine.register_blueprint(error_flow_bp)
    engine.dispatcher = Dispatcher(storage, config)

    mock_dispatch = AsyncMock()
    monkeypatch.setattr(engine.dispatcher, "dispatch", mock_dispatch)

    job_id = "job-invalid-123"
    # 1. Manually create the job state
    initial_job_state = {
        "id": job_id,
        "status": "waiting_for_worker",
        "blueprint_name": "error_flow",
        "current_task_transitions": {"success": "finished", "failure": "failed"},
    }
    await storage.save_job_state(job_id, initial_job_state)

    # 2. Simulate a single invalid input failure response
    payload_data = {
        "job_id": job_id,
        "task_id": "some_task",
        "result": {"status": "failure", "error": {"code": "INVALID_INPUT_ERROR", "message": "bad params"}},
    }
    req = MagicMock()
    req.app = {ENGINE_KEY: engine}
    req.json = AsyncMock(return_value=payload_data)
    req.get.return_value = "test-worker"

    await task_result_handler(req)

    # 3. Check for immediate failed status
    final_state = await storage.get_job_state(job_id)
    assert final_state is not None, "Job did not fail immediately"
    assert final_state["status"] == "failed"
    assert final_state.get("retry_count", 0) == 0
    assert "Task failed due to invalid input" in final_state.get("error_message", "")
    mock_dispatch.assert_not_called()

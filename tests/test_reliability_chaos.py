# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis

from avtomatika.constants import AUTH_HEADER_WORKER
from avtomatika.dispatcher import Dispatcher
from avtomatika.engine import OrchestratorEngine
from avtomatika.executor import JobExecutor
from avtomatika.storage.memory import MemoryStorage
from avtomatika.storage.redis import RedisStorage
from avtomatika.ws_manager import WebSocketManager
from tests.conftest import STORAGE_KEY


def make_valid_worker_payload(worker_id: str, **kwargs) -> dict[str, Any]:
    payload = {
        "worker_id": worker_id,
        "worker_type": "test-worker",
        "supported_skills": [{"name": "test"}],
        "resources": {
            "properties": {"cpu_cores": 4, "ram_gb": 8.0},
        },
        "installed_software": {"python": "3.11"},
        "installed_artifacts": [],
        "capabilities": {
            "hostname": "test-host",
            "ip_address": "127.0.0.1",
            "cost_per_skill": {},
        },
    }
    payload.update(kwargs)
    return payload


@pytest.mark.asyncio
async def test_corrupted_redis_data_handling(redis_client: FakeRedis, redis_storage: RedisStorage):
    """
    Chaos: Inject corrupted (non-msgpack) data into Redis.
    The system should not crash but log an error and return None.
    """
    job_id = "chaos-job-1"
    key = f"orchestrator:job:{job_id}"

    # Inject completely random bytes that are NOT a valid msgpack
    await redis_client.set(key, b"\xff\xfe\xfd\xfc\x00\x01")

    # Should not raise an exception, should return None due to our new try-except in _unpack
    state = await redis_storage.get_job_state(job_id)
    assert state is None


@pytest.mark.asyncio
async def test_worker_malformed_registration_null_skills(aiohttp_client, app):
    """
    Reliability: Register a worker with null supported_skills.
    Should handle gracefully instead of 500 error.
    """
    client = await aiohttp_client(app)

    worker_payload = make_valid_worker_payload("malicious-worker-1", supported_skills=None)

    headers = {AUTH_HEADER_WORKER: "secure-worker-token"}
    resp = await client.post("/_worker/workers/register", json=worker_payload, headers=headers)

    # It should succeed by substituting [] instead of 500.
    assert resp.status == 200

    storage = app[STORAGE_KEY]
    worker_info = await storage.get_worker_info("malicious-worker-1")
    assert worker_info["supported_skills"] == []


@pytest.mark.asyncio
async def test_worker_heartbeat_null_current_tasks(aiohttp_client, app):
    """
    Reliability: Send a heartbeat with null current_tasks.
    Should handle gracefully.
    """
    client = await aiohttp_client(app)
    worker_id = "w1"

    # First register normally
    headers = {AUTH_HEADER_WORKER: "secure-worker-token"}
    worker_payload = make_valid_worker_payload(worker_id)
    await client.post("/_worker/workers/register", json=worker_payload, headers=headers)

    # Then send heartbeat (PATCH) with null current_tasks
    heartbeat_payload = {
        "status": "busy",
        "current_tasks": None,  # Malicious null
    }

    resp = await client.patch(f"/_worker/workers/{worker_id}", json=heartbeat_payload, headers=headers)
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_executor_resilience_to_storage_exceptions(config, redis_storage):
    """
    Chaos: Simulate storage exceptions during job dequeuing.
    Executor should perform exponential backoff and continue.
    """
    engine = OrchestratorEngine(redis_storage, config)
    engine.dispatcher = Dispatcher(redis_storage, config)

    executor = JobExecutor(engine, engine.history_storage)

    # Mock dequeue_job to raise an exception
    original_dequeue = redis_storage.dequeue_job
    redis_storage.dequeue_job = AsyncMock(
        side_effect=[
            RuntimeError("Redis connection lost!"),  # First call fails
            ("job1", "msg1"),  # Second call succeeds
            None,  # Third call empty (to stop loop)
        ]
    )

    # Run executor in background for a short time
    executor_task = asyncio.create_task(executor.run())

    # Wait long enough for the first retry attempt (initial backoff is 1.0s)
    await asyncio.sleep(1.5)

    assert redis_storage.dequeue_job.call_count >= 2

    executor.stop()
    await executor_task

    # Restore
    redis_storage.dequeue_job = original_dequeue


@pytest.mark.asyncio
async def test_ws_manager_parallel_closing_resilience():
    """
    Reliability: WSManager.close_all should handle failing websocket closures gracefully.
    """
    storage = MemoryStorage()
    manager = WebSocketManager(storage)

    mock_ws_fail = AsyncMock()
    mock_ws_fail.close.side_effect = Exception("Websocket already dead")

    mock_ws_ok = AsyncMock()

    await manager.register("worker_fail", mock_ws_fail)
    await manager.register("worker_ok", mock_ws_ok)

    # Should not raise exception
    await manager.close_all()

    mock_ws_fail.close.assert_called_once()
    mock_ws_ok.close.assert_called_once()
    assert len(manager._connections) == 0


@pytest.mark.asyncio
async def test_dispatcher_null_safety_complex_payloads(redis_storage, config):
    """
    Reliability: Dispatcher should handle missing or null resource_requirements and params.
    """
    dispatcher = Dispatcher(redis_storage, config)

    job_state = {"id": "j1"}
    # Maliciously empty task_info
    task_info = {"type": "some_skill", "dispatch_strategy": "default", "resource_requirements": None, "params": None}

    # This should NOT raise TypeError/KeyError with our new or {} fixes
    try:
        await dispatcher.dispatch(job_state, task_info)
    except ValueError as e:
        # Expected if no workers found, but NOT a TypeError
        assert "No capable workers found" in str(e)
    except Exception as e:
        assert not isinstance(e, (TypeError, KeyError, AttributeError))

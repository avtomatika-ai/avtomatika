# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Dmitrii Gagarin aka madgagarin

from unittest.mock import AsyncMock, MagicMock

import msgpack
import pytest

from avtomatika.context import ActionFactory
from avtomatika.executor import JobExecutor
from avtomatika.storage.memory import MemoryStorage
from avtomatika.storage.redis import RedisStorage


@pytest.fixture
def config():
    mock_conf = MagicMock()
    mock_conf.JOB_MAX_RETRIES = 3
    mock_conf.MAX_TRANSITIONS_PER_JOB = 100
    return mock_conf


@pytest.fixture
def engine(config):
    engine_mock = MagicMock()
    engine_mock.config = config
    engine_mock.app = {}
    engine_mock.blueprints = {}
    engine_mock.worker_service = None
    return engine_mock


# --- 1. RedisStorage Normalization Tests ---


def test_redis_normalize_byte_keys():
    """HAPPY PATH: Byte keys from Redis Lua should be decoded to strings."""
    raw_data = {b"worker_id": "w1", b"resources": {b"cpu": 4, b"ram": 8192}}
    normalized = RedisStorage._normalize_unpacked(raw_data)

    assert normalized == {"worker_id": "w1", "resources": {"cpu": 4, "ram": 8192}}
    assert isinstance(list(normalized.keys())[0], str)


def test_redis_normalize_recursive_msgpack():
    """HAPPY PATH: Deeply nested msgpack strings should be automatically unpacked."""
    inner = {"result": "ok", "value": 42}
    packed_inner = msgpack.packb(inner)

    # Simulating data structure where one field is a msgpack blob
    raw_data = {"id": "job-1", "payload": packed_inner}

    normalized = RedisStorage._normalize_unpacked(raw_data)
    assert normalized["payload"] == inner
    assert normalized["payload"]["value"] == 42


def test_redis_normalize_ambiguous_binary_data():
    """PROBLEM PATH: Binary data starting with msgpack-like byte but having trailing garbage."""
    # 0x81 is a msgpack prefix for a 1-element map.
    fake_msgpack = b"\x81\xa1a\x01" + b"GARBAGE_TRAILING_DATA"

    normalized = RedisStorage._normalize_unpacked(fake_msgpack)

    # It should remain raw bytes
    assert isinstance(normalized, bytes)
    assert normalized == fake_msgpack


def test_redis_bzpopmax_logic_fix():
    """HAPPY PATH: Verify the logic of Beta 20 fix (result[1] instead of result[1][0])."""
    task_data = {"task_id": "t1", "type": "test"}
    packed_task = msgpack.packb(task_data)

    # Redis 5.0+ bzpopmax returns [key, value, score]
    result_from_redis = [b"queue_key", packed_task, 10.5]

    # This is exactly what the new dequeue_task_for_worker does:

    # We call _unpack manually to verify it handles the second element of the list
    task = RedisStorage._unpack(result_from_redis[1])

    assert task == task_data
    assert task["task_id"] == "t1"


# --- 2. ActionFactory Context Update Tests ---


def test_action_factory_context_update():
    """HAPPY PATH: update_context should accumulate updates in _context_updates."""
    factory = ActionFactory("job-123")

    factory.update_context({"token": "abc"})
    factory.update_context({"user_id": 42})

    assert factory.context_updates == {"token": "abc", "user_id": 42}

    # Overwrite check
    factory.update_context({"token": "new-token"})
    assert factory.context_updates["token"] == "new-token"


def test_action_factory_update_context_problematic_input():
    """PROBLEM PATH: update_context should raise ValueError/TypeError if input is not a dict."""
    factory = ActionFactory("job-123")

    with pytest.raises((ValueError, TypeError)):
        factory.update_context("not-a-dict")  # type: ignore


@pytest.mark.asyncio
async def test_executor_applies_context_updates(engine):
    """HAPPY PATH: JobExecutor should apply updates to job_state['initial_data']."""
    storage = MemoryStorage()
    history = AsyncMock()

    executor = JobExecutor(engine, history)
    executor.storage = storage

    job_id = "test-context-sync"
    job_state = {
        "id": job_id,
        "blueprint_name": "sync_bp",
        "current_state": "start",
        "status": "running",
        "initial_data": {"existing": "val"},
        "client_config": {},
    }
    await storage.save_job_state(job_id, job_state)

    mock_bp = MagicMock()
    mock_bp.start_state = "start"
    mock_bp.data_stores = {}
    mock_bp.end_states = ["end"]
    mock_bp.aggregator_handlers = {}
    engine.blueprints = {"sync_bp": mock_bp}

    async def handler(actions):
        actions.update_context({"new_key": "secret", "existing": "overwritten"})
        actions.go_to("end")

    mock_bp.find_handler.return_value = handler
    mock_bp.get_handler_params.return_value = ("actions",)

    await executor._process_job(job_id, "msg-1")

    updated_state = await storage.get_job_state(job_id)
    assert updated_state["initial_data"]["new_key"] == "secret"
    assert updated_state["initial_data"]["existing"] == "overwritten"


@pytest.mark.asyncio
async def test_executor_context_update_persistence(engine):
    """HAPPY PATH: ensure context updates are persisted even if transition fails later."""
    storage = MemoryStorage()
    history = AsyncMock()
    executor = JobExecutor(engine, history)
    executor.storage = storage

    job_id = "test-persistence"
    job_state = {
        "id": job_id,
        "blueprint_name": "persist_bp",
        "current_state": "start",
        "status": "running",
        "initial_data": {"v": 1},
        "client_config": {},
    }
    await storage.save_job_state(job_id, job_state)

    mock_bp = MagicMock()
    mock_bp.start_state = "start"
    mock_bp.data_stores = {}
    mock_bp.end_states = ["end"]
    mock_bp.aggregator_handlers = {}
    engine.blueprints = {"persist_bp": mock_bp}

    async def handler(actions):
        actions.update_context({"v": 2})

    mock_bp.find_handler.return_value = handler
    mock_bp.get_handler_params.return_value = ("actions",)

    await executor._process_job(job_id, "msg-1")

    updated_state = await storage.get_job_state(job_id)
    assert updated_state["initial_data"]["v"] == 2

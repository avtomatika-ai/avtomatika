# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Dmitrii Gagarin aka madgagarin

import asyncio
import contextlib
from time import time
from unittest.mock import AsyncMock, MagicMock

import docker
import pytest
import pytest_asyncio
import redis.asyncio as redis

from avtomatika.config import Config
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.redis import RedisStorage


async def is_redis_accessible(host="localhost", port=6379):
    """Check if Redis is accessible at the given host and port."""
    try:
        conn = await asyncio.open_connection(host, port)
        conn[1].close()
        with contextlib.suppress(Exception):
            await conn[1].wait_closed()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture(scope="module")
async def redis_container():
    """Optional fixture to start a Redis container if docker is available."""
    container = None
    try:
        # 1. First, check if Redis is already accessible (e.g. running on host or in an existing container)
        if await is_redis_accessible():
            yield None
            return

        client = docker.from_env()

        # 2. Check if there's any container already running the redis image and mapping to 6379
        for c in client.containers.list():
            if "redis" in c.image.tags[0] and "6379/tcp" in c.ports:
                # If we found a running redis container on the right port, use it
                yield None
                return

        # 3. Check if our specific test container exists but is stopped
        try:
            c = client.containers.get("avtomatika-test-redis")
            if c.status != "running":
                c.start()
            container = c
        except docker.errors.NotFound:
            # 4. If not found, create and run a new one
            container = client.containers.run(
                "redis:alpine", detach=True, ports={"6379/tcp": 6379}, name="avtomatika-test-redis", auto_remove=True
            )

        # Wait for redis to be ready
        for _ in range(10):
            if await is_redis_accessible():
                break
            await asyncio.sleep(0.5)
    except Exception:
        pass

    yield container

    if container:
        with contextlib.suppress(Exception):
            container.stop()


@pytest_asyncio.fixture
async def real_redis_storage(redis_container):
    """Fixture that connects to the actual Redis in Docker for Lua testing.
    Uses DB 10 to avoid interference with the main orchestrator DB.
    """
    host = "localhost"
    port = 6379

    if not await is_redis_accessible(host, port):
        pytest.skip("Real Redis is not available (Docker might be missing or port 6379 blocked)")

    client = redis.Redis(host=host, port=port, db=10, decode_responses=False)
    try:
        # Flush test DB to ensure clean state
        await client.flushdb()
        storage = RedisStorage(client, consumer_name="test-fix-validator")
        yield storage
        await client.flushdb()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_redis_load_counter_resilience(real_redis_storage):
    """
    Test that _internal_load increments, decrements, and never goes below zero.
    Uses real Redis because fakeredis lacks cmsgpack support.
    """
    storage = real_redis_storage
    worker_id = "test-worker-load"

    await storage.register_worker(worker_id, {"status": "idle"}, ttl=60)

    # 1. Initial load should be 0
    info = await storage.get_worker_info(worker_id)
    assert info.get("_internal_load", 0) == 0

    # 2. Increment
    await storage.increment_worker_load(worker_id)
    info = await storage.get_worker_info(worker_id)
    assert info["_internal_load"] == 1

    # 3. Decrement
    await storage.decrement_worker_load(worker_id)
    info = await storage.get_worker_info(worker_id)
    assert info["_internal_load"] == 0

    # 4. EDGE CASE: Decrement below zero
    await storage.decrement_worker_load(worker_id)
    info = await storage.get_worker_info(worker_id)
    assert info["_internal_load"] == 0  # Should stay at 0, not -1


@pytest.mark.asyncio
async def test_redis_index_expiry_robustness(real_redis_storage):
    """
    Test that aggregate='max' in ZINTERSTORE allows finding workers
    even if their skill index is older than their idle index.
    """
    storage = real_redis_storage
    worker_id = "gpu-worker-flaky"
    skill = "transcode_video"
    now = int(time())

    await storage.register_worker(worker_id, {"status": "idle", "supported_skills": [{"name": skill}]}, ttl=60)

    # Simulate stale skill index (30s old) but fresh idle index (active)
    await storage._redis.zadd(f"orchestrator:index:workers:skill:{skill}", {worker_id: now - 30})
    await storage._redis.zadd("orchestrator:index:workers:idle", {worker_id: now + 60})

    found_workers = await storage.find_workers_for_skill(skill)
    assert worker_id in found_workers


@pytest.mark.asyncio
async def test_worker_service_decrements_load_on_result(real_redis_storage):
    """
    Test that WorkerService calls decrement_worker_load when a result is processed.
    """
    storage = real_redis_storage
    config = Config()

    # Mock engine to provide storage
    engine = MagicMock()
    engine.storage = storage
    engine.config = config

    # Initialize WorkerService with required args
    worker_service = WorkerService(engine=engine, storage=storage, history_storage=MagicMock(), config=config)

    worker_id = "test-worker-service"
    await storage.register_worker(worker_id, {"status": "idle"}, ttl=60)
    await storage.increment_worker_load(worker_id)

    result_payload = {
        "task_id": "task-1",
        "job_id": "job-1",
        "worker_id": worker_id,
        "status": "success",
        "result": {},
        "timestamp": int(time()),
    }

    # Mock signature verification
    worker_service._verify_zero_trust = AsyncMock(return_value=None)

    with contextlib.suppress(Exception):
        await worker_service.process_task_result(result_payload, authenticated_worker_id=worker_id)

    info = await storage.get_worker_info(worker_id)
    assert info.get("_internal_load", 0) == 0

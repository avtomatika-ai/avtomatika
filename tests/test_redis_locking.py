# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio

import fakeredis.aioredis
import pytest

from avtomatika.storage.redis import RedisStorage


@pytest.mark.asyncio
async def test_redis_storage_locking():
    """Tests distributed locking logic in RedisStorage using fakeredis."""
    # fakeredis usually supports basic Lua and SET NX

    redis_client = fakeredis.aioredis.FakeRedis()
    storage = RedisStorage(redis_client)

    key = "global_lock"
    holder_1 = "worker_a"
    holder_2 = "worker_b"

    assert await storage.acquire_lock(key, holder_1, ttl=10)
    assert not await storage.acquire_lock(key, holder_2, ttl=10)
    assert await storage.release_lock(key, holder_2) is False or await storage.release_lock(key, holder_2) == 0
    assert await storage.release_lock(key, holder_1)
    assert await storage.acquire_lock(key, holder_2, ttl=10)

    await storage.release_lock(key, holder_2)
    assert await storage.acquire_lock(key, holder_1, ttl=1) is True

    # Wait for expiration in Redis
    await asyncio.sleep(1.1)

    # Now it should be free
    assert await storage.acquire_lock(key, holder_2, ttl=10) is True

    await redis_client.aclose()

import asyncio

import pytest
from src.avtomatika.storage.redis import RedisStorage


@pytest.mark.asyncio
async def test_redis_storage_locking():
    """Tests distributed locking logic in RedisStorage using fakeredis."""
    # fakeredis usually supports basic Lua and SET NX
    import fakeredis.aioredis

    redis_client = fakeredis.aioredis.FakeRedis()
    storage = RedisStorage(redis_client)

    key = "global_lock"
    holder_1 = "worker_a"
    holder_2 = "worker_b"

    # 1. Acquire lock
    assert await storage.acquire_lock(key, holder_1, ttl=10) is True

    # 2. Try acquire by another holder (should fail)
    assert not await storage.acquire_lock(key, holder_2, ttl=10)

    # 3. Release by wrong holder (should fail)
    assert await storage.release_lock(key, holder_2) is False

    # 4. Release by correct holder (should succeed)
    assert await storage.release_lock(key, holder_1) is True

    # 5. Acquire again
    assert await storage.acquire_lock(key, holder_2, ttl=10) is True

    # 6. Test expiration (short TTL)
    await storage.release_lock(key, holder_2)
    assert await storage.acquire_lock(key, holder_1, ttl=1) is True

    # Wait for expiration in Redis
    await asyncio.sleep(1.1)

    # Now it should be free
    assert await storage.acquire_lock(key, holder_2, ttl=10) is True

    await redis_client.aclose()

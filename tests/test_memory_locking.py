import asyncio

import pytest
from src.avtomatika.storage.memory import MemoryStorage


@pytest.mark.asyncio
async def test_memory_storage_locking():
    storage = MemoryStorage()
    key = "test_lock"

    # 1. Acquire successfully
    assert await storage.acquire_lock(key, "holder_1", 10) is True

    # 2. Fail to acquire with different holder
    assert await storage.acquire_lock(key, "holder_2", 10) is False

    # 3. Fail to acquire with same holder (lock is non-reentrant in our simple impl)
    assert await storage.acquire_lock(key, "holder_1", 10) is False

    # 4. Fail to release by wrong holder
    assert await storage.release_lock(key, "holder_2") is False

    # 5. Successfully release by correct holder
    assert await storage.release_lock(key, "holder_1") is True

    # 6. Acquire again by different holder
    assert await storage.acquire_lock(key, "holder_2", 10) is True

    # 7. Test expiration logic
    await storage.release_lock(key, "holder_2")

    # Set short TTL
    assert await storage.acquire_lock(key, "holder_3", 0.1) is True
    assert await storage.acquire_lock(key, "holder_4", 10) is False

    # Wait for expiration
    await asyncio.sleep(0.2)

    # Now it should be acquirable (MemoryStorage checks expiry on acquire)
    # Note: Our MemoryStorage implementation of acquire_lock checks:
    # `if current_lock and current_lock[1] > now:`
    # So if expired, it overwrites.
    assert await storage.acquire_lock(key, "holder_4", 10) is True

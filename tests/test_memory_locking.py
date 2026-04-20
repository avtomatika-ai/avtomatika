# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio

import pytest
from src.avtomatika.storage.memory import MemoryStorage


@pytest.mark.asyncio
async def test_memory_storage_locking():
    storage = MemoryStorage()
    key = "test_lock"

    assert await storage.acquire_lock(key, "holder_1", 10) is True

    assert await storage.acquire_lock(key, "holder_2", 10) is False

    assert await storage.acquire_lock(key, "holder_1", 10) is False

    assert await storage.release_lock(key, "holder_2") is False

    assert await storage.release_lock(key, "holder_1") is True

    assert await storage.acquire_lock(key, "holder_2", 10) is True

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

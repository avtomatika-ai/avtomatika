# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import pytest


@pytest.mark.asyncio
@pytest.mark.skipif(not pytest.importorskip("redis"), reason="redis not installed")
async def test_redis_storage_get_ttl(redis_storage):
    """Directly tests the get_ttl method in RedisStorage."""
    storage = redis_storage
    key = "test_ttl_key"

    # 1. Key doesn't exist
    ttl = await storage.get_ttl(key)
    assert ttl == -2

    # 2. Key exists but no TTL
    await storage._redis.set(key, "value")
    ttl = await storage.get_ttl(key)
    assert ttl == -1

    # 3. Key exists with TTL
    await storage._redis.set(key, "value", ex=60)
    ttl = await storage.get_ttl(key)
    assert 50 <= ttl <= 60

    await storage._redis.delete(key)

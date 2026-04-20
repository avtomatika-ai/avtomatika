# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock

import pytest

from avtomatika.history.noop import NoOpHistoryStorage
from avtomatika.history.sqlite import SQLiteHistoryStorage
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.memory import MemoryStorage


@pytest.mark.asyncio
async def test_memory_storage_collections_integrity():
    storage = MemoryStorage()

    assert await storage.get_available_workers() == []
    assert await storage.get_active_worker_ids() == []
    assert await storage.get_workers(["non-existent"]) == []
    assert await storage.find_workers_for_skill("none") == []
    assert await storage.find_hot_workers("none", "none") == []
    assert await storage.find_workers_by_hot_skill("none") == []
    assert await storage.get_timed_out_jobs() == []
    assert await storage.get_quarantined_jobs() == []
    assert await storage.mget(["none"]) == [None]  # mget returns list of values, None is correct for missing key

    # Test dicts (that don't return None)
    assert await storage.get_priority_queue_stats("none") == {
        "queue_name": "in-memory:none",
        "task_count": 0,
        "highest_bids": [],
        "lowest_bids": [],
        "average_bid": 0,
        "error": "Statistics are not supported for MemoryStorage backend.",
    }


@pytest.mark.asyncio
async def test_noop_history_collections_integrity():
    storage = NoOpHistoryStorage()

    assert await storage.get_job_history("none") == []
    assert await storage.get_jobs() == []
    assert await storage.get_worker_history("none", 1) == []
    assert await storage.get_job_summary() == {}


@pytest.mark.asyncio
async def test_sqlite_history_collections_integrity(tmp_path):
    db_path = str(tmp_path / "test_history.db")
    storage = SQLiteHistoryStorage(db_path)
    await storage.initialize()

    assert await storage.get_job_history("none") == []
    assert await storage.get_jobs() == []
    assert await storage.get_worker_history("none", 1) == []
    assert await storage.get_job_summary() == {}

    await storage.close()


@pytest.mark.asyncio
async def test_redis_storage_collections_integrity(redis_storage):
    # Use the real redis_storage fixture if available
    storage = redis_storage

    # Clean state
    await storage._redis.flushdb()

    assert await storage.find_workers_for_skill("none") == []
    assert await storage.find_hot_workers("none", "none") == []
    assert await storage.find_workers_by_hot_skill("none") == []
    assert await storage.get_available_workers() == []
    assert await storage.get_workers(["none"]) == []
    assert await storage.get_active_worker_ids() == []
    assert await storage.get_timed_out_jobs() == []
    assert await storage.get_quarantined_jobs() == []
    assert await storage.mget(["none"]) == [None]

    stats = await storage.get_priority_queue_stats("none")
    assert stats["task_count"] == 0
    assert stats["highest_bids"] == []


@pytest.mark.asyncio
async def test_worker_service_collections_integrity():
    mock_storage = MagicMock()
    mock_storage.refresh_worker_ttl = AsyncMock(return_value=False)

    service = WorkerService(mock_storage, MagicMock(), MagicMock(), MagicMock())

    # Test that it returns None on failed refresh
    res = await service.update_worker_heartbeat("w1", None)
    assert res is None

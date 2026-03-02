# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import pytest
from src.avtomatika.history.noop import NoOpHistoryStorage


@pytest.mark.asyncio
async def test_noop_history_storage():
    """Tests that the NoOpHistoryStorage methods do nothing and return empty values."""
    storage = NoOpHistoryStorage()

    await storage.initialize()
    await storage.log_job_event({})
    await storage.log_worker_event({})

    assert await storage.get_job_history("job-1") == []
    assert await storage.get_jobs() == []
    assert await storage.get_job_summary() == {}
    assert await storage.get_worker_history("worker-1", 1) == []

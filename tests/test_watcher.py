# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.avtomatika.config import Config
from src.avtomatika.engine import OrchestratorEngine
from src.avtomatika.watcher import Watcher


@pytest.mark.asyncio
async def test_watcher_run():
    """Tests that the watcher correctly identifies and handles timed out jobs."""
    engine = MagicMock(spec=OrchestratorEngine)
    engine.storage = AsyncMock()
    engine.config = Config()  # Use real config to avoid lazy behavior
    engine.config.WATCHER_LIMIT = 500

    job_1_state = {
        "id": "job-1",
        "status": "waiting_for_worker",
        "blueprint_name": "test_bp",
    }

    engine.storage.get_timed_out_jobs.return_value = ["job-1"]
    engine.storage.get_job_state.return_value = job_1_state
    engine.storage.acquire_lock = AsyncMock(return_value=True)
    engine.storage.release_lock = AsyncMock(return_value=True)
    engine.handle_job_timeout = AsyncMock()

    watcher = Watcher(engine)
    watcher.watch_interval_seconds = 0.05

    # Start watcher
    task = asyncio.create_task(watcher.run())
    # Give it enough time to run at least once
    await asyncio.sleep(0.1)
    watcher.stop()
    await task

    engine.storage.get_timed_out_jobs.assert_called()
    engine.storage.get_job_state.assert_called_with("job-1")

    # Check that handle_job_timeout was called with the CORRECT state
    engine.handle_job_timeout.assert_called()
    called_state = engine.handle_job_timeout.call_args[0][0]
    assert called_state["id"] == "job-1"
    assert called_state["status"] == "waiting_for_worker"

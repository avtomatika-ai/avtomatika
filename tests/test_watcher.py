import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.avtomatika.watcher import Watcher


@pytest.mark.asyncio
async def test_watcher_run():
    """Tests that the watcher correctly identifies and handles timed out jobs."""
    engine = MagicMock()
    engine.storage.get_timed_out_jobs = AsyncMock(return_value=["job-1"])
    engine.storage.get_job_state = AsyncMock(
        return_value={
            "id": "job-1",
            "status": "waiting_for_worker",
            "blueprint_name": "test_bp",
        }
    )
    engine.storage.acquire_lock = AsyncMock(return_value=True)
    engine.storage.release_lock = AsyncMock(return_value=True)
    engine.storage.save_job_state = AsyncMock()  # Mock save_job_state too
    engine.send_job_webhook = AsyncMock()  # Must be async mock now
    engine.app.get = MagicMock(return_value=None)  # S3 Service mock
    engine.handle_job_timeout = AsyncMock()  # Mock the delegated handler

    watcher = Watcher(engine)
    watcher.watch_interval_seconds = 0.1

    # Run the watcher for a short period
    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.2)
    watcher.stop()
    await task

    engine.storage.get_timed_out_jobs.assert_called()
    engine.storage.get_job_state.assert_called_with("job-1")
    # Logic is now delegated to handle_job_timeout
    engine.handle_job_timeout.assert_called()

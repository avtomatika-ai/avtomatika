import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.avtomatika.app_keys import (
    EXECUTOR_KEY,
    EXECUTOR_TASK_KEY,
    HEALTH_CHECKER_KEY,
    HEALTH_CHECKER_TASK_KEY,
    REPUTATION_CALCULATOR_KEY,
    REPUTATION_CALCULATOR_TASK_KEY,
    SCHEDULER_KEY,
    SCHEDULER_TASK_KEY,
    WATCHER_KEY,
    WATCHER_TASK_KEY,
)
from src.avtomatika.engine import OrchestratorEngine


@pytest.mark.asyncio
async def test_graceful_shutdown_waits_for_tasks():
    """
    Tests that on_shutdown calls stop() on services and waits for tasks to complete
    instead of cancelling them immediately (for Executor and Scheduler).
    """
    # Mock storage and config
    storage = AsyncMock()
    storage.ping.return_value = True
    config = MagicMock()
    config.TLS_ENABLED = False
    config.CLIENTS_CONFIG_PATH = ""
    config.WORKERS_CONFIG_PATH = ""
    config.SCHEDULES_CONFIG_PATH = ""
    config.RATE_LIMITING_ENABLED = False
    config.LOG_LEVEL = "INFO"
    config.LOG_FORMAT = "text"
    config.TZ = "UTC"

    # Initialize engine
    engine = OrchestratorEngine(storage, config)
    app = engine.app

    # Create mock background tasks that simulate work
    # We want Executor to finish its work, not be cancelled.
    executor_finished = asyncio.Event()

    async def mock_executor_run():
        try:
            # Simulate some work
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            # Should NOT be cancelled
            raise AssertionError("Executor task was cancelled unexpectedly!") from None
        finally:
            executor_finished.set()

    # Watcher CAN be cancelled
    watcher_cancelled = asyncio.Event()

    async def mock_watcher_run():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            watcher_cancelled.set()
            # Re-raise or just return, doesn't matter for the test logic itself,
            # but usually cancellation should propagate if we want .cancelled() to be True.
            # However, for graceful shutdown we just want it to stop.
            raise

    # Assign mock tasks to app keys
    # We use create_task to make them real asyncio Tasks
    executor_task = asyncio.create_task(mock_executor_run())
    watcher_task = asyncio.create_task(mock_watcher_run())

    app[EXECUTOR_TASK_KEY] = executor_task
    app[WATCHER_TASK_KEY] = watcher_task
    # Mock others as done tasks
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    app[REPUTATION_CALCULATOR_TASK_KEY] = done_task
    app[HEALTH_CHECKER_TASK_KEY] = done_task
    app[SCHEDULER_TASK_KEY] = done_task

    # Mock services to verify stop() is called
    engine.app[EXECUTOR_KEY] = MagicMock()
    engine.app[SCHEDULER_KEY] = MagicMock()
    engine.app[WATCHER_KEY] = MagicMock()  # Should receive stop too
    engine.app[REPUTATION_CALCULATOR_KEY] = MagicMock()
    engine.app[HEALTH_CHECKER_KEY] = MagicMock()

    # Mock ws_manager and webhook_sender
    engine.ws_manager = AsyncMock()
    engine.webhook_sender = MagicMock()
    engine.webhook_sender.stop = AsyncMock()

    # Mock rxon listener
    engine.rxon_listener = AsyncMock()

    # Trigger shutdown
    # We need to mock HTTP_SESSION_KEY to avoid actual cleanup error
    from src.avtomatika.app_keys import HTTP_SESSION_KEY

    app[HTTP_SESSION_KEY] = AsyncMock()

    await engine.on_shutdown(app)

    # Verify Executor finished naturally
    assert executor_finished.is_set()
    assert executor_task.done()
    assert not executor_task.cancelled()

    # Verify Watcher was cancelled
    assert watcher_cancelled.is_set()

    # Verify stop() methods were called
    engine.app[EXECUTOR_KEY].stop.assert_called_once()
    engine.app[SCHEDULER_KEY].stop.assert_called_once()

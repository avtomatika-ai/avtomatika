import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from src.avtomatika.blueprint import StateMachineBlueprint
from src.avtomatika.config import Config
from src.avtomatika.executor import JobExecutor
from src.avtomatika.history.sqlite import SQLiteHistoryStorage
from src.avtomatika.storage.redis import RedisStorage

# Mark all tests in this file as asyncio
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sqlite_storage():
    """Fixture to create an in-memory SQLite history storage for testing."""
    storage = SQLiteHistoryStorage(":memory:")
    await storage.initialize()
    await storage.start()
    yield storage
    await storage.close()


async def test_sqlite_log_and_get_job_event(sqlite_storage: SQLiteHistoryStorage):
    """Tests basic logging and retrieval of a job event for SQLiteHistoryStorage."""
    job_id = "test-job-123"
    event_data = {
        "job_id": job_id,
        "state": "start",
        "event_type": "state_started",
        "context_snapshot": {"some_data": "value"},
    }

    # Log an event
    await sqlite_storage.log_job_event(event_data)
    await asyncio.sleep(0.1)

    # Retrieve history
    history = await sqlite_storage.get_job_history(job_id)

    # Assertions
    assert len(history) == 1
    event = history[0]
    assert event["job_id"] == job_id
    assert event["state"] == "start"
    assert event["event_type"] == "state_started"
    assert event["context_snapshot"] == {"some_data": "value"}


async def test_sqlite_log_and_get_worker_event(sqlite_storage: SQLiteHistoryStorage):
    """Tests logging of a worker event. This doesn't have a 'get' method,
    so we just test that the call succeeds.
    """
    worker_id = "test-worker-abc"
    event_data = {
        "worker_id": worker_id,
        "event_type": "registered",
        "worker_info_snapshot": {"ram": "32g", "gpu": "none"},
    }

    # This should execute without raising an exception
    await sqlite_storage.log_worker_event(event_data)
    await asyncio.sleep(0.1)


async def test_get_job_history_empty(sqlite_storage: SQLiteHistoryStorage):
    """Tests that getting history for a job with no events returns an empty list."""
    history = await sqlite_storage.get_job_history("non-existent-job")
    assert history == []


@pytest.mark.asyncio
async def test_initialize_fails(mocker):
    async def mock_connect(*args, **kwargs):
        raise aiosqlite.Error("mock error")

    mocker.patch("src.avtomatika.history.sqlite.connect", mock_connect)
    storage = SQLiteHistoryStorage(":memory:")
    with pytest.raises(aiosqlite.Error):
        await storage.initialize()


@pytest.mark.asyncio
async def test_log_job_event_fails(sqlite_storage: SQLiteHistoryStorage, mocker, caplog):
    # This method is directly awaited, so a simple side_effect works.
    async def mock_execute(*args, **kwargs):
        raise aiosqlite.Error("Mock DB Error")

    mocker.patch.object(sqlite_storage, "_persist_job_event", side_effect=mock_execute)
    await sqlite_storage.log_job_event({"job_id": "test-job"})
    await asyncio.sleep(0.1)
    assert "Error persisting history event" in caplog.text


@pytest.mark.asyncio
async def test_log_worker_event_fails(sqlite_storage: SQLiteHistoryStorage, mocker, caplog):
    # This method is directly awaited.
    async def mock_execute(*args, **kwargs):
        raise aiosqlite.Error("Mock DB Error")

    mocker.patch.object(sqlite_storage, "_persist_worker_event", side_effect=mock_execute)
    await sqlite_storage.log_worker_event({"worker_id": "test-worker"})
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_get_job_history_fails(sqlite_storage: SQLiteHistoryStorage, mocker, caplog):
    # This method uses 'async with', so we need to mock the async context manager.
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__.side_effect = aiosqlite.Error("Mock DB Error")
    mocker.patch.object(sqlite_storage._conn, "execute", return_value=mock_cursor)

    history = await sqlite_storage.get_job_history("test-job")
    assert history == []
    assert "Failed to get job history" in caplog.text


@pytest.mark.asyncio
async def test_get_jobs_fails(sqlite_storage: SQLiteHistoryStorage, mocker, caplog):
    # This method uses 'async with'.
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__.side_effect = aiosqlite.Error("Mock DB Error")
    mocker.patch.object(sqlite_storage._conn, "execute", return_value=mock_cursor)

    jobs = await sqlite_storage.get_jobs()
    assert jobs == []
    assert "Failed to get jobs" in caplog.text


@pytest.mark.asyncio
async def test_get_job_summary_fails(sqlite_storage: SQLiteHistoryStorage, mocker, caplog):
    # This method uses 'async with'.
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__.side_effect = aiosqlite.Error("Mock DB Error")
    mocker.patch.object(sqlite_storage._conn, "execute", return_value=mock_cursor)

    summary = await sqlite_storage.get_job_summary()
    assert summary == {}
    assert "Failed to get job summary" in caplog.text


@pytest.mark.asyncio
async def test_get_worker_history_fails(sqlite_storage: SQLiteHistoryStorage, mocker, caplog):
    # This method uses 'async with'.
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__.side_effect = aiosqlite.Error("Mock DB Error")
    mocker.patch.object(sqlite_storage._conn, "execute", return_value=mock_cursor)

    history = await sqlite_storage.get_worker_history("test-worker", 1)
    assert history == []
    assert "Failed to get worker history" in caplog.text


@pytest.mark.asyncio
async def test_get_worker_history_empty(sqlite_storage: SQLiteHistoryStorage):
    history = await sqlite_storage.get_worker_history("non-existent-worker", 1)
    assert history == []


@pytest.mark.asyncio
async def test_get_jobs_empty(sqlite_storage: SQLiteHistoryStorage):
    jobs = await sqlite_storage.get_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_get_job_summary_empty(sqlite_storage: SQLiteHistoryStorage):
    summary = await sqlite_storage.get_job_summary()
    assert summary == {}


@pytest_asyncio.fixture
async def redis_client():
    client = FakeRedis()
    yield client
    await client.aclose()


# --- Integration Test (Variant B) ---

# A simple blueprint for the test
test_bp = StateMachineBlueprint("test_bp")


@test_bp.handler_for("start", is_start=True)
async def start_handler(context, actions):
    actions.transition_to("step_one")


@test_bp.handler_for("step_one")
async def step_one_handler(context, actions):
    # Use a dispatch action to test that event type
    actions.dispatch_task("some_task", params={}, transitions={"success": "finished"})


@test_bp.handler_for("finished")
async def finished_handler(context, actions):
    pass  # Terminal state


test_bp.validate()


async def test_executor_logs_history_integration(sqlite_storage: SQLiteHistoryStorage, redis_client: FakeRedis):
    """Tests the integration between JobExecutor and HistoryStorage, mocking
    other dependencies.
    """
    # 1. Setup dependencies
    job_storage = RedisStorage(redis_client)

    mock_engine = MagicMock()
    mock_engine.config = Config()
    mock_engine.blueprints = {"test_bp": test_bp}

    # The dispatcher needs to be an async mock to be awaitable
    mock_dispatcher = AsyncMock()
    mock_engine.dispatcher = mock_dispatcher

    # 2. Instantiate the real JobExecutor with real history and fake storage
    executor = JobExecutor(mock_engine, sqlite_storage)
    executor.storage = job_storage  # Manually set the storage
    executor.dispatcher = mock_dispatcher

    # 3. Create and enqueue a job
    job_id = str(uuid.uuid4())
    job_state = {
        "id": job_id,
        "blueprint_name": "test_bp",
        "current_state": "start",
        "initial_data": {},
        "state_history": {},
        "status": "pending",
    }
    await job_storage.save_job_state(job_id, job_state)
    await job_storage.enqueue_job(job_id)

    # 4. Manually drive the executor loop until the job is done
    # We'll process a few steps to cover the whole flow.
    for _ in range(3):  # start -> step_one -> dispatch -> (we stop here)
        result = await job_storage.dequeue_job()
        if result:
            queued_job_id, message_id = result
            assert queued_job_id == job_id
            await executor._process_job(queued_job_id, message_id)
        else:
            # If nothing is in the queue, the job might be waiting for a worker
            # which is fine for this test.
            break

    await asyncio.sleep(0.1)  # Wait for async logs

    # 5. Assert the history
    history = await sqlite_storage.get_job_history(job_id)

    # There should be 4 events:
    # 1. 'start' state begins
    # 2. 'start' state finishes (transitions to step_one)
    # 3. 'step_one' state begins
    # 4. 'step_one' state dispatches a task (which is its terminal action)
    assert len(history) == 4

    event_types = [e["event_type"] for e in history]
    states = [e["state"] for e in history]

    assert event_types == [
        "state_started",
        "state_finished",
        "state_started",
        "task_dispatched",
    ]
    assert states == ["start", "start", "step_one", "step_one"]

    # Check transition details for the first transition
    assert history[1]["previous_state"] == "start"
    assert history[1]["next_state"] == "step_one"

    # Check dispatch details
    assert history[3]["event_type"] == "task_dispatched"

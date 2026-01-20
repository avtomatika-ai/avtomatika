import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from src.avtomatika.history.postgres import PostgresHistoryStorage


class PostgresHistoryStorageImpl(PostgresHistoryStorage):
    """Subclass to allow mocking `create_pool` without connecting to real DB."""

    async def initialize(self):
        # We override initialize to avoid real connection attempts
        # and just set the pool mock.
        pass


@pytest_asyncio.fixture
async def postgres_storage(mocker):
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()
    mock_conn = AsyncMock()
    # Setup acquire context manager
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    storage = PostgresHistoryStorageImpl("postgresql://...")
    storage._pool = mock_pool

    # Start the background worker
    await storage.start()

    yield storage, mock_conn, mock_pool, mocker

    await storage.close()


@pytest.mark.asyncio
async def test_initialize_success(mocker):
    # This tests the REAL initialize logic, so we need a fresh instance
    # that doesn't use the Impl override, but we must mock create_pool
    mock_create_pool = mocker.patch("src.avtomatika.history.postgres.create_pool", new_callable=AsyncMock)
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_create_pool.return_value = mock_pool

    storage = PostgresHistoryStorage("postgresql://...")
    await storage.initialize()

    mock_create_pool.assert_awaited_once()
    # Check that init sql was executed
    assert mock_conn.execute.call_count >= 3


@pytest.mark.asyncio
async def test_log_job_event(postgres_storage):
    storage, mock_conn, _, _ = postgres_storage
    # No need to call initialize here as we use Impl with pre-set pool

    mock_conn.reset_mock()
    await storage.log_job_event({"job_id": "test-job"})

    # Wait for async worker
    await asyncio.sleep(0.1)

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]
    assert "INSERT INTO job_history" in args[0]


@pytest.mark.asyncio
async def test_log_worker_event(postgres_storage):
    storage, mock_conn, _, _ = postgres_storage

    mock_conn.reset_mock()
    await storage.log_worker_event({"worker_id": "test-worker"})

    # Wait for async worker
    await asyncio.sleep(0.1)

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]
    assert "INSERT INTO worker_history" in args[0]

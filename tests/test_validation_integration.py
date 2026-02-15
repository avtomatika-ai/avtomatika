from unittest.mock import AsyncMock, MagicMock

import pytest

from avtomatika.config import Config
from avtomatika.services.worker_service import WorkerService


@pytest.mark.asyncio
async def test_register_worker_invalid_id():
    """Test that registering a worker with an invalid ID raises ValueError."""
    storage = AsyncMock()
    history = AsyncMock()
    config = Config()
    engine = MagicMock()
    engine.app = {}  # Mock app dictionary for S3 service lookup

    service = WorkerService(storage, history, config, engine)

    invalid_payload = {"worker_id": "bad/worker/id", "worker_type": "cpu", "supported_skills": []}

    with pytest.raises(ValueError, match="Invalid worker_id"):
        await service.register_worker(invalid_payload)

    # Ensure storage was not called
    storage.register_worker.assert_not_called()


@pytest.mark.asyncio
async def test_register_worker_valid_id():
    """Test that registering a worker with a valid ID succeeds."""
    storage = AsyncMock()
    history = AsyncMock()
    config = Config()
    engine = MagicMock()
    engine.app = {}

    service = WorkerService(storage, history, config, engine)

    valid_payload = {"worker_id": "good-worker-id_123", "worker_type": "cpu", "supported_skills": []}

    # Should not raise exception
    await service.register_worker(valid_payload)

    # Ensure storage was called correctly
    storage.register_worker.assert_called_once()
    args, _ = storage.register_worker.call_args
    assert args[0] == "good-worker-id_123"

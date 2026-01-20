from unittest.mock import AsyncMock, MagicMock

import pytest
from src.avtomatika.reputation import ReputationCalculator


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.storage = AsyncMock()
    engine.history_storage = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_reputation_calculation_logic(mock_engine):
    """Tests the basic logic of reputation calculation."""
    calculator = ReputationCalculator(mock_engine)

    # 1. Configure mocks
    mock_engine.storage.get_active_worker_ids = AsyncMock(return_value=["worker-1"])

    # History: 3 successful tasks, 1 failed
    mock_history = [
        {
            "event_type": "task_finished",
            "context_snapshot": {"result": {"status": "success"}},
        },
        {
            "event_type": "task_finished",
            "context_snapshot": {"result": {"status": "success"}},
        },
        {
            "event_type": "task_finished",
            "context_snapshot": {"result": {"status": "failure"}},
        },
        {
            "event_type": "task_finished",
            "context_snapshot": {"result": {"status": "success"}},
        },
        {"event_type": "state_started"},  # This event should be ignored
    ]
    mock_engine.history_storage.get_worker_history.return_value = mock_history

    # 2. Perform calculation
    calculator._running = True
    await calculator.calculate_all_reputations()

    # 3. Check the result
    # Expected reputation = 3 / 4 = 0.75
    mock_engine.storage.update_worker_data.assert_called_once_with(
        "worker-1",
        {"reputation": 0.75},
    )


@pytest.mark.asyncio
async def test_reputation_no_history(mock_engine):
    """Tests the case where a worker has no task history.
    The reputation should not change.
    """
    calculator = ReputationCalculator(mock_engine)
    mock_workers = [{"worker_id": "worker-1", "reputation": 1.0}]
    mock_engine.storage.get_available_workers.return_value = mock_workers
    mock_engine.history_storage.get_worker_history.return_value = []

    await calculator.calculate_all_reputations()

    # The update method should not be called
    mock_engine.storage.update_worker_data.assert_not_called()


@pytest.mark.asyncio
async def test_reputation_division_by_zero(mock_engine):
    """Tests the case where a worker has a history, but no task completion events
    (to avoid division by zero).
    """
    calculator = ReputationCalculator(mock_engine)
    mock_workers = [{"worker_id": "worker-1", "reputation": 1.0}]
    mock_engine.storage.get_available_workers.return_value = mock_workers
    mock_engine.history_storage.get_worker_history.return_value = [
        {"event_type": "state_started"},
    ]

    await calculator.calculate_all_reputations()

    # The update method should not be called
    mock_engine.storage.update_worker_data.assert_not_called()

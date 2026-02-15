from unittest.mock import AsyncMock, MagicMock

import pytest
from src.avtomatika.dispatcher import Dispatcher


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    return storage


@pytest.fixture
def dispatcher(mock_storage):
    config = MagicMock()
    return Dispatcher(mock_storage, config)


@pytest.mark.asyncio
async def test_dispatcher_cheapest_strategy(dispatcher, mock_storage):
    """Verifies that the 'cheapest' strategy selects the worker with lowest cost_per_second."""
    workers = [
        {"worker_id": "expensive", "status": "idle", "supported_skills": ["task"], "cost_per_second": 0.5},
        {"worker_id": "cheap", "status": "idle", "supported_skills": ["task"], "cost_per_second": 0.1},
        {"worker_id": "medium", "status": "idle", "supported_skills": ["task"], "cost_per_second": 0.3},
    ]
    # Update for O(1) dispatcher
    mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
    mock_storage.get_workers.return_value = workers

    job_state = {"id": "job-1"}
    task_info = {"type": "task", "dispatch_strategy": "cheapest"}

    await dispatcher.dispatch(job_state, task_info)

    # Check that it was enqueued for the 'cheap' worker
    mock_storage.enqueue_task_for_worker.assert_called_once()
    args = mock_storage.enqueue_task_for_worker.call_args[0]
    assert args[0] == "cheap"


@pytest.mark.asyncio
async def test_dispatcher_best_value_strategy(dispatcher, mock_storage):
    """Verifies that 'best_value' strategy considers both cost and reputation (cost / reputation)."""
    workers = [
        # score = 0.2 / 0.5 = 0.4
        {
            "worker_id": "mid_reliability",
            "status": "idle",
            "supported_skills": ["task"],
            "cost_per_second": 0.2,
            "reputation": 0.5,
        },
        # score = 0.5 / 1.0 = 0.5
        {
            "worker_id": "high_cost_perfect",
            "status": "idle",
            "supported_skills": ["task"],
            "cost_per_second": 0.5,
            "reputation": 1.0,
        },
        # score = 0.1 / 0.8 = 0.125 (Best)
        {
            "worker_id": "cheap_reliable",
            "status": "idle",
            "supported_skills": ["task"],
            "cost_per_second": 0.1,
            "reputation": 0.8,
        },
    ]
    mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
    mock_storage.get_workers.return_value = workers

    job_state = {"id": "job-1"}
    task_info = {"type": "task", "dispatch_strategy": "best_value"}

    await dispatcher.dispatch(job_state, task_info)

    args = mock_storage.enqueue_task_for_worker.call_args[0]
    assert args[0] == "cheap_reliable"


@pytest.mark.asyncio
async def test_dispatcher_max_cost_filtering(dispatcher, mock_storage):
    """Verifies that workers exceeding max_cost are filtered out."""
    workers = [
        {"worker_id": "too_expensive", "status": "idle", "supported_skills": ["task"], "cost_per_second": 1.0},
        {"worker_id": "just_right", "status": "idle", "supported_skills": ["task"], "cost_per_second": 0.05},
    ]
    mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
    mock_storage.get_workers.return_value = workers

    job_state = {"id": "job-1"}
    task_info = {"type": "task", "max_cost": 0.1}

    await dispatcher.dispatch(job_state, task_info)

    args = mock_storage.enqueue_task_for_worker.call_args[0]
    assert args[0] == "just_right"

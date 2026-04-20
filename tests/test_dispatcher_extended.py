# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


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
    # Ensure reputation filtering doesn't interfere by default
    config.REPUTATION_MIN_THRESHOLD = 0.0
    return Dispatcher(mock_storage, config)


@pytest.mark.asyncio
async def test_dispatcher_cheapest_strategy(dispatcher, mock_storage):
    """Verifies that the 'cheapest' strategy selects the worker with lowest cost_per_second."""
    workers = [
        {
            "worker_id": "expensive",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.5}},
        },
        {
            "worker_id": "cheap",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.1}},
        },
        {
            "worker_id": "medium",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.3}},
        },
    ]
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
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.2}},
            "reputation": 0.5,
        },
        # score = 0.5 / 1.0 = 0.5
        {
            "worker_id": "high_cost_perfect",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.5}},
            "reputation": 1.0,
        },
        # score = 0.1 / 0.8 = 0.125 (Best)
        {
            "worker_id": "cheap_reliable",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.1}},
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
        {
            "worker_id": "too_expensive",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 1.0}},
        },
        {
            "worker_id": "just_right",
            "status": "idle",
            "supported_skills": [{"name": "task"}],
            "capabilities": {"cost_per_skill": {"task": 0.05}},
        },
    ]
    mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
    mock_storage.get_workers.return_value = workers

    job_state = {"id": "job-1"}
    task_info = {"type": "task", "max_cost": 0.1}

    await dispatcher.dispatch(job_state, task_info)

    args = mock_storage.enqueue_task_for_worker.call_args[0]
    assert args[0] == "just_right"


@pytest.mark.asyncio
async def test_dispatcher_ram_filtering(dispatcher, mock_storage):
    """Tests that the dispatcher correctly filters workers based on RAM requirements."""
    workers = [
        {
            "worker_id": "low_ram",
            "supported_skills": [{"name": "task"}],
            "resources": {"properties": {"ram_gb": 8.0}},
            "status": "idle",
        },
        {
            "worker_id": "high_ram",
            "supported_skills": [{"name": "task"}],
            "resources": {"properties": {"ram_gb": 64.0}},
            "status": "idle",
        },
    ]

    mock_storage.find_workers_by_hot_skill.return_value = []
    mock_storage.find_workers_for_skill.return_value = ["low_ram", "high_ram"]
    mock_storage.get_workers.return_value = workers

    job_state = {"id": "job-1"}
    task_info = {
        "type": "task",
        "resource_requirements": {"resources": {"properties": {"ram_gb": 32.0}}},
    }
    await dispatcher.dispatch(job_state, task_info)

    args = mock_storage.enqueue_task_for_worker.call_args[0]
    assert args[0] == "high_ram"

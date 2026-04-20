# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


import pytest

from avtomatika.config import Config
from avtomatika.dispatcher import Dispatcher
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def setup_env():
    storage = MemoryStorage()
    config = Config()
    config.DISPATCHER_SOFT_LIMIT = 2
    dispatcher = Dispatcher(storage, config)
    return storage, config, dispatcher


@pytest.mark.asyncio
async def test_overflow_strategy(setup_env):
    storage, config, dispatcher = setup_env

    worker_cheap = {
        "worker_id": "cheap_w",
        "supported_skills": [{"name": "test_task"}],
        "capabilities": {"cost_per_skill": {"test_task": 10}},
        "status": "idle",
    }
    worker_expensive = {
        "worker_id": "expensive_w",
        "supported_skills": [{"name": "test_task"}],
        "capabilities": {"cost_per_skill": {"test_task": 50}},
        "status": "idle",
    }

    await storage.register_worker("cheap_w", worker_cheap, 60)
    await storage.register_worker("expensive_w", worker_expensive, 60)

    job_state = {"id": "job1", "blueprint_name": "test_bp"}
    task_info = {"type": "test_task", "dispatch_strategy": "overflow", "params": {}}

    # Task 1: Should go to cheap_w (queue_len 0 < 2)
    await dispatcher.dispatch(job_state, task_info)
    assert job_state["task_worker_id"] == "cheap_w"

    # Task 2: Should go to cheap_w (queue_len 1 < 2)
    job_state["id"] = "job2"
    await dispatcher.dispatch(job_state, task_info)
    assert job_state["task_worker_id"] == "cheap_w"

    # Task 3: cheap_w queue is now 2 (reaches SOFT_LIMIT). Should overflow to expensive_w.
    job_state["id"] = "job3"
    await dispatcher.dispatch(job_state, task_info)
    assert job_state["task_worker_id"] == "expensive_w"


@pytest.mark.asyncio
async def test_work_stealing_memory(setup_env):
    storage, config, dispatcher = setup_env

    w1_info = {"worker_id": "w1", "supported_skills": [{"name": "steal_me"}], "status": "idle"}
    w2_info = {"worker_id": "w2", "supported_skills": [{"name": "steal_me"}], "status": "idle"}
    await storage.register_worker("w1", w1_info, 60)
    await storage.register_worker("w2", w2_info, 60)

    payload = {"job_id": "j1", "type": "steal_me"}
    await storage.enqueue_task_for_worker("w1", payload, 1.0)

    # It should steal from w1
    stolen_task = await storage.dequeue_task_for_worker("w2", timeout=1)

    assert stolen_task is not None
    assert stolen_task["job_id"] == "j1"

    w1_task = await storage.dequeue_task_for_worker("w1", timeout=1)
    assert w1_task is None


@pytest.mark.asyncio
async def test_null_safety_hot_skills(setup_env):
    storage, config, dispatcher = setup_env

    # Worker with hot_skills as None
    worker_info = {
        "worker_id": "w_null",
        "supported_skills": [{"name": "test_task"}],
        "hot_skills": None,
        "status": "idle",
    }
    await storage.register_worker("w_null", worker_info, 60)

    job_state = {"id": "job_null", "blueprint_name": "test_bp"}
    task_info = {"type": "test_task", "params": {}}

    # Should not crash
    await dispatcher.dispatch(job_state, task_info)
    assert job_state["task_worker_id"] == "w_null"

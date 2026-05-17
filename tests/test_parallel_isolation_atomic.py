# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import msgpack
import pytest
import pytest_asyncio
from fakeredis import aioredis as redis

from avtomatika.blueprint import Blueprint
from avtomatika.config import Config
from avtomatika.constants import (
    JOB_STATUS_WAITING_FOR_PARALLEL,
    JOB_STATUS_WAITING_FOR_WORKER,
)
from avtomatika.dispatcher import Dispatcher
from avtomatika.engine import OrchestratorEngine
from avtomatika.executor import JobExecutor
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.redis import RedisStorage
from avtomatika.utils.webhook_sender import WebhookSender
from avtomatika.ws_manager import WebSocketManager


@pytest_asyncio.fixture
async def redis_storage():
    client = redis.FakeRedis(decode_responses=False)
    storage = RedisStorage(client)
    yield storage
    await client.aclose()


@pytest.fixture
def config():
    conf = Config()
    conf.JOB_MAX_RETRIES = 2
    return conf


@pytest_asyncio.fixture
async def engine(redis_storage, config):
    engine = OrchestratorEngine(redis_storage, config)

    # Initialize required components that engine expected
    engine.dispatcher = MagicMock(spec=Dispatcher)
    engine.worker_service = MagicMock(spec=WorkerService)

    async def mock_dispatch(job_state, task_info):
        # Dispatcher.dispatch is expected to return the updated state from storage
        # to prevent 'lost updates'.
        job_id = job_state["id"]
        tid = task_info.get("task_id")
        active_branches = job_state.get("active_branches") or []
        is_parallel = tid in active_branches

        # Reload latest from storage to simulate real environment
        latest_state = await redis_storage.get_job_state(job_id) or job_state

        # Use provided worker if possible, or fallback to mock
        worker_id = task_info.get("worker_id") or "mock-worker"

        if is_parallel:
            latest_state.setdefault("branch_worker_ids", {})[tid] = worker_id
            latest_state.setdefault("parallel_tasks_info", {})[tid] = task_info
            # IMPORTANT: Do NOT change overall status for parallel branches
            latest_state["status"] = JOB_STATUS_WAITING_FOR_PARALLEL
        else:
            latest_state["assigned_worker_id"] = worker_id
            latest_state["status"] = JOB_STATUS_WAITING_FOR_WORKER

        await redis_storage.save_job_state(job_id, latest_state)
        return latest_state

    engine.dispatcher.dispatch = AsyncMock(side_effect=mock_dispatch)
    engine.ws_manager = WebSocketManager(engine)
    engine.webhook_sender = WebhookSender(AsyncMock())
    engine.app = MagicMock()  # Mock aiohttp App

    # Simple parallel blueprint
    parallel_bp = Blueprint(name="parallel_bp")

    @parallel_bp.handler("start", is_start=True)
    async def start(actions):
        actions.dispatch_parallel(
            tasks=[{"type": "task_a", "params": {"id": 1}}, {"type": "task_b", "params": {"id": 2}}],
            aggregate_into="aggregator",
        )

    @parallel_bp.aggregator("aggregator")
    async def aggregator(aggregation_results, actions):
        actions.go_to("end")

    @parallel_bp.handler("end", is_end=True)
    async def end():
        pass

    parallel_bp.validate()
    engine.register_blueprint(parallel_bp)

    # Mock history
    engine.history_storage = MagicMock()
    engine.history_storage.log_job_event = AsyncMock()
    engine.history_storage.log_worker_event = AsyncMock()

    # We do NOT call on_startup to avoid background task complexities
    # But we need to initialize storage manually if needed
    await redis_storage.initialize()

    yield engine


@pytest.mark.asyncio
async def test_parallel_branch_isolation_happy_path(engine, redis_storage):
    """
    Test that parallel branches have isolated state (worker IDs, timestamps).
    """
    executor = JobExecutor(engine, engine.history_storage)
    job_id = "parallel-iso-job"

    # 1. Start job
    await redis_storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "parallel_bp",
            "current_state": "start",
            "status": "running",
            "initial_data": {},
        },
    )

    await executor._process_job(job_id, "msg-1")

    state = await redis_storage.get_job_state(job_id)
    assert state["status"] == JOB_STATUS_WAITING_FOR_PARALLEL
    active_branches = state["active_branches"]
    assert len(active_branches) == 2

    # 2. Simulate worker service processing results for both branches
    worker_service = WorkerService(redis_storage, engine.history_storage, engine.config, engine)

    # Manually update branch_worker_ids in storage to match our expected workers
    # otherwise WorkerService will complain about hijacking (assigned to 'mock-worker')
    tid_a = active_branches[0]
    tid_b = active_branches[1]

    state["branch_worker_ids"] = {tid_a: "worker-a", tid_b: "worker-b"}
    await redis_storage.save_job_state(job_id, state)

    # Branch 1 (Task A)
    tid_a = active_branches[0]
    await worker_service.process_task_result(
        {
            "job_id": job_id,
            "task_id": tid_a,
            "worker_id": "worker-a",
            "status": "success",
            "data": {"res": "A"},
            "timestamp": time.time(),
            "security": {"signature": "sig-a", "signer_id": "worker-a"},
        },
        authenticated_worker_id="worker-a",
    )

    # Branch 2 (Task B)
    tid_b = active_branches[1]
    await worker_service.process_task_result(
        {
            "job_id": job_id,
            "task_id": tid_b,
            "worker_id": "worker-b",
            "status": "success",
            "data": {"res": "B"},
            "timestamp": time.time(),
            "security": {"signature": "sig-b", "signer_id": "worker-b"},
        },
        authenticated_worker_id="worker-b",
    )

    # 3. Verify Isolated state in Redis
    final_state = await redis_storage.get_job_state(job_id)

    # Check isolated worker IDs
    branch_workers = final_state.get("branch_worker_ids", {})
    assert branch_workers[tid_a] == "worker-a"
    assert branch_workers[tid_b] == "worker-b"

    # Check aggregation results
    agg_res = final_state.get("aggregation_results", {})
    assert agg_res[tid_a]["data"]["res"] == "A"
    assert agg_res[tid_b]["data"]["res"] == "B"

    # Check that main job was re-enqueued for aggregation
    assert await redis_storage.get_job_queue_length() > 0


@pytest.mark.asyncio
async def test_atomic_aggregation_concurrency(engine, redis_storage):
    """
    Stress test: Simulate simultaneous results for multiple branches to ensure no lost updates.
    """
    job_id = "stress-job"
    num_branches = 20
    branch_ids = [f"tid-{i}" for i in range(num_branches)]

    # Manually setup a state waiting for 20 branches
    await redis_storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "parallel_bp",
            "status": JOB_STATUS_WAITING_FOR_PARALLEL,
            "active_branches": branch_ids,
            "aggregation_results": {},
            "branch_worker_ids": {},
            "current_state": "aggregator",
            "aggregation_target": "aggregator",
        },
    )

    worker_service = WorkerService(redis_storage, engine.history_storage, engine.config, engine)

    async def send_result(tid, i):
        await worker_service.process_task_result(
            {
                "job_id": job_id,
                "task_id": tid,
                "worker_id": f"worker-{i}",
                "status": "success",
                "data": {"val": i},
                "timestamp": time.time(),
                "security": {"signature": "s", "signer_id": f"worker-{i}"},
            },
            authenticated_worker_id=f"worker-{i}",
        )

    # Launch all concurrently
    await asyncio.gather(*[send_result(tid, i) for i, tid in enumerate(branch_ids)])

    # Verify all results are present
    final_state = await redis_storage.get_job_state(job_id)
    results = final_state.get("aggregation_results", {})
    assert len(results) == num_branches
    for i in range(num_branches):
        tid = branch_ids[i]
        assert results[tid]["data"]["val"] == i


@pytest.mark.asyncio
async def test_independent_branch_timeouts(engine, redis_storage):
    """
    Verify that Watcher can timeout branches independently.
    """
    job_id = "timeout-job"
    tid_1 = "t1"
    tid_2 = "t2"

    await redis_storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "parallel_bp",
            "status": JOB_STATUS_WAITING_FOR_PARALLEL,
            "active_branches": [tid_1, tid_2],
            "parallel_tasks_info": {tid_1: {"type": "a"}, tid_2: {"type": "b"}},
        },
    )

    # Expire only t1
    now = time.time()
    await redis_storage.add_job_to_watch(f"{job_id}:{tid_1}", now - 10)
    await redis_storage.add_job_to_watch(f"{job_id}:{tid_2}", now + 100)

    # Trigger one watcher cycle
    # We mock acquire_lock to always succeed for the test
    with patch.object(redis_storage, "acquire_lock", AsyncMock(return_value=True)):
        timed_out = await redis_storage.get_timed_out_jobs()
        assert len(timed_out) == 1
        assert timed_out[0] == f"{job_id}:{tid_1}"

        # Manually process the timeout like the watcher would
        jid, tid = timed_out[0].split(":", 1)
        job_state = await redis_storage.get_job_state(jid)

        with patch.object(engine, "handle_task_failure", AsyncMock()) as mock_fail:
            if ":" in timed_out[0]:
                await engine.handle_task_failure(job_state, tid, "Timeout")

            mock_fail.assert_called_with(job_state, tid, "Timeout")


@pytest.mark.asyncio
async def test_data_normalization_regression(redis_storage):
    """
    Test RedisStorage._normalize_unpacked with complex nested cmsgpack-like bytes.
    """
    inner_data = {"id": "123", "status": "ok"}
    packed_inner = msgpack.packb(inner_data)

    raw_redis_data = {b"job_id": b"job-1", b"payload": packed_inner, b"list": [b"item1", packed_inner]}

    normalized = RedisStorage._normalize_unpacked(raw_redis_data)

    assert normalized["job_id"] == "job-1"
    assert normalized["payload"] == inner_data
    assert normalized["list"][0] == "item1"
    assert normalized["list"][1] == inner_data
    assert isinstance(normalized["job_id"], str)


@pytest.mark.asyncio
async def test_token_patterns_matching(redis_storage):
    """
    Test find_worker_token with various patterns.
    """
    # 1. Direct match
    await redis_storage.set_worker_token("worker-1", "token-1")
    # 2. Pattern match
    await redis_storage.set_str("orchestrator:worker:token:pattern:cpu-worker-*", "token-cpu")
    await redis_storage.set_str("orchestrator:worker:token:pattern:gpu-node-??", "token-gpu")

    # Tests
    assert await redis_storage.find_worker_token("worker-1") == "token-1"
    assert await redis_storage.find_worker_token("cpu-worker-01") == "token-cpu"
    assert await redis_storage.find_worker_token("cpu-worker-large") == "token-cpu"
    assert await redis_storage.find_worker_token("gpu-node-aa") == "token-gpu"
    assert await redis_storage.find_worker_token("gpu-node-aaa") is None  # Too long for ??
    assert await redis_storage.find_worker_token("unknown") is None


@pytest.mark.asyncio
async def test_per_branch_retry_limit(engine, redis_storage):
    """
    Test that each branch tracks its own retries and fails independently.
    """
    job_id = "retry-job"
    tid_1 = "t1"
    tid_2 = "t2"

    task_info = {"type": "test", "transitions": {"success": "ok", "failure": "fail"}}

    await redis_storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "parallel_bp",
            "status": JOB_STATUS_WAITING_FOR_PARALLEL,
            "active_branches": [tid_1, tid_2],
            "parallel_tasks_info": {tid_1: {**task_info, "task_id": tid_1}, tid_2: {**task_info, "task_id": tid_2}},
            "retry_count_t1": 0,
            "retry_count_t2": 0,
        },
    )

    # Fail t1 twice (limit is 2)
    state = await redis_storage.get_job_state(job_id)
    await engine.handle_task_failure(state, tid_1, "Error 1")

    state = await redis_storage.get_job_state(job_id)
    assert state["retry_count_t1"] == 1
    assert state["status"] == JOB_STATUS_WAITING_FOR_PARALLEL  # Overall job still waiting

    await engine.handle_task_failure(state, tid_1, "Error 2")
    state = await redis_storage.get_job_state(job_id)
    assert state["retry_count_t1"] == 2

    # 3rd failure should move job to quarantine
    await engine.handle_task_failure(state, tid_1, "Error 3")
    state = await redis_storage.get_job_state(job_id)
    assert state["status"] == "quarantined"
    assert "Task t1 failed after 3 attempts" in state["error_message"]


@pytest.mark.asyncio
async def test_lost_update_prevention_parallel_dispatch(engine, redis_storage):
    """
    Verify that dispatching multiple parallel tasks doesn't lose 'parallel_tasks_info'.
    This tests the fix in executor.py where job_state is updated from dispatcher.
    """
    executor = JobExecutor(engine, engine.history_storage)
    job_id = "lost-update-job"

    # Prepare start state
    await redis_storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "parallel_bp",
            "current_state": "start",
            "status": "running",
            "initial_data": {},
        },
    )

    # This will trigger _handle_parallel_dispatch
    await executor._process_job(job_id, "msg-1")

    state = await redis_storage.get_job_state(job_id)
    # Check that BOTH tasks are in parallel_tasks_info
    info = state.get("parallel_tasks_info", {})
    assert len(info) == 2
    assert len(state["active_branches"]) == 2
    for tid in state["active_branches"]:
        assert tid in info

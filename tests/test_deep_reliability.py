# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import ANY, patch

import pytest

from avtomatika.blueprint import Blueprint
from avtomatika.constants import (
    AUTH_HEADER_WORKER,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_WAITING_FOR_WORKER,
)
from tests.conftest import STORAGE_KEY


@pytest.mark.asyncio
async def test_job_hijacking_prevention(aiohttp_client, app):
    """
    Security: Verify that worker B cannot submit results for a task assigned to worker A.
    """
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    job_id = "hijack-job"
    task_id = "task-1"
    worker_a = "worker-a"
    worker_b = "worker-b"

    # 1. Setup job state assigned to worker_a
    job_state = {
        "id": job_id,
        "blueprint_name": "test_bp",
        "current_state": "step1",
        "status": JOB_STATUS_WAITING_FOR_WORKER,
        "current_task_id": task_id,
        "task_worker_id": worker_a,
        "current_task_transitions": {"success": "done"},
    }
    await storage.save_job_state(job_id, job_state)

    # 2. Worker B tries to submit result
    result_payload = {
        "job_id": job_id,
        "task_id": task_id,
        "worker_id": worker_b,
        "status": "success",
        "data": {"foo": "bar"},
    }

    headers = {AUTH_HEADER_WORKER: "secure-worker-token"}  # Using global token for simplicity
    resp = await client.post("/_worker/tasks/result", json=result_payload, headers=headers)

    # Authenticated worker_id will be "unknown_authenticated_by_global_token" in this test setup
    # unless we use individual tokens. Let's use individual tokens to be precise.

    # Setup individual tokens (store hashes)
    from hashlib import sha256

    await storage.set_worker_token(worker_a, sha256(b"token-a").hexdigest())
    await storage.set_worker_token(worker_b, sha256(b"token-b").hexdigest())

    # Real attempt by worker_b
    headers_b = {AUTH_HEADER_WORKER: "token-b"}
    resp = await client.post("/_worker/tasks/result", json=result_payload, headers=headers_b)

    assert resp.status == 200  # API returns 200, but the result is IGNORED internally
    data = await resp.json()
    assert data["status"] == "ignored"
    from avtomatika.constants import IGNORED_REASON_MISMATCH

    assert data["reason"] == IGNORED_REASON_MISMATCH

    # Verify job state remains unchanged (still waiting for worker_a)
    final_state = await storage.get_job_state(job_id)
    assert final_state["task_worker_id"] == worker_a
    assert final_state["status"] == JOB_STATUS_WAITING_FOR_WORKER


@pytest.mark.asyncio
async def test_infinite_loop_prevention(config, redis_storage):
    """
    Reliability: Verify that a blueprint with an infinite loop is terminated.
    """
    from avtomatika.engine import OrchestratorEngine
    from avtomatika.executor import JobExecutor

    # Create a looping blueprint
    loop_bp = Blueprint(name="loop_bp")

    @loop_bp.handler("start", is_start=True)
    async def start_node(actions):
        actions.go_to("loop")

    @loop_bp.handler("loop")
    async def loop_node(actions):
        actions.go_to("loop")  # Loop back to itself

    engine = OrchestratorEngine(redis_storage, config)
    engine.register_blueprint(loop_bp)
    engine.setup()
    await engine.on_startup(engine.app)

    try:
        # Set a very small limit for testing
        config.MAX_TRANSITIONS_PER_JOB = 5

        executor = JobExecutor(engine, engine.history_storage)

        job_id = "looping-job"
        await redis_storage.save_job_state(
            job_id,
            {
                "id": job_id,
                "blueprint_name": "loop_bp",
                "current_state": "start",
                "status": JOB_STATUS_RUNNING,
                "initial_data": {},
            },
        )
        await redis_storage.enqueue_job(job_id)

        # Process several times
        for _ in range(7):  # 1 (start->loop) + 5 (loop->loop) = 6. 7th should fail.
            result = await redis_storage.dequeue_job()
            if result:
                jid, mid = result
                await executor._process_job(jid, mid)
                await redis_storage.ack_job(mid)

        # Check final state
        final_state = await redis_storage.get_job_state(job_id)
        assert final_state["status"] == JOB_STATUS_FAILED
        assert "infinite loop" in final_state["error_message"].lower()
        assert final_state["transition_count"] == 5
    finally:
        await engine.on_shutdown(engine.app)


@pytest.mark.asyncio
async def test_stale_result_ignored(aiohttp_client, app):
    """
    Reliability: Verify that results for old tasks (stale) are ignored.
    """
    client = await aiohttp_client(app)
    storage = app[STORAGE_KEY]

    job_id = "stale-job"
    worker_id = "worker-1"
    headers = {AUTH_HEADER_WORKER: "secure-worker-token"}

    # 1. Setup job at task-2
    await storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "test_bp",
            "current_state": "step2",
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "current_task_id": "task-2",
            "task_worker_id": worker_id,
        },
    )

    # 2. Submit result for old task-1
    result_payload = {
        "job_id": job_id,
        "task_id": "task-1",  # Stale ID
        "worker_id": worker_id,
        "status": "success",
        "data": {},
    }

    resp = await client.post("/_worker/tasks/result", json=result_payload, headers=headers)
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ignored"
    from avtomatika.constants import IGNORED_REASON_STALE

    assert data["reason"] == IGNORED_REASON_STALE

    # Verify job state still at task-2
    final_state = await storage.get_job_state(job_id)
    assert final_state["current_task_id"] == "task-2"


@pytest.mark.asyncio
async def test_race_condition_protection(config, redis_storage):
    """
    Concurrency: Verify that parallel execution attempts are handled.
    In this project, we rely on XREADGROUP to prevent duplicate processing,
    but we also want to ensure that if a job IS somehow processed twice,
    one of them bails out early if the status is already terminal.
    """
    from avtomatika.engine import OrchestratorEngine
    from avtomatika.executor import JobExecutor

    engine = OrchestratorEngine(redis_storage, config)
    engine.setup()
    await engine.on_startup(engine.app)
    try:
        executor = JobExecutor(engine, engine.history_storage)

        job_id = "race-job"
        # Set to terminal state
        await redis_storage.save_job_state(job_id, {"id": job_id, "blueprint_name": "any", "status": "finished"})

        # Try to process
        with patch("avtomatika.executor.logger.warning") as mock_log:
            await executor._process_job(job_id, "some-msg-id")
            # Should have returned early because status is terminal
            mock_log.assert_called_with(ANY)
            assert "terminal state" in mock_log.call_args[0][0]
    finally:
        await engine.on_shutdown(engine.app)

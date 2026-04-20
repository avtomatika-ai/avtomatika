# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Dmitrii Gagarin aka madgagarin

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from avtomatika.config import Config
from avtomatika.constants import JOB_STATUS_WAITING_FOR_WORKER
from avtomatika.executor import JobExecutor
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    conf = Config()
    conf.GLOBAL_WORKER_TOKEN = "secure-b8-token"
    return conf


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def engine(storage, config):
    engine_mock = MagicMock()
    engine_mock.storage = storage
    engine_mock.config = config
    engine_mock.app = {}
    engine_mock.on_worker_event = []
    engine_mock.blueprint_contracts = {}
    return engine_mock


@pytest.fixture
def worker_service(storage, config, engine):
    history_mock = MagicMock()
    history_mock.log_worker_event = AsyncMock()
    history_mock.log_job_event = AsyncMock()
    return WorkerService(storage, history_mock, config, engine)


@pytest.mark.asyncio
async def test_b8_timestamp_replay_protection_fail(worker_service, config):
    """FAIL: Message timestamp is too old (Replay attempt)."""
    worker_id = "w1"
    old_timestamp = time.time() - 300  # 5 minutes ago

    payload = {
        "worker_id": worker_id,
        "timestamp": old_timestamp,
        "security": {"signature": "any", "signer_id": worker_id},
    }

    # This must fail because the timestamp is 300s old, exceeding 60s TTL
    with pytest.raises(PermissionError, match="Message timestamp expired"):
        await worker_service._verify_zero_trust(payload, worker_id, secret="some-secret")


@pytest.mark.asyncio
async def test_b8_task_result_worker_id_autofill(worker_service, storage):
    """SUCCESS: worker_id is missing in payload, Orchestrator fills it from Auth."""
    worker_id = "auth-w"
    job_id = "job-1"

    await storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "current_task_id": "t1",
            "task_worker_id": worker_id,
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "current_task_info": {"type": "test"},
        },
    )

    # Payload WITHOUT worker_id (Beta 8 optimization)
    result_payload = {
        "job_id": job_id,
        "task_id": "t1",
        "status": "success",
        "data": {"ok": True},
        "timestamp": time.time(),
        "security": {"signature": "sig", "signer_id": worker_id},
    }

    # Mock Zero Trust verification to bypass signature checks for this logic test
    with patch.object(worker_service, "_verify_zero_trust", AsyncMock()):
        await worker_service.process_task_result(result_payload, worker_id)

    worker_service.history_storage.log_job_event.assert_called()
    event = worker_service.history_storage.log_job_event.call_args[0][0]
    assert event["worker_id"] == worker_id


@pytest.mark.asyncio
async def test_b8_metadata_propagation(worker_service, storage):
    """SUCCESS: Metadata and timestamp from result are propagated to job state."""
    worker_id = "w1"
    job_id = "job-meta"
    ts = time.time()

    await storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "current_task_id": "t1",
            "task_worker_id": worker_id,
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "current_task_info": {"type": "test"},
            "current_task_transitions": {"success": "next"},
        },
    )

    result_payload = {
        "job_id": job_id,
        "task_id": "t1",
        "status": "success",
        "data": {"foo": "bar"},
        "metadata": {"source": "worker-local-sensor"},
        "timestamp": ts,
        "security": {"signature": "sig", "signer_id": worker_id},
    }

    with patch.object(worker_service, "_verify_zero_trust", AsyncMock()):
        await worker_service.process_task_result(result_payload, worker_id)

    job_state = await storage.get_job_state(job_id)
    assert job_state["metadata"]["source"] == "worker-local-sensor"
    assert job_state["timestamp"] == ts


@pytest.mark.asyncio
async def test_b8_executor_event_timestamps(engine, storage):
    """SUCCESS: JobExecutor adds timestamps to all generated events."""
    history = MagicMock()
    history.log_job_event = AsyncMock()

    executor = JobExecutor(engine, history)
    job_id = "test-ts"

    await storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "bp1",
            "current_state": "start",
            "status": "running",
            "initial_data": {},
            "client_config": {},
        },
    )

    mock_bp = MagicMock()
    mock_bp.start_state = "start"
    mock_bp.data_stores = {}
    mock_bp.end_states = ["end"]
    engine.blueprints = {"bp1": mock_bp}

    async def handler(actions):
        actions.go_to("end")

    mock_bp.find_handler.return_value = handler
    mock_bp.get_handler_params.return_value = ("actions",)

    await executor._process_job(job_id, "msg-1")

    # Check if events have timestamps
    assert history.log_job_event.call_count >= 1
    for call in history.log_job_event.call_args_list:
        event = call[0][0]
        assert "timestamp" in event
        assert isinstance(event["timestamp"], float)

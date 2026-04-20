# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Dmitrii Gagarin aka madgagarin

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from rxon.models import Heartbeat, TaskResult, WorkerEventPayload, WorkerRegistration
from rxon.security import sign_payload
from rxon.utils import to_dict

from avtomatika.config import Config
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    conf = Config()
    conf.GLOBAL_WORKER_TOKEN = "test-secret-key"
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
    return engine_mock


@pytest.fixture
def worker_service(storage, config, engine):
    history_mock = MagicMock()
    history_mock.log_worker_event = AsyncMock()
    history_mock.log_job_event = AsyncMock()
    return WorkerService(storage, history_mock, config, engine)


def sign_pure(data, secret, ignore=None):
    """Helper to sign data stripping None values like the new logic does."""

    def _strip(obj):
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, list):
            return [_strip(v) for v in obj if v is not None]
        return obj

    clean = _strip(to_dict(data))
    clean.pop("security", None)
    if ignore:
        for f in ignore:
            clean.pop(f, None)
    return sign_payload(clean, secret)


@pytest.mark.asyncio
async def test_register_worker_full_object_signature(worker_service, config):
    worker_id = "worker-1"
    timestamp = time.time()
    reg = WorkerRegistration(worker_id=worker_id, worker_type="test-worker", timestamp=timestamp)

    # Sign with our new 'pure' logic
    signature = sign_pure(reg, config.GLOBAL_WORKER_TOKEN)
    reg_dict = to_dict(reg)
    reg_dict["security"] = {"signature": signature, "signer_id": worker_id}

    await worker_service.register_worker(reg_dict, worker_id)
    assert await worker_service.storage.get_worker_info(worker_id) is not None


@pytest.mark.asyncio
async def test_process_task_result_full_object_signature(worker_service, config, storage):
    worker_id = "worker-1"
    job_id = "job-1"
    task_id = "task-1"
    timestamp = time.time()

    await storage.register_worker(worker_id, {"worker_id": worker_id}, 60)
    await storage.save_job_state(
        job_id, {"id": job_id, "current_task_id": task_id, "task_worker_id": worker_id, "status": "waiting_for_worker"}
    )

    result = TaskResult(
        job_id=job_id,
        task_id=task_id,
        worker_id=worker_id,
        status="success",
        data={"output": 42},
        metadata={"extra": "data"},  # Now covered by signature!
        timestamp=timestamp,
    )

    signature = sign_pure(result, config.GLOBAL_WORKER_TOKEN)
    res_dict = to_dict(result)
    res_dict["security"] = {"signature": signature, "signer_id": worker_id}

    resp = await worker_service.process_task_result(res_dict, worker_id)
    assert resp["status"] == "accepted"


@pytest.mark.asyncio
async def test_process_task_result_tamper_metadata(worker_service, config, storage):
    worker_id = "worker-1"
    job_id = "job-1"
    task_id = "task-1"
    timestamp = time.time()

    await storage.register_worker(worker_id, {"worker_id": worker_id}, 60)
    await storage.save_job_state(
        job_id, {"id": job_id, "current_task_id": task_id, "task_worker_id": worker_id, "status": "waiting_for_worker"}
    )

    result = TaskResult(
        job_id=job_id, task_id=task_id, worker_id=worker_id, metadata={"secure_flag": True}, timestamp=timestamp
    )

    signature = sign_pure(result, config.GLOBAL_WORKER_TOKEN)
    res_dict = to_dict(result)
    res_dict["security"] = {"signature": signature, "signer_id": worker_id}

    # Tamper with metadata!
    res_dict["metadata"]["secure_flag"] = False

    with pytest.raises(PermissionError, match="Invalid cryptographic signature"):
        await worker_service.process_task_result(res_dict, worker_id)


@pytest.mark.asyncio
async def test_heartbeat_full_object_signature(worker_service, config, storage):
    worker_id = "worker-1"
    await storage.register_worker(worker_id, {"worker_id": worker_id}, 60)

    hb = Heartbeat(worker_id=worker_id, status="ready", timestamp=time.time())

    signature = sign_pure(hb, config.GLOBAL_WORKER_TOKEN)
    hb_dict = to_dict(hb)
    hb_dict["security"] = {"signature": signature, "signer_id": worker_id}

    resp = await worker_service.update_worker_heartbeat(worker_id, hb_dict)
    assert resp["status"] == "ok"


@pytest.mark.asyncio
async def test_process_worker_event_bubbling_safe_full_signature(worker_service, config):
    worker_id = "worker-1"
    timestamp = time.time()

    event = WorkerEventPayload(
        event_id="ev-1",
        worker_id=worker_id,
        origin_worker_id=worker_id,
        event_type="progress",
        payload={"progress": 0.5},
        timestamp=timestamp,
    )

    # Sign ignoring bubbling_chain
    signature = sign_pure(event, config.GLOBAL_WORKER_TOKEN, ignore=["bubbling_chain"])

    event_dict = to_dict(event)
    event_dict["bubbling_chain"] = ["proxy-1", "proxy-2"]  # Modified!
    event_dict["security"] = {"signature": signature, "signer_id": worker_id}

    fut = asyncio.Future()
    fut.set_result(None)
    worker_service.storage.get_job_state = MagicMock(return_value=fut)

    resp = await worker_service.process_worker_event(worker_id, event_dict)
    assert resp["status"] == "event_accepted"


@pytest.mark.asyncio
async def test_missing_signature_rejected(worker_service, config):
    worker_id = "worker-1"
    reg = WorkerRegistration(worker_id=worker_id, timestamp=time.time())
    reg_dict = to_dict(reg)
    # Intentionally omitted security dict
    with pytest.raises(PermissionError, match="Missing required security context"):
        await worker_service.register_worker(reg_dict, worker_id)


@pytest.mark.asyncio
async def test_timestamp_boundary_conditions(worker_service, config):
    """Boundary test: 60s is OK, 61s is REJECTED."""
    worker_id = "boundary-worker"

    ts_60 = time.time() - 60
    reg_60 = WorkerRegistration(worker_id=worker_id, timestamp=ts_60)
    sig_60 = sign_pure(reg_60, config.GLOBAL_WORKER_TOKEN)
    reg_dict_60 = to_dict(reg_60)
    reg_dict_60["security"] = {"signature": sig_60, "signer_id": worker_id}

    # Should not raise
    await worker_service.register_worker(reg_dict_60, worker_id)

    ts_61 = time.time() - 61
    reg_61 = WorkerRegistration(worker_id=worker_id, timestamp=ts_61)
    sig_61 = sign_pure(reg_61, config.GLOBAL_WORKER_TOKEN)
    reg_dict_61 = to_dict(reg_61)
    reg_dict_61["security"] = {"signature": sig_61, "signer_id": worker_id}

    with pytest.raises(PermissionError, match="Message timestamp expired"):
        await worker_service.register_worker(reg_dict_61, worker_id)


@pytest.mark.asyncio
async def test_mandatory_timestamp_all_workers(worker_service):
    """Verify that timestamp is mandatory even if no secret is set (Zero Trust policy)."""
    worker_id = "no-token-worker"
    # Registration WITHOUT timestamp
    payload = {"worker_id": worker_id}

    with pytest.raises(PermissionError, match="Missing required timestamp"):
        await worker_service.register_worker(payload, worker_id)


@pytest.mark.asyncio
async def test_missing_timestamp_rejected(worker_service, config):
    worker_id = "worker-1"
    reg = WorkerRegistration(worker_id=worker_id)  # No timestamp
    signature = sign_pure(reg, config.GLOBAL_WORKER_TOKEN)
    reg_dict = to_dict(reg)
    reg_dict["security"] = {"signature": signature, "signer_id": worker_id}
    with pytest.raises(PermissionError, match="Missing required timestamp"):
        await worker_service.register_worker(reg_dict, worker_id)


@pytest.mark.asyncio
async def test_future_timestamp_rejected(worker_service, config):
    worker_id = "worker-1"
    reg = WorkerRegistration(worker_id=worker_id, timestamp=time.time() + 100)  # Clock skew from future
    signature = sign_pure(reg, config.GLOBAL_WORKER_TOKEN)
    reg_dict = to_dict(reg)
    reg_dict["security"] = {"signature": signature, "signer_id": worker_id}
    with pytest.raises(PermissionError, match="Message timestamp expired"):
        await worker_service.register_worker(reg_dict, worker_id)


@pytest.mark.asyncio
async def test_none_stripping_resilience(worker_service, config):
    worker_id = "worker-1"
    reg = WorkerRegistration(worker_id=worker_id, timestamp=time.time())
    signature = sign_pure(reg, config.GLOBAL_WORKER_TOKEN)
    reg_dict = to_dict(reg)

    # Introduce None fields that shouldn't affect verification
    reg_dict["capabilities"] = None
    reg_dict["metadata"] = None
    reg_dict["security"] = {"signature": signature, "signer_id": worker_id}

    # Should not raise
    await worker_service.register_worker(reg_dict, worker_id)


@pytest.mark.asyncio
async def test_event_type_spoofing_rejected(worker_service, config):
    worker_id = "worker-1"
    event = WorkerEventPayload(
        event_id="ev-1",
        worker_id=worker_id,
        origin_worker_id=worker_id,
        event_type="progress",
        payload={"progress": 0.5},
        timestamp=time.time(),
    )

    signature = sign_pure(event, config.GLOBAL_WORKER_TOKEN, ignore=["bubbling_chain"])
    event_dict = to_dict(event)

    # Tamper with event type
    event_dict["event_type"] = "alert"
    event_dict["security"] = {"signature": signature, "signer_id": worker_id}

    with pytest.raises(PermissionError, match="Invalid cryptographic signature"):
        await worker_service.process_worker_event(worker_id, event_dict)

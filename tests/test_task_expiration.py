# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock

import pytest

from avtomatika.constants import JOB_STATUS_FAILED, TASK_STATUS_SUCCESS
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.memory import MemoryStorage


@pytest.mark.asyncio
async def test_late_result_handling():
    """Verify that late results are rejected if job is already marked failed."""
    storage = MemoryStorage()
    history = MagicMock()
    history.log_job_event = AsyncMock()
    config = MagicMock()
    config.S3_AUTO_CLEANUP = False  # Avoid async task creation for S3 cleanup
    engine = MagicMock()
    engine.worker_service = None  # Not needed for this test

    service = WorkerService(storage, history, config, engine)

    job_id = "late-job"
    # Pre-setup failed job state in real memory storage
    await storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "status": JOB_STATUS_FAILED,
            "error_message": "Worker task timed out while waiting in queue (dispatch timeout).",
            "current_task_id": "task-1",
        },
    )

    result_payload = {
        "job_id": job_id,
        "task_id": "task-1",
        "worker_id": "worker-1",
        "status": TASK_STATUS_SUCCESS,
        "data": {"secret": "data"},
    }

    # We need to bypass zero trust for simplicity
    with MagicMock() as _:
        service._verify_zero_trust = AsyncMock()

        response = await service.process_task_result(result_payload, "worker-1")

    # Should return ignored result
    assert response["status"] == "ignored"
    assert response["reason"] == "deadline_exceeded"

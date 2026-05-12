# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from aiohttp import web

from avtomatika.api.handlers import get_job_history_handler, get_jobs_handler
from avtomatika.app_keys import ENGINE_KEY


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.config = MagicMock()
    engine.config.DETAILED_API_RESPONSES = False
    engine.history_storage = AsyncMock()
    engine.storage = AsyncMock()
    return engine


def create_mock_request(engine, token="test-token", query=None):
    req = MagicMock(spec=web.Request)
    req.app = {ENGINE_KEY: engine}
    req.query = query or {}
    req.match_info = {"job_id": "test-job"}
    req.get.side_effect = lambda k, default=None: {"token": token} if k == "client_config" else default
    return req


@pytest.mark.asyncio
async def test_no_result_leak_in_job_list(mock_engine):
    """
    LEAK CHECK: Field 'result' must NOT be present in job list objects for non-terminal jobs.
    """
    token = "owner-token"
    mock_jobs = [
        {
            "id": "job-running",
            "status": "running",
            "result": {"hidden": "should-not-see-this"},
            "client_token": token,
            "blueprint_name": "test-bp",
        },
        {
            "id": "job-finished",
            "status": "finished",
            "result": {"visible": "data"},
            "client_token": token,
            "blueprint_name": "test-bp",
        },
    ]
    mock_engine.history_storage.get_jobs.return_value = mock_jobs

    req = create_mock_request(mock_engine, token)
    resp = await get_jobs_handler(req)
    data = orjson.loads(resp.body)

    # 1. Result should be removed from running job
    running_job = next(j for j in data if j["id"] == "job-running")
    assert "result" not in running_job

    # 2. Result should remain in finished job
    finished_job = next(j for j in data if j["id"] == "job-finished")
    assert finished_job.get("result") == {"visible": "data"}


@pytest.mark.asyncio
async def test_no_result_leak_in_history_snapshots(mock_engine):
    """
    LEAK CHECK: Field 'result' must NOT be present in history context snapshots for non-terminal events.
    """
    token = "owner-token"
    mock_engine.storage.get_job_state.return_value = {"client_config": {"token": token}}

    mock_history = [
        {
            "event_type": "step_completed",
            "state": "running",
            "context_snapshot": {"id": "test-job", "status": "running", "result": {"leak": "dangerous"}},
        }
    ]
    mock_engine.history_storage.get_job_history.return_value = mock_history

    req = create_mock_request(mock_engine, token)
    resp = await get_job_history_handler(req)
    data = orjson.loads(resp.body)

    snapshot = data[0]["context_snapshot"]
    assert "result" not in snapshot
    assert snapshot["status"] == "running"

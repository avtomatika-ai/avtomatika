# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from aiohttp import web

from avtomatika.api.handlers import (
    get_dashboard_handler,
    get_job_history_handler,
    get_job_status_handler,
    get_jobs_handler,
    get_quarantined_jobs_handler,
)
from avtomatika.app_keys import ENGINE_KEY


def create_mock_request(engine, token=None, job_id=None):
    req = MagicMock(spec=web.Request)
    req.app = {ENGINE_KEY: engine}
    req.match_info = {"job_id": job_id} if job_id else {}
    req.query = {}
    req.get.side_effect = lambda k, default=None: {"token": token} if (k == "client_config" and token) else default
    return req


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.storage = AsyncMock()
    engine.history_storage = AsyncMock()
    engine.config = MagicMock()
    engine.config.DETAILED_API_RESPONSES = False
    return engine


@pytest.mark.asyncio
async def test_list_isolation(mock_engine):
    """Verify that job lists only show client's own jobs."""
    token_a = "client-a"
    mock_engine.history_storage.get_jobs.return_value = [{"id": "1", "client_token": token_a}]

    req = create_mock_request(mock_engine, token_a)
    resp = await get_jobs_handler(req)
    data = orjson.loads(resp.body)

    mock_engine.history_storage.get_jobs.assert_called_with(limit=100, offset=0, client_token=token_a)
    assert len(data) == 1


@pytest.mark.asyncio
async def test_quarantine_list_isolation(mock_engine):
    """Verify that quarantined job list is isolated."""
    token_a = "client-a"
    mock_engine.storage.get_quarantined_jobs.return_value = ["job-1"]
    mock_engine.storage.get_job_state.return_value = {
        "id": "job-1",
        "status": "quarantined",
        "client_config": {"token": token_a},
    }

    req = create_mock_request(mock_engine, token_a)
    await get_quarantined_jobs_handler(req)

    mock_engine.storage.get_quarantined_jobs.assert_called_with(client_token=token_a)


@pytest.mark.asyncio
async def test_direct_access_forbidden(mock_engine):
    """Verify that direct job access is denied to non-owners."""
    mock_engine.storage.get_job_state.return_value = {"id": "job-1", "client_config": {"token": "owner"}}

    req = create_mock_request(mock_engine, "stranger", job_id="job-1")
    with pytest.raises(web.HTTPForbidden):
        await get_job_status_handler(req)


@pytest.mark.asyncio
async def test_result_hidden_while_running(mock_engine):
    """Verify that results are hidden during execution."""
    mock_engine.storage.get_job_state.return_value = {
        "id": "1",
        "status": "running",
        "result": "secret",
        "client_config": {"token": "owner"},
    }

    req = create_mock_request(mock_engine, "owner", job_id="1")
    resp = await get_job_status_handler(req)
    data = orjson.loads(resp.body)

    assert "result" not in data


@pytest.mark.asyncio
async def test_result_visible_at_finish(mock_engine):
    """Verify that results are visible upon completion."""
    mock_engine.storage.get_job_state.return_value = {
        "id": "1",
        "status": "finished",
        "result": "data",
        "client_config": {"token": "owner"},
    }

    req = create_mock_request(mock_engine, "owner", job_id="1")
    resp = await get_job_status_handler(req)
    data = orjson.loads(resp.body)

    assert data["result"] == "data"


@pytest.mark.asyncio
async def test_dashboard_isolation(mock_engine):
    """Verify dashboard metrics isolation."""
    token_a = "client-a"
    mock_engine.storage.get_active_worker_count.return_value = 10
    mock_engine.storage.get_job_queue_length.return_value = 5
    mock_engine.history_storage.get_job_summary.return_value = {"finished": 1}

    req = create_mock_request(mock_engine, token_a)
    resp = await get_dashboard_handler(req)
    data = orjson.loads(resp.body)

    mock_engine.history_storage.get_job_summary.assert_called_with(client_token=token_a)
    assert data["jobs"]["finished"] == 1


@pytest.mark.asyncio
async def test_access_denied_without_owner_info(mock_engine):
    """Verify access denial if ownership info is missing."""
    mock_engine.storage.get_job_state.return_value = {"id": "1", "client_config": {}}

    req = create_mock_request(mock_engine, "any", job_id="1")
    with pytest.raises(web.HTTPForbidden):
        await get_job_status_handler(req)


@pytest.mark.asyncio
async def test_history_event_filtering(mock_engine):
    """Verify that results in history are filtered appropriately."""
    token = "owner"
    mock_engine.storage.get_job_state.return_value = {"id": "1", "client_config": {"token": token}}
    mock_engine.history_storage.get_job_history.return_value = [
        {"event_type": "step", "context_snapshot": {"status": "running", "result": "leak"}},
        {"event_type": "done", "context_snapshot": {"status": "finished", "result": "visible"}},
    ]

    req = create_mock_request(mock_engine, token, job_id="1")
    resp = await get_job_history_handler(req)
    data = orjson.loads(resp.body)

    assert "result" not in data[0]["context_snapshot"]
    assert data[1]["context_snapshot"]["result"] == "visible"

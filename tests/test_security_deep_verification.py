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
    _filter_job_state,
    get_dashboard_handler,
    get_job_status_handler,
    get_jobs_handler,
    get_quarantined_jobs_handler,
)
from avtomatika.app_keys import ENGINE_KEY


def create_mock_request(engine, token=None, job_id=None, query=None):
    req = MagicMock(spec=web.Request)
    req.app = {ENGINE_KEY: engine}
    req.match_info = {}
    if job_id:
        req.match_info["job_id"] = job_id
    req.query = query or {}

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
async def test_isolation_at_scale(mock_engine):
    """Verify isolation with many jobs from different clients."""
    token_a = "client-a"
    token_b = "client-b"

    jobs_a = [{"id": f"a-{i}", "client_token": token_a, "status": "finished"} for i in range(50)]
    jobs_b = [{"id": f"b-{i}", "client_token": token_b, "status": "finished"} for i in range(50)]

    async def side_effect(limit, offset, client_token=None):
        if client_token == token_a:
            return jobs_a[offset : offset + limit]
        return jobs_b[offset : offset + limit]

    mock_engine.history_storage.get_jobs.side_effect = side_effect

    req_a = create_mock_request(mock_engine, token_a, query={"limit": "100"})
    resp_a = await get_jobs_handler(req_a)
    data_a = orjson.loads(resp_a.body)
    assert len(data_a) == 50
    assert all(j["id"].startswith("a-") for j in data_a)


@pytest.mark.asyncio
async def test_quarantine_isolation(mock_engine):
    """Verify that clients only see their own jobs in quarantine."""
    token_a = "client-a"

    async def get_quarantined_side_effect(client_token=None):
        if client_token == token_a:
            return ["job-a1", "job-a2"]
        return ["job-b1"]

    mock_engine.storage.get_quarantined_jobs.side_effect = get_quarantined_side_effect

    mock_engine.storage.get_job_state.side_effect = lambda j_id: {
        "id": j_id,
        "status": "quarantined",
        "client_config": {"token": token_a if "a" in j_id else "client-b"},
    }

    req = create_mock_request(mock_engine, token_a)
    resp = await get_quarantined_jobs_handler(req)
    data = orjson.loads(resp.body)

    assert len(data) == 2
    assert all(j["id"].startswith("job-a") for j in data)


@pytest.mark.asyncio
async def test_history_ownership_verification_at_scale(mock_engine):
    """Full cycle: job not in Redis, but present in history. Access granted to owner."""
    token = "owner-token"
    job_id = "old-job"
    req = create_mock_request(mock_engine, token, job_id=job_id)

    mock_engine.storage.get_job_state.return_value = None

    mock_engine.history_storage.get_job_history.return_value = [
        {"job_id": job_id, "client_token": token, "state": "pending", "event_type": "job_created"},
        {
            "job_id": job_id,
            "client_token": token,
            "state": "finished",
            "event_type": "job_completed",
            "context_snapshot": {"status": "finished", "result": "ok", "id": job_id},
        },
    ]

    resp = await get_job_status_handler(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_forbidden_history_access(mock_engine):
    """Access denied for jobs in history owned by others."""
    req = create_mock_request(mock_engine, "me", job_id="stranger-job")

    mock_engine.storage.get_job_state.return_value = None
    mock_engine.history_storage.get_job_history.return_value = [
        {"job_id": "stranger-job", "client_token": "not-me", "state": "finished"}
    ]

    with pytest.raises(web.HTTPForbidden):
        await get_job_status_handler(req)


@pytest.mark.asyncio
async def test_filter_robustness(mock_engine):
    """Test filter resilience against malformed data."""
    assert _filter_job_state({}, False) == {}

    state = {"id": "123", "secret": "data"}
    filtered = _filter_job_state(state, False)
    assert "secret" not in filtered
    assert filtered["id"] == "123"

    assert _filter_job_state({"id": "1"}, False, set()) == {"id": "1"}


@pytest.mark.asyncio
async def test_dashboard_isolation_verified(mock_engine):
    """Verify that dashboard statistics are isolated per client."""
    token_a = "client-a"
    req = create_mock_request(mock_engine, token_a)

    mock_engine.storage.get_active_worker_count.return_value = 5
    mock_engine.storage.get_job_queue_length.return_value = 10

    mock_engine.history_storage.get_job_summary.return_value = {"finished": 5, "failed": 1}

    resp = await get_dashboard_handler(req)
    data = orjson.loads(resp.body)

    mock_engine.history_storage.get_job_summary.assert_called_with(client_token=token_a)
    assert data["jobs"]["finished"] == 5

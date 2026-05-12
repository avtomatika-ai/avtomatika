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
    cancel_job_handler,
    get_dashboard_handler,
    get_job_file_download_handler,
    get_job_file_upload_handler,
    get_job_history_handler,
    get_job_status_handler,
    get_jobs_handler,
    human_approval_webhook_handler,
    stream_job_file_upload_handler,
)
from avtomatika.app_keys import ENGINE_KEY, S3_SERVICE_KEY


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.storage = AsyncMock()
    engine.history_storage = AsyncMock()
    engine.config = MagicMock()
    engine.config.DETAILED_API_RESPONSES = False
    engine.app = {ENGINE_KEY: engine}
    return engine


@pytest.fixture
def mock_request(mock_engine):
    req = MagicMock(spec=web.Request)
    req.app = mock_engine.app
    req.match_info = {"job_id": "test-job", "filename": "test.txt"}
    req.query = {"filename": "test.txt"}
    req.get.side_effect = lambda k, default=None: {"token": "client-a"} if k == "client_config" else default
    return req


@pytest.mark.asyncio
async def test_all_handlers_require_ownership(mock_engine, mock_request):
    """Verify that all relevant endpoints enforce job ownership."""
    handlers = [
        get_job_status_handler,
        cancel_job_handler,
        get_job_history_handler,
        get_job_file_upload_handler,
        stream_job_file_upload_handler,
        get_job_file_download_handler,
        human_approval_webhook_handler,
    ]

    job_state = {"id": "test-job", "client_config": {"token": "client-a"}}
    mock_engine.storage.get_job_state.return_value = job_state
    mock_engine.history_storage.get_job_history.return_value = []
    mock_engine.cancel_job = AsyncMock(return_value=True)

    # Setup S3 service mock for file handlers
    s3_service = MagicMock()
    s3_service._enabled = True
    task_files = MagicMock()
    task_files.upload_stream = AsyncMock(return_value="s3://uri")
    task_files.generate_presigned_url.return_value = "http://presigned"
    s3_service.get_task_files.return_value = task_files
    mock_engine.app[S3_SERVICE_KEY] = s3_service

    # 1. Access granted to owner
    for handler in handlers:
        if handler == human_approval_webhook_handler:
            mock_request.json = AsyncMock(return_value={"decision": "approve"})
            job_state["status"] = "waiting_for_human"
            job_state["current_task_transitions"] = {"approve": "next"}

        try:
            res = await handler(mock_request)
            assert res.status in (200, 202, 302)
        except web.HTTPFound as e:
            # Redirect is success for download handler
            assert e.status == 302

    # 2. Access denied to others
    mock_request.get.side_effect = lambda k, default=None: {"token": "client-b"} if k == "client_config" else default

    for handler in handlers:
        if handler == human_approval_webhook_handler:
            mock_request.json = AsyncMock(return_value={"decision": "approve"})

        with pytest.raises(web.HTTPForbidden):
            await handler(mock_request)


@pytest.mark.asyncio
async def test_file_access_ownership_enforcement(mock_engine, mock_request):
    """Specific check for S3 file access isolation."""
    s3_service = MagicMock()
    s3_service._enabled = True
    mock_engine.app[S3_SERVICE_KEY] = s3_service

    mock_engine.storage.get_job_state.return_value = {"id": "test-job", "client_config": {"token": "client-a"}}

    mock_request.get.side_effect = lambda k, default=None: {"token": "client-b"} if k == "client_config" else default

    with pytest.raises(web.HTTPForbidden):
        await get_job_file_download_handler(mock_request)


@pytest.mark.asyncio
async def test_ownership_check_is_early(mock_engine, mock_request):
    """Ownership must be verified even for pending or waiting jobs."""
    mock_request.get.side_effect = lambda k, default=None: {"token": "client-b"} if k == "client_config" else default

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "pending",
        "client_config": {"token": "client-a"},
    }

    with pytest.raises(web.HTTPForbidden):
        await get_job_status_handler(mock_request)


@pytest.mark.asyncio
async def test_history_ownership_fallback(mock_engine, mock_request):
    """Verify ownership check falls back to history if job is not in Redis."""
    mock_engine.storage.get_job_state.return_value = None

    mock_engine.history_storage.get_job_history.return_value = [
        {"client_token": "client-b", "event_type": "job_created"}
    ]

    mock_request.get.side_effect = lambda k, default=None: {"token": "client-a"} if k == "client_config" else default

    with pytest.raises(web.HTTPForbidden):
        await get_job_status_handler(mock_request)


@pytest.mark.asyncio
async def test_history_ownership_success(mock_engine, mock_request):
    """Owner should be able to access history of their finished jobs."""
    mock_engine.storage.get_job_state.return_value = None
    mock_engine.history_storage.get_job_history.return_value = [
        {
            "client_token": "client-a",
            "event_type": "job_created",
            "context_snapshot": {"id": "test-job", "status": "finished"},
        }
    ]

    mock_request.get.side_effect = lambda k, default=None: {"token": "client-a"} if k == "client_config" else default

    res = await get_job_status_handler(mock_request)
    assert res.status == 200


@pytest.mark.asyncio
async def test_job_list_isolation(mock_engine, mock_request):
    """Clients should only see their own jobs in the list."""
    mock_request.get.side_effect = lambda k, default=None: {"token": "client-a"} if k == "client_config" else default

    all_jobs = [
        {"job_id": "job-a", "client_token": "client-a", "context_snapshot": {"id": "job-a", "status": "finished"}},
        {"job_id": "job-b", "client_token": "client-b", "context_snapshot": {"id": "job-b", "status": "finished"}},
    ]

    async def get_jobs_side_effect(limit, offset, client_token=None):
        return [j for j in all_jobs if j.get("client_token") == client_token]

    mock_engine.history_storage.get_jobs.side_effect = get_jobs_side_effect

    response = await get_jobs_handler(mock_request)
    data = orjson.loads(response.body)

    assert len(data) == 1
    assert data[0]["job_id"] == "job-a"


@pytest.mark.asyncio
async def test_dashboard_stats_isolation(mock_engine, mock_request):
    """Dashboard metrics should be isolated per client."""
    mock_request.get.side_effect = lambda k, default=None: {"token": "client-a"} if k == "client_config" else default

    mock_engine.storage.get_active_worker_count.return_value = 5
    mock_engine.storage.get_job_queue_length.return_value = 10

    mock_engine.history_storage.get_job_summary.return_value = {"finished": 1}

    response = await get_dashboard_handler(mock_request)
    data = orjson.loads(response.body)

    assert data["jobs"]["finished"] == 1
    mock_engine.history_storage.get_job_summary.assert_called_with(client_token="client-a")

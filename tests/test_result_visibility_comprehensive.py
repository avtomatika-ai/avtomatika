# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from avtomatika.api.handlers import get_job_history_handler, get_job_status_handler
from avtomatika.app_keys import ENGINE_KEY


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.storage = AsyncMock()
    engine.config = MagicMock()
    return engine


@pytest.fixture
def mock_request(mock_engine):
    req = MagicMock()
    req.app = {ENGINE_KEY: mock_engine}
    req.match_info = {"job_id": "test-job"}
    req.query = {}

    # Authenticate the request
    token = "test-token"
    req.get.side_effect = lambda k, default=None: {"token": token} if k == "client_config" else default
    return req


@pytest.mark.asyncio
async def test_result_hidden_for_running_job(mock_engine, mock_request):
    """Verify that result is hidden for running jobs in compact mode."""
    token = "test-token"
    mock_engine.config.DETAILED_API_RESPONSES = False

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "running",
        "result": {"partial": "data"},
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_result_hidden_for_waiting_job(mock_engine, mock_request):
    """Verify that result is hidden for jobs waiting for a worker."""
    token = "test-token"
    mock_engine.config.DETAILED_API_RESPONSES = False

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "waiting_for_worker",
        "result": {"previous_step": "ok"},
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data
    assert data["status"] == "waiting_for_worker"


@pytest.mark.asyncio
async def test_result_visible_for_finished_job(mock_engine, mock_request):
    """Verify that result is visible for finished jobs."""
    token = "test-token"
    mock_engine.config.DETAILED_API_RESPONSES = False

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "finished",
        "result": {"final": "data"},
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert data["result"] == {"final": "data"}
    assert data["status"] == "finished"


@pytest.mark.asyncio
async def test_result_visible_for_failed_job(mock_engine, mock_request):
    """Verify that result (error context) is visible for failed jobs."""
    token = "test-token"
    mock_engine.config.DETAILED_API_RESPONSES = False

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "failed",
        "result": {"error": "context"},
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert data["result"] == {"error": "context"}
    assert data["status"] == "failed"


@pytest.mark.asyncio
async def test_result_hidden_in_detailed_mode_for_running_job(mock_engine, mock_request):
    """Result should be hidden even in detailed mode for non-terminal jobs."""
    token = "test-token"
    mock_engine.config.DETAILED_API_RESPONSES = True

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "running",
        "result": {"secret": "tracking"},
        "state_history": {"step1": "done"},
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data
    assert "state_history" in data


@pytest.mark.asyncio
async def test_result_visibility_with_fields_filter(mock_engine, mock_request):
    """Field filter should not bypass result hiding rules."""
    token = "test-token"
    mock_engine.config.DETAILED_API_RESPONSES = False

    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "running",
        "result": {"top": "secret"},
        "client_config": {"token": token},
    }
    mock_request.query = {"fields": "id,status,result"}

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_result_in_history_only_for_completion_event(mock_engine, mock_request):
    """Verify that results in history events are only visible for completion events."""
    mock_engine.config.DETAILED_API_RESPONSES = False
    mock_engine.history_storage = AsyncMock()

    token = "test-token"
    mock_request.get.side_effect = lambda k, default=None: {"token": token} if k == "client_config" else default
    mock_engine.storage.get_job_state.return_value = {"id": "test-job", "client_config": {"token": token}}

    history = [
        {"event_type": "job_created", "context_snapshot": {"status": "running", "result": {"hidden": 1}}},
        {"event_type": "job_completed", "context_snapshot": {"status": "finished", "result": {"visible": 2}}},
    ]
    mock_engine.history_storage.get_job_history.return_value = history

    response = await get_job_history_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data[0]["context_snapshot"]
    assert data[1]["context_snapshot"]["result"] == {"visible": 2}

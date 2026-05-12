# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from avtomatika.api.handlers import (
    get_job_history_handler,
    get_job_status_handler,
    get_jobs_handler,
    get_quarantined_jobs_handler,
)
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
    app_mock = MagicMock()
    app_dict = {ENGINE_KEY: mock_engine}
    app_mock.__getitem__.side_effect = app_dict.__getitem__
    req.app = app_mock
    req.match_info = {"job_id": "test-job"}
    req.query = {}

    # Authenticate the request
    token = "test-token"
    req.get.return_value = {"token": token, "client_id": "test-client"}
    return req


@pytest.mark.asyncio
async def test_strict_api_field_filtering_blocks_secrets(mock_engine, mock_request):
    """
    Ensures that even if a client explicitly requests secrets via ?fields,
    they are blocked in compact mode.
    """
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False  # Compact mode
    job_state = {
        "id": "test-job",
        "status": "running",
        "result": {"ok": True},
        "blueprint_name": "bp",
        "secret_key": "HIDE_ME",
        "client_config": {"token": token},
        "state_history": {"step1": "SHOULD_BE_HIDDEN"},
    }
    mock_engine.storage.get_job_state.return_value = job_state

    # Client tries to cheat and get state_history
    mock_request.query = {"fields": "status,state_history,client_config"}

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    # Result should ONLY contain id and status (allowed fields from the intersection)
    assert "id" in data
    assert "status" in data
    assert "state_history" not in data
    assert "client_config" not in data
    assert len(data) == 2


@pytest.mark.asyncio
async def test_api_result_field_consistency_terminal_states(mock_engine, mock_request):
    """
    Verifies that the 'result' field contains the last step data in terminal states.
    """
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = True
    job_state = {
        "id": "test-job",
        "status": "finished",
        "result": {"final": "success"},
        "blueprint_name": "bp",
        "current_state": "finished",
        "client_config": {"token": token},
    }
    mock_engine.storage.get_job_state.return_value = job_state

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert data["result"] == {"final": "success"}
    assert data["status"] == "finished"


@pytest.mark.asyncio
async def test_api_fields_parameter_with_id_injection(mock_engine, mock_request):
    """Ensures ID is always present even if not requested."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = True
    mock_engine.storage.get_job_state.return_value = {
        "id": "1",
        "status": "ok",
        "other": "x",
        "client_config": {"token": token},
    }
    mock_request.query = {"fields": "status"}

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)
    assert "id" in data
    assert "status" in data
    assert "other" not in data


@pytest.mark.asyncio
async def test_api_jobs_list_filtering(mock_engine, mock_request):
    """Verifies that context_snapshot in jobs list is also filtered."""
    mock_engine.config.DETAILED_API_RESPONSES = False
    mock_engine.history_storage = AsyncMock()

    full_snapshot = {"id": "1", "status": "ok", "secret": "hide_me", "blueprint_name": "bp"}
    mock_engine.history_storage.get_jobs.return_value = [{"job_id": "1", "context_snapshot": full_snapshot}]

    response = await get_jobs_handler(mock_request)
    data = orjson.loads(response.body)

    snapshot = data[0]["context_snapshot"]
    assert "secret" not in snapshot
    assert "id" in snapshot
    assert "status" in snapshot


@pytest.mark.asyncio
async def test_api_job_history_filtering(mock_engine, mock_request):
    """Verifies that context_snapshot in history events is filtered."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False
    mock_engine.history_storage = AsyncMock()

    full_snapshot = {"id": "test-job", "status": "ok", "secret": "hide_me", "blueprint_name": "bp"}
    mock_engine.storage.get_job_state.return_value = {"id": "test-job", "client_config": {"token": token}}
    mock_engine.history_storage.get_job_history.return_value = [{"event_id": "e1", "context_snapshot": full_snapshot}]

    response = await get_job_history_handler(mock_request)
    data = orjson.loads(response.body)

    snapshot = data[0]["context_snapshot"]
    assert "secret" not in snapshot
    assert "status" in snapshot


@pytest.mark.asyncio
async def test_api_quarantined_jobs_filtering(mock_engine, mock_request):
    """Verifies that jobs in quarantine list are filtered."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False

    full_job = {
        "id": "q1",
        "status": "quarantined",
        "secret": "hide",
        "blueprint_name": "bp",
        "client_config": {"token": token},
    }
    # get_quarantined_jobs is async, but the handler awaits it.
    mock_engine.storage.get_quarantined_jobs = AsyncMock(return_value=[full_job])

    # Ownership verification needs this
    mock_engine.storage.get_job_state = AsyncMock(return_value=full_job)

    response = await get_quarantined_jobs_handler(mock_request)
    data = orjson.loads(response.body)

    assert "secret" not in data[0]
    assert data[0]["status"] == "quarantined"
    assert "id" in data[0]

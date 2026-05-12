# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from avtomatika.api.handlers import get_job_status_handler
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
async def test_result_hidden_when_running_compact(mock_engine, mock_request):
    """Result should be hidden on non-terminal states in compact mode."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False
    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "running",
        "result": {"step": 1},
        "blueprint_name": "bp",
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_result_hidden_when_running_detailed(mock_engine, mock_request):
    """Result should be hidden on non-terminal states even in detailed mode."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = True
    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "running",
        "result": {"step": 1},
        "blueprint_name": "bp",
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert "result" not in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_result_visible_when_finished(mock_engine, mock_request):
    """Result should be visible when job is finished."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False
    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "finished",
        "result": {"final": "data"},
        "blueprint_name": "bp",
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert data["result"] == {"final": "data"}
    assert data["status"] == "finished"


@pytest.mark.asyncio
async def test_result_visible_when_failed(mock_engine, mock_request):
    """Result should be visible when job is failed (as it is a terminal state)."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False
    mock_engine.storage.get_job_state.return_value = {
        "id": "test-job",
        "status": "failed",
        "result": {"error": "context"},
        "blueprint_name": "bp",
        "client_config": {"token": token},
    }

    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)

    assert data["result"] == {"error": "context"}
    assert data["status"] == "failed"

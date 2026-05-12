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
async def test_api_compact_mode_strict_default(mock_engine, mock_request):
    """Verifies that DETAILED_API_RESPONSES=False (default) returns only 4 fields and blocks others."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = False
    job_state = {
        "id": "test-job",
        "status": "finished",
        "result": {"data": 123},
        "blueprint_name": "test_bp",
        "current_state": "end",
        "internal_debug_info": "secret",
        "client_config": {"token": token},
    }
    mock_engine.storage.get_job_state.return_value = job_state

    # 1. Default request
    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)
    assert set(data.keys()) == {"id", "status", "result", "blueprint_name"}
    assert "current_state" not in data

    # 2. Explicit request for blocked field
    mock_request.query = {"fields": "status,internal_debug_info"}
    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)
    # internal_debug_info should be ignored because DETAILED_API_RESPONSES is False
    assert set(data.keys()) == {"id", "status"}
    assert "internal_debug_info" not in data


@pytest.mark.asyncio
async def test_api_detailed_mode_allowed(mock_engine, mock_request):
    """Verifies that DETAILED_API_RESPONSES=True allows all fields and filtering."""
    token = mock_request.get("client_config")["token"]
    mock_engine.config.DETAILED_API_RESPONSES = True
    job_state = {
        "id": "test-job",
        "status": "finished",
        "current_state": "end",
        "internal_debug_info": "secret",
        "client_config": {"token": token},
    }
    mock_engine.storage.get_job_state.return_value = job_state

    # 1. Detailed mode without filter returns everything
    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)
    assert "internal_debug_info" in data
    assert "current_state" in data

    # 2. Detailed mode with filter allows requested fields
    mock_request.query = {"fields": "status,internal_debug_info"}
    response = await get_job_status_handler(mock_request)
    data = orjson.loads(response.body)
    assert set(data.keys()) == {"id", "status", "internal_debug_info"}

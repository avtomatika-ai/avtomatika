# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from aiohttp import web

from avtomatika.api.handlers import (
    cancel_job_handler,
    docs_handler,
    get_blueprint_graph_handler,
    get_job_status_handler,
    get_jobs_handler,
    get_worker_catalog_handler,
    human_approval_webhook_handler,
)
from avtomatika.app_keys import ENGINE_KEY
from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def engine(storage, config):
    engine = OrchestratorEngine(storage, config)
    # Mock components that are usually set up in on_startup
    engine.ws_manager = AsyncMock()
    engine.ws_manager.register = AsyncMock()
    engine.ws_manager.unregister = AsyncMock()
    engine.ws_manager.handle_message = AsyncMock()
    engine.ws_manager.send_command = AsyncMock(return_value=True)

    engine.webhook_sender = AsyncMock()
    engine.webhook_sender.send = AsyncMock()
    engine.webhook_sender.start = MagicMock()

    return engine


@pytest.fixture
def request_mock(engine):
    req = MagicMock(spec=web.Request)
    req.json = AsyncMock(return_value={})

    # app needs to be accessible via []
    app_mock = MagicMock()
    app_dict = {
        ENGINE_KEY: engine,
    }
    app_mock.__getitem__.side_effect = app_dict.__getitem__
    app_mock.get.side_effect = app_dict.get
    req.app = app_mock

    req.headers = {}
    # Authenticate the request
    token = "test-token"
    req.get.return_value = {"token": token, "client_id": "test-client"}

    # Use a dictionary to avoid lazy behavior
    req.match_info = {}
    req.query = {}
    req.can_read_body = True
    return req


@pytest.mark.asyncio
async def test_cancel_job_not_found(engine, request_mock):
    request_mock.match_info = {"job_id": "non-existent-job"}
    response = await cancel_job_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_cancel_job_wrong_state(engine, request_mock):
    job_id = "job-in-wrong-state"
    token = request_mock.get("client_config")["token"]
    await engine.storage.save_job_state(job_id, {"id": job_id, "status": "running", "client_config": {"token": token}})
    request_mock.match_info = {"job_id": job_id}
    response = await cancel_job_handler(request_mock)
    assert response.status == 200


@pytest.mark.asyncio
async def test_cancel_job_no_worker_id(engine, request_mock):
    job_id = "job-no-worker-id"
    token = request_mock.get("client_config")["token"]
    await engine.storage.save_job_state(
        job_id, {"id": job_id, "status": "waiting_for_worker", "client_config": {"token": token}}
    )
    request_mock.match_info = {"job_id": job_id}
    response = await cancel_job_handler(request_mock)
    assert response.status == 200


@pytest.mark.asyncio
async def test_cancel_job_no_task_id(engine, request_mock):
    job_id = "job-no-task-id"
    token = request_mock.get("client_config")["token"]
    await engine.storage.save_job_state(
        job_id,
        {"id": job_id, "status": "waiting_for_worker", "task_worker_id": "worker-1", "client_config": {"token": token}},
    )
    request_mock.match_info = {"job_id": job_id}
    response = await cancel_job_handler(request_mock)
    assert response.status == 200


@pytest.mark.asyncio
async def test_cancel_job_ws_fails(engine, request_mock, caplog):
    job_id = "job-ws-fails"
    worker_id = "worker-1"
    task_id = "task-1"
    token = request_mock.get("client_config")["token"]
    await engine.storage.save_job_state(
        job_id,
        {
            "id": job_id,
            "status": "waiting_for_worker",
            "task_worker_id": worker_id,
            "current_task_id": task_id,
            "client_config": {"token": token},
        },
    )
    await engine.storage.register_worker(worker_id, {"worker_id": worker_id, "capabilities": {"websockets": True}}, 60)

    engine.ws_manager.send_command.return_value = False

    request_mock.match_info = {"job_id": job_id}
    response = await cancel_job_handler(request_mock)
    assert response.status == 200
    assert "Failed to send cancellation command via WebSocket" in caplog.text


@pytest.mark.asyncio
async def test_get_blueprint_graph_not_found(engine, request_mock):
    request_mock.match_info = {"blueprint_name": "non-existent-blueprint"}
    response = await get_blueprint_graph_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_get_blueprint_graph_file_not_found(engine, request_mock):
    bp = MagicMock()
    bp.name = "test_bp"
    bp.render_graph.side_effect = FileNotFoundError
    engine.register_blueprint(bp)
    request_mock.match_info = {"blueprint_name": "test_bp"}
    response = await get_blueprint_graph_handler(request_mock)
    assert response.status == 501


@pytest.mark.asyncio
async def test_human_approval_job_not_found(engine, request_mock):
    request_mock.match_info = {"job_id": "non-existent-job"}
    request_mock.json.return_value = {"decision": "approve"}
    response = await human_approval_webhook_handler(request_mock)
    assert response.status == 404


@pytest.mark.asyncio
async def test_human_approval_invalid_body(engine, request_mock):
    job_id = "j1"
    request_mock.match_info = {"job_id": job_id}
    request_mock.json = AsyncMock(side_effect=ValueError)
    response = await human_approval_webhook_handler(request_mock)
    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_human_approval_wrong_state(engine, request_mock):
    job_id = "job-in-wrong-state"
    token = request_mock.get("client_config")["token"]

    async def mock_get_job_state(jid):
        if jid == job_id:
            return {"id": job_id, "status": "running", "client_config": {"token": token}}
        return None

    engine.storage.get_job_state = mock_get_job_state
    request_mock.match_info = {"job_id": job_id}
    request_mock.json = AsyncMock(return_value={"decision": "approved"})
    response = await human_approval_webhook_handler(request_mock)
    # Return Conflict (409) if job is not in 'waiting_for_human' state
    assert response.status == 409


@pytest.mark.asyncio
async def test_human_approval_invalid_decision(engine, request_mock):
    job_id = "job-invalid-decision"
    token = request_mock.get("client_config")["token"]

    async def mock_get_job_state(jid):
        return {"id": job_id, "status": "waiting_for_human", "client_config": {"token": token}}

    engine.storage.get_job_state = mock_get_job_state
    request_mock.match_info = {"job_id": job_id}
    request_mock.json = AsyncMock(return_value={"decision": "rejected"})
    response = await human_approval_webhook_handler(request_mock)
    assert response.status == 400


@pytest.mark.asyncio
async def test_get_worker_catalog_cached(engine, request_mock):
    """Checks that the worker catalog is cached."""
    engine.worker_catalog_cache["data"] = [{"skill": "cached"}]
    engine.worker_catalog_cache["expires_at"] = 9999999999.0

    response = await get_worker_catalog_handler(request_mock)
    assert response.status == 200
    data = orjson.loads(response.body)
    assert data == [{"skill": "cached"}]


@pytest.mark.asyncio
async def test_get_jobs_handler(engine, request_mock):
    """Checks the jobs listing handler."""
    engine.history_storage = AsyncMock()
    engine.history_storage.get_jobs.return_value = [{"id": "j1"}]
    request_mock.query = {"limit": "10", "offset": "0"}

    response = await get_jobs_handler(request_mock)
    assert response.status == 200


@pytest.mark.asyncio
async def test_get_jobs_handler_invalid_params(engine, request_mock):
    """Checks the jobs listing handler with invalid query params."""
    request_mock.query = {"limit": "abc"}
    response = await get_jobs_handler(request_mock)
    assert response.status == 400


@pytest.mark.asyncio
async def test_api_field_filtering(engine, request_mock):
    """Checks that GET /api/v1/jobs/{id}?fields=... filters the response."""

    job_id = "j1"
    token = request_mock.get("client_config")["token"]
    await engine.storage.save_job_state(
        job_id,
        {"id": job_id, "status": "finished", "result": "data", "secret": "hide-me", "client_config": {"token": token}},
    )

    request_mock.match_info = {"job_id": job_id}
    request_mock.query = {"fields": "status,result"}

    response = await get_job_status_handler(request_mock)
    assert response.status == 200
    data = orjson.loads(response.body)

    assert "id" in data
    assert "status" in data
    assert "result" in data
    assert "secret" not in data


@pytest.mark.asyncio
async def test_docs_handler_file_not_found(engine, request_mock):
    """Checks that docs_handler returns 500 when docs file is missing."""
    with patch("importlib.resources.read_text", side_effect=FileNotFoundError):
        response = await docs_handler(request_mock)
        assert response.status == 500

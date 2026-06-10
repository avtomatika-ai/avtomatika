# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from avtomatika.engine import OrchestratorEngine
from avtomatika.ratelimit import rate_limit_middleware_factory
from avtomatika.services.worker_service import WorkerService


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.RATE_LIMITING_ENABLED = True
    config.RATE_LIMIT_LIMIT = 5
    config.RATE_LIMIT_PERIOD = 60
    config.RATE_LIMIT_POLL_LIMIT = 2
    config.RATE_LIMIT_HEARTBEAT_LIMIT = 2
    config.LOG_LEVEL = "INFO"
    config.LOG_FORMAT = "JSON"
    config.TZ = "UTC"
    config.HISTORY_DATABASE_URI = ""
    config.GLOBAL_WORKER_TOKEN = "global_secret"
    config.WORKER_AUTH_MODE = "mixed"
    config.REDIS_ENCRYPTION_KEY = ""
    return config


@pytest.mark.asyncio
async def test_protection_against_worker_id_spoofing(mock_config):
    """
    Exhaustive test: A worker with a valid global token tries to bypass limits
    by rotating worker_id in each request.
    """
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    # verify_worker_auth returns (spoofed_id, token_hash)
    token = "global_secret"
    token_hash = sha256(token.encode()).hexdigest()

    async def mock_verify(s, c, t, cert, hint, m, ch, fc=None):
        return hint, token_hash

    with patch("avtomatika.engine.verify_worker_auth", side_effect=mock_verify):
        engine = OrchestratorEngine(storage, mock_config)
        engine.setup()
        engine.worker_service = AsyncMock()

        # Requests with DIFFERENT worker_ids but SAME token
        ids = ["attacker_1", "attacker_2", "attacker_3"]

        for wid in ids:
            await engine.handle_rxon_message("poll", wid, {"token": token})

            # Verify that each Redis key contains the TOKEN HASH
            # This is the anchor that prevents bypass
            last_key = storage.increment_key_with_ttl.call_args[0][0]
            assert token_hash in last_key
            assert wid in last_key
            assert "ratelimit:worker" in last_key


@pytest.mark.asyncio
async def test_worker_limit_isolation_behind_nat(mock_config):
    """
    Verifies that workers with DIFFERENT tokens have strictly
    independent limits even if they would share IP (handled in engine).
    """
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None

    async def mock_verify(s, c, t, cert, hint, m, ch, fc=None):
        return hint, sha256(t.encode()).hexdigest()

    with patch("avtomatika.engine.verify_worker_auth", side_effect=mock_verify):
        engine = OrchestratorEngine(storage, mock_config)
        engine.setup()
        engine.worker_service = AsyncMock()

        # Worker A (token A)
        storage.increment_key_with_ttl.return_value = 1
        await engine.handle_rxon_message("poll", "worker_A", {"token": "token_A"})

        # Worker B (token B) - should get its own counter start
        storage.increment_key_with_ttl.return_value = 1
        await engine.handle_rxon_message("poll", "worker_B", {"token": "token_B"})

        assert storage.increment_key_with_ttl.call_count == 2
        keys = [call[0][0] for call in storage.increment_key_with_ttl.call_args_list]
        assert keys[0] != keys[1]


@pytest.mark.asyncio
async def test_rate_limit_fail_open_on_storage_error(mock_config):
    """
    CRITICAL: If Redis is down, we must NOT block all workers.
    System should log error and allow the message.
    """
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    storage.increment_key_with_ttl.side_effect = Exception("Redis Connection Refused")

    with patch("avtomatika.engine.verify_worker_auth", return_value=("w1", "hash")):
        engine = OrchestratorEngine(storage, mock_config)
        engine.setup()
        engine.worker_service = AsyncMock()

        # This call should succeed even if rate limiting fails
        result = await engine.handle_rxon_message("poll", "w1", {"token": "t"})
        assert result is not None
        assert engine.worker_service.get_next_task.called


@pytest.mark.asyncio
async def test_retry_after_only_for_workers(mock_config):
    """
    Ensures Retry-After is present in engine (worker) exceptions
    but NOT in middleware (client) responses.
    """
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    storage.increment_key_with_ttl.return_value = 100  # Definitely blocked
    storage.get_ttl.return_value = 55

    # 1. Check Engine (Worker)
    with patch("avtomatika.engine.verify_worker_auth", return_value=("w1", "h1")):
        engine = OrchestratorEngine(storage, mock_config)
        engine.setup()
        with pytest.raises(web.HTTPTooManyRequests) as exc:
            await engine.handle_rxon_message("poll", "w1", {"token": "t"})
        assert exc.value.headers["Retry-After"] == "55"

    # 2. Check Middleware (Client)
    middleware = rate_limit_middleware_factory(storage, MagicMock(), 1, 60)
    req = MagicMock()
    req.path = "/api/jobs"
    req.remote = "1.2.3.4"
    handler = AsyncMock()

    response = await middleware(req, handler)
    assert response.status == 429
    assert "Retry-After" not in response.headers


@pytest.mark.asyncio
async def test_engine_rate_limit_retry_after_edge_cases(mock_config):
    """Tests various TTL edge cases for Retry-After header in engine."""
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    storage.increment_key_with_ttl.return_value = 10  # Always blocked (> 5)

    with patch("avtomatika.engine.verify_worker_auth", return_value=("w1", "hash")):
        engine = OrchestratorEngine(storage, mock_config)
        engine.setup()
        engine.worker_service = AsyncMock()

        # Case 1: TTL is -2
        storage.get_ttl.return_value = -2
        with pytest.raises(web.HTTPTooManyRequests) as exc:
            await engine.handle_rxon_message("poll", "w1", {"token": "t"})
        assert exc.value.headers["Retry-After"] == "1"

        # Case 2: TTL is -1
        storage.get_ttl.return_value = -1
        with pytest.raises(web.HTTPTooManyRequests) as exc:
            await engine.handle_rxon_message("poll", "w1", {"token": "t"})
        assert exc.value.headers["Retry-After"] == "1"

        # Case 3: TTL is 0
        storage.get_ttl.return_value = 0
        with pytest.raises(web.HTTPTooManyRequests) as exc:
            await engine.handle_rxon_message("poll", "w1", {"token": "t"})
        assert exc.value.headers["Retry-After"] == "1"


@pytest.mark.asyncio
async def test_webhook_payload_filtering(mock_config):
    """Verifies that webhooks also respect DETAILED_API_RESPONSES policy."""
    mock_config.DETAILED_API_RESPONSES = False  # Compact
    engine = OrchestratorEngine(AsyncMock(), mock_config)
    engine.webhook_sender = AsyncMock()

    job_state = {
        "id": "job-1",
        "status": "finished",
        "webhook_url": "http://example.com/webhook",
        "result": {"final": "success"},
        "state_history": {"step1": "secret_data"},
        "security": {"token": "secret_token"},
        "timestamp": 12345.6,
    }

    await engine.send_job_webhook(job_state, "job_finished")

    # Verify the payload sent to webhook_sender
    payload = engine.webhook_sender.send.call_args[0][1]

    assert payload.result == {"final": "success"}  # Top-level result is allowed
    assert payload.security is None  # Blocked in compact mode


@pytest.mark.asyncio
async def test_worker_service_event_webhook_filtering(mock_config):
    """
    Verifies that worker-initiated event webhooks also respect privacy policy.
    Crucially, ensures webhooks are sent even if on_worker_event list is empty.
    """

    mock_config.DETAILED_API_RESPONSES = False
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    history = AsyncMock()
    engine = MagicMock()
    engine.config = mock_config
    engine.history_storage = history
    engine.webhook_sender = AsyncMock()
    engine.blueprint_contracts = {}

    # CRITICAL: Explicitly set to empty list to catch nesting errors
    engine.on_worker_event = []

    service = WorkerService(storage, history, mock_config, engine, MagicMock())

    # Mock job state with a webhook URL
    storage.get_job_state.return_value = {"id": "job-123", "webhook_url": "http://callback", "status": "running"}
    storage.get_worker_info.return_value = {"supported_skills": []}

    event_raw = {
        "event_id": "e1",
        "event_type": "progress",
        "worker_id": "worker_1",
        "origin_worker_id": "worker_1",
        "target_job_id": "job-123",
        "payload": {"percent": 50},
        "security": {"token": "secret"},
        "timestamp": 100,
    }

    with patch.object(service, "_verify_zero_trust", AsyncMock()):
        await service.process_worker_event("worker_1", event_raw)

    # 1. Verify webhook sent EVEN IF on_worker_event was empty
    assert engine.webhook_sender.send.called
    sent_payload = engine.webhook_sender.send.call_args[0][1]
    assert sent_payload.security is None


@pytest.mark.asyncio
async def test_worker_service_no_webhook_if_no_target_job(mock_config):
    """Verifies that no webhook is sent if event is not targeted to a specific job."""
    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    engine = MagicMock()
    engine.webhook_sender = AsyncMock()
    engine.on_worker_event = []

    service = WorkerService(storage, AsyncMock(), mock_config, engine, MagicMock())

    # Event WITHOUT target_job_id
    event_raw = {
        "event_id": "e2",
        "event_type": "system_log",
        "worker_id": "worker_1",
        "origin_worker_id": "worker_1",
        "target_job_id": None,
        "payload": {"msg": "hello"},
        "timestamp": 100,
    }

    with patch.object(service, "_verify_zero_trust", AsyncMock()):
        await service.process_worker_event("worker_1", event_raw)

    # Webhook sender should NOT be called
    assert not engine.webhook_sender.send.called
    # get_job_state should NOT be called either (optimization)
    assert not storage.get_job_state.called
    # Even if payload has a 'result' attribute, we check the actual asdict conversion
    # would happen later, but here we can check the dataclass fields.

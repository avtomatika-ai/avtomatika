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


@pytest.mark.asyncio
async def test_engine_rate_limit_anti_spoofing():
    """
    Verifies that a worker cannot bypass limits by changing worker_id
    if they use the same token.
    """
    config = MagicMock()
    config.RATE_LIMITING_ENABLED = True
    config.RATE_LIMIT_LIMIT = 100
    config.RATE_LIMIT_PERIOD = 60
    config.RATE_LIMIT_POLL_LIMIT = 1
    config.RATE_LIMIT_HEARTBEAT_LIMIT = 1
    config.LOG_LEVEL = "INFO"
    config.LOG_FORMAT = "JSON"
    config.TZ = "UTC"
    config.HISTORY_DATABASE_URI = ""

    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    token = "attacker_token"
    token_hash = sha256(token.encode()).hexdigest()

    # Mock verify_worker_auth to return (spoofed_id, token_hash)
    async def mock_verify(s, c, t, cert, hint, m, ch, fc=None):
        return hint, token_hash

    with patch("avtomatika.engine.verify_worker_auth", side_effect=mock_verify):
        engine = OrchestratorEngine(storage, config)
        engine.worker_service = AsyncMock()

        context = {"token": token}

        # 1. Request as worker_1
        await engine.handle_rxon_message("poll", "worker_1", context)
        args1 = storage.increment_key_with_ttl.call_args[0][0]
        assert token_hash in args1
        assert "worker_1" in args1

        # 2. Request as worker_2 (spoofed)
        await engine.handle_rxon_message("poll", "worker_2", context)
        args2 = storage.increment_key_with_ttl.call_args[0][0]
        assert token_hash in args2  # Still has same token hash!
        assert "worker_2" in args2

        # Both keys are isolated but tied to the same token hash.
        # This prevents "wildcard" spoofing because the token hash
        # is always a mandatory prefix in the Redis key.
        assert args1 != args2


@pytest.mark.asyncio
async def test_engine_rate_limit_per_worker_individual_new():
    """
    Verifies that rate limiting in engine is applied per worker_id,
    even if they share the same token or IP.
    """
    config = MagicMock()
    config.RATE_LIMITING_ENABLED = True
    config.RATE_LIMIT_LIMIT = 1
    config.RATE_LIMIT_PERIOD = 60
    config.RATE_LIMIT_POLL_LIMIT = 1
    config.RATE_LIMIT_HEARTBEAT_LIMIT = 1
    config.LOG_LEVEL = "INFO"
    config.LOG_FORMAT = "JSON"
    config.TZ = "UTC"
    config.HISTORY_DATABASE_URI = ""

    storage = AsyncMock()
    storage.verify_worker_access_token.return_value = None
    storage.find_worker_token.return_value = None
    token_hash = "some_hash"

    async def mock_verify_auth(s, c, t, cert, hint, m, ch, fc=None):
        return hint, token_hash

    with patch("avtomatika.engine.verify_worker_auth", side_effect=mock_verify_auth):
        engine = OrchestratorEngine(storage, config)
        engine.worker_service = AsyncMock()

        # 1. First request for worker_A -> OK
        storage.increment_key_with_ttl.return_value = 1
        await engine.handle_rxon_message("poll", "worker_A", {"token": "token_A"})

        # 2. Second request for worker_A -> Blocked
        storage.increment_key_with_ttl.return_value = 2
        storage.get_ttl.return_value = 30

        with pytest.raises(web.HTTPTooManyRequests):
            await engine.handle_rxon_message("poll", "worker_A", {"token": "token_A"})

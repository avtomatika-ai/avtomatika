from unittest.mock import AsyncMock, MagicMock

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin
import pytest
from aiohttp import web
from src.avtomatika.ratelimit import rate_limit_middleware_factory


@pytest.mark.asyncio
async def test_rate_limit_middleware_skips_workers():
    """
    Verifies that the global middleware skips internal worker paths,
    as they are handled by the engine for better precision.
    """
    storage = MagicMock()
    middleware = rate_limit_middleware_factory(storage, MagicMock(), 1, 60)
    handler = AsyncMock(return_value=web.Response(text="OK"))

    # Request to worker path
    request = MagicMock()
    request.path = "/_worker/tasks/next"

    # Even if storage would indicate a block, middleware should not call it
    response = await middleware(request, handler)

    assert response.status == 200
    assert not storage.increment_key_with_ttl.called


@pytest.mark.asyncio
async def test_rate_limit_blocks_clients_by_ip():
    """Tests that general clients are still blocked by IP in middleware."""
    storage = MagicMock()

    async def mock_inc(*args):
        return 2  # Blocked

    storage.increment_key_with_ttl.side_effect = mock_inc

    middleware = rate_limit_middleware_factory(storage, MagicMock(), 1, 60)
    handler = AsyncMock(return_value=web.Response(text="OK"))

    # Request to client path
    req = MagicMock()
    req.path = "/api/jobs"
    req.remote = "1.2.3.4"

    response = await middleware(req, handler)
    assert response.status == 429
    # No Retry-After for general clients
    assert "Retry-After" not in response.headers

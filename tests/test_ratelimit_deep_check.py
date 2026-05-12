# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from src.avtomatika.ratelimit import rate_limit_middleware_factory


@pytest.mark.asyncio
async def test_ratelimit_client_only_logic(aiohttp_client):
    """
    Comprehensive test for the rate limit middleware.
    Verifies that it applies only to clients and uses IP.
    """
    storage = AsyncMock()
    # Default limit 2, Period 60
    middleware = rate_limit_middleware_factory(storage, default_limit=2, period=60)

    app = web.Application(middlewares=[middleware])

    async def client_handler(request):
        return web.Response(text="client_ok")

    app.router.add_get("/api/action", client_handler)

    client = await aiohttp_client(app)

    # --- Test Case 1: Client API (Identifier should be IP) ---
    storage.increment_key_with_ttl.return_value = 1
    resp = await client.get("/api/action")
    assert resp.status == 200

    # Identifier should be '127.0.0.1' (default for aiohttp_client)
    storage.increment_key_with_ttl.assert_called_with("ratelimit:client:127.0.0.1:/api/action", 60)

    # --- Test Case 2: Blocking at default limit ---
    storage.increment_key_with_ttl.reset_mock()
    # Request 2: OK (limit is 2)
    storage.increment_key_with_ttl.return_value = 2
    resp = await client.get("/api/action")
    assert resp.status == 200

    # Request 3: Blocked (exceeds limit 2)
    storage.increment_key_with_ttl.return_value = 3
    storage.get_ttl.return_value = 60
    with patch("src.avtomatika.metrics.ratelimit_blocked_total") as mock_metric:
        resp = await client.get("/api/action")
        assert resp.status == 429
        # Verify metrics label used the IP
        mock_metric.inc.assert_called_once_with({"identifier": "127.0.0.1", "path": "/api/action"})

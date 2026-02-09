from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from src.avtomatika.ratelimit import rate_limit_middleware_factory


@pytest.mark.asyncio
async def test_ratelimit_identifier_logic(aiohttp_client):
    """
    Comprehensive test for the identifier logic in rate limit middleware.
    Verifies that worker_id is extracted from path if present, otherwise uses IP.
    """
    storage = AsyncMock()
    # Default limit 2, Period 60
    # Override: 'special' -> limit 1
    overrides = {"special": 1}
    middleware = rate_limit_middleware_factory(storage, default_limit=2, period=60, overrides=overrides)

    app = web.Application(middlewares=[middleware])

    # 1. Route with worker_id (simulating Worker API)
    async def worker_handler(request):
        return web.Response(text="worker_ok")

    app.router.add_get("/workers/{worker_id}/action", worker_handler)

    # 2. Route without worker_id (simulating Client API)
    async def client_handler(request):
        return web.Response(text="client_ok")

    app.router.add_get("/api/action", client_handler)

    # 3. Route with override
    app.router.add_get("/api/special", client_handler)

    client = await aiohttp_client(app)

    # --- Test Case 1: Worker API (Identifier should be worker_id) ---
    # We use a specific worker_id
    storage.increment_key_with_ttl.return_value = 1
    resp = await client.get("/workers/gpu-99/action")
    assert resp.status == 200

    # Verify the key in storage used the worker_id 'gpu-99'
    storage.increment_key_with_ttl.assert_called_with("ratelimit:gpu-99:/workers/gpu-99/action", 60)

    # --- Test Case 2: Client API (Identifier should be IP) ---
    storage.increment_key_with_ttl.reset_mock()
    storage.increment_key_with_ttl.return_value = 1
    resp = await client.get("/api/action")
    assert resp.status == 200

    # Identifier should be '127.0.0.1' (default for aiohttp_client)
    storage.increment_key_with_ttl.assert_called_with("ratelimit:127.0.0.1:/api/action", 60)

    # --- Test Case 3: Overrides (Verify different limits) ---
    storage.increment_key_with_ttl.reset_mock()
    # Request 1: OK (limit is 1 for 'special')
    storage.increment_key_with_ttl.return_value = 1
    resp = await client.get("/api/special")
    assert resp.status == 200

    # Request 2: Blocked (exceeds limit 1)
    storage.increment_key_with_ttl.return_value = 2
    with patch("src.avtomatika.metrics.ratelimit_blocked_total") as mock_metric:
        resp = await client.get("/api/special")
        assert resp.status == 429
        # Verify metrics label used the IP
        mock_metric.inc.assert_called_once_with({"identifier": "127.0.0.1", "path": "/api/special"})

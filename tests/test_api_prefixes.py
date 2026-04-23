# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

import pytest

from avtomatika.blueprint import Blueprint
from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def storage():
    return MemoryStorage()


async def create_test_engine(storage, **config_overrides):
    config = Config()
    for key, value in config_overrides.items():
        setattr(config, key, value)

    # Minimal setup to make engine work without full startup
    engine = OrchestratorEngine(storage, config)

    # Register a dummy blueprint to test client API routes
    bp = Blueprint("test_bp", api_endpoint="run")
    bp.start_state = "start"

    @bp.handler("start")
    async def start_handler():
        pass

    engine.register_blueprint(bp)

    engine.setup()
    return engine


@pytest.mark.asyncio
async def test_default_prefixes(aiohttp_client, storage):
    """Positive: Verify default paths (/api/... and /_public/...) work."""
    engine = await create_test_engine(storage)
    client = await aiohttp_client(engine.app)

    # Public API
    resp = await client.get("/_public/status")
    assert resp.status == 200

    # Client API
    resp = await client.post("/api/run")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_custom_client_prefix_public_stays_fixed(aiohttp_client, storage):
    """Positive: Verify custom client prefix works while public API stays at /_public/."""
    engine = await create_test_engine(storage, CLIENT_API_PREFIX="v1/client")
    client = await aiohttp_client(engine.app)

    # Public API stays at /_public/
    resp = await client.get("/_public/status")
    assert resp.status == 200

    # Client API moved to /v1/client/
    resp = await client.post("/v1/client/run")
    assert resp.status == 401

    # Old path should fail
    resp = await client.post("/api/run")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_empty_client_prefix(aiohttp_client, storage):
    """Positive: Verify API works at root if CLIENT_API_PREFIX is empty."""
    engine = await create_test_engine(storage, CLIENT_API_PREFIX="")
    client = await aiohttp_client(engine.app)

    # Client API now at root
    resp = await client.post("/run")
    assert resp.status == 401

    # Public still works
    resp = await client.get("/_public/status")
    assert resp.status == 200


@pytest.mark.asyncio
async def test_worker_api_stays_at_root(aiohttp_client, storage):
    """Positive: Verify Worker API remains at /_worker/ regardless of other prefixes."""
    engine = await create_test_engine(storage, CLIENT_API_PREFIX="hidden")
    client = await aiohttp_client(engine.app)

    # Worker API (rxon uses /_worker/ by default)
    resp = await client.get("/_worker/workers/register")
    assert resp.status in [405, 401]


@pytest.mark.asyncio
async def test_prefix_with_slashes_handling(aiohttp_client, storage):
    """Boundary: Verify that slashes in config are handled correctly."""
    engine = await create_test_engine(storage, CLIENT_API_PREFIX="//api//")
    client = await aiohttp_client(engine.app)

    # Should resolve to /api/run
    resp = await client.post("/api/run")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_access_denied_on_wrong_prefix(aiohttp_client, storage):
    """Negative: Accessing with slightly wrong prefix."""
    engine = await create_test_engine(storage, CLIENT_API_PREFIX="api")
    client = await aiohttp_client(engine.app)

    # Missing segment
    resp = await client.post("/run")
    assert resp.status == 404

    # Extra segment
    resp = await client.post("/api/extra/run")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_nested_blueprint_path(aiohttp_client, storage):
    """Positive: Verify nested blueprint endpoints work with custom prefixes."""
    config = Config()
    config.CLIENT_API_PREFIX = "v1"
    engine = OrchestratorEngine(storage, config)

    bp = Blueprint("nested_bp", api_endpoint="group/sub/run")
    bp.start_state = "start"

    @bp.handler("start")
    async def h():
        pass

    engine.register_blueprint(bp)
    engine.setup()

    client = await aiohttp_client(engine.app)
    resp = await client.post("/v1/group/sub/run")
    assert resp.status == 401

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import importlib.metadata
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Patch importlib.metadata.version to avoid DeprecationWarning in Python 3.13
# triggered by dependencies like fakeredis.
_original_version = importlib.metadata.version


def _patched_version(package_name):
    try:
        return importlib.metadata.distribution(package_name).metadata.get("Version")
    except Exception:
        return None


importlib.metadata.version = _patched_version

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from fakeredis import aioredis as redis  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import (  # noqa: E402
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

# Ensure src is in python path for correct import resolution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from avtomatika.app_keys import (  # noqa: E402
    STORAGE_KEY,
)
from avtomatika.client_config_loader import load_client_configs_to_redis  # noqa: E402
from avtomatika.config import Config  # noqa: E402
from avtomatika.engine import ENGINE_KEY, OrchestratorEngine  # noqa: E402
from avtomatika.storage.memory import MemoryStorage  # noqa: E402
from avtomatika.storage.redis import RedisStorage  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_verify_zero_trust(request):
    excluded_files = ["test_worker_security_new.py", "test_rxon_b8_features.py"]
    if any(ef in request.node.fspath.strpath for ef in excluded_files):
        yield
        return

    with patch("avtomatika.services.worker_service.WorkerService._verify_zero_trust", new_callable=AsyncMock) as m:
        yield m


# Define AppKeys globally for tests
# (Removed local STORAGE_KEY definition, imported from app_keys instead)


@pytest.fixture(scope="session", autouse=True)
def tracing_setup():
    provider = TracerProvider()

    processor = SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    yield

    provider.shutdown()


@pytest.fixture
def config():
    """Provides a default Config instance."""
    c = Config()
    c.INSTANCE_ID = "test-consumer-1"
    c.REDIS_STREAM_BLOCK_MS = 0  # Disable blocking for tests
    return c


@pytest_asyncio.fixture
async def redis_client():
    """Function-scoped fixture to create and clean up a fakeredis client."""
    client = redis.FakeRedis(decode_responses=False)
    yield client
    await client.aclose()


@pytest.fixture
def memory_storage():
    """Provides a MemoryStorage instance."""
    return MemoryStorage()


@pytest.fixture
def redis_storage(redis_client, config):
    """Provides a RedisStorage instance, using the managed redis_client."""
    return RedisStorage(redis_client, consumer_name=config.INSTANCE_ID, min_idle_time_ms=100)


@pytest.fixture
def metrics_mock():
    return MagicMock()


@pytest.fixture
def engine(storage, config, metrics_mock):
    """Provides an OrchestratorEngine instance."""
    engine = OrchestratorEngine(storage, config)
    engine.metrics = metrics_mock
    engine.setup()  # setup is now synchronous
    return engine


@pytest_asyncio.fixture
async def app(request, config, redis_storage, metrics_mock):
    """
    The main fixture for creating the aiohttp application for tests.
    """
    storage = redis_storage
    engine = OrchestratorEngine(storage, config)
    engine.metrics = metrics_mock

    # Pre-load client configs for tests
    current_dir = os.path.dirname(os.path.abspath(__file__))
    clients_toml_path = os.path.join(current_dir, "clients.toml")
    await load_client_configs_to_redis(storage, config_path=clients_toml_path)

    # Register blueprints engine.setup()ed from the test if any
    if hasattr(request, "param"):
        if request.param.get("extra_blueprints"):
            for bp in request.param["extra_blueprints"]:
                engine.register_blueprint(bp)
        if request.param.get("workers_config_path"):
            config.WORKERS_CONFIG_PATH = request.param["workers_config_path"]

    engine.setup()
    engine.app[STORAGE_KEY] = storage
    engine.app[ENGINE_KEY] = engine

    yield engine.app

    await engine.on_shutdown(engine.app)
    if hasattr(storage, "close"):
        await storage.close()

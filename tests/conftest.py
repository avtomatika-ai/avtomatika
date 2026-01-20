import os
import sys

import pytest
import pytest_asyncio
from aiohttp.web import AppKey
from fakeredis import aioredis as redis
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from src.avtomatika.client_config_loader import load_client_configs_to_redis
from src.avtomatika.config import Config
from src.avtomatika.engine import ENGINE_KEY, OrchestratorEngine
from src.avtomatika.storage.base import StorageBackend
from src.avtomatika.storage.memory import MemoryStorage
from src.avtomatika.storage.redis import RedisStorage

# Define AppKeys globally for tests
STORAGE_KEY = AppKey("storage", StorageBackend)


@pytest.fixture(scope="session", autouse=True)
def tracing_setup():
    provider = TracerProvider()

    processor = SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    yield

    provider.shutdown()


# @pytest.fixture(scope="session")
# def event_loop():
#     """Create an instance of the default event loop for each test session."""
#     loop = asyncio.get_event_loop_policy().new_event_loop()
#     yield loop
#     loop.close()


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


@pytest_asyncio.fixture
async def app(request, config, redis_storage):
    """
    The main fixture for creating the aiohttp application for tests.
    It's function-scoped, ensuring a clean state for each test.
    It can be parameterized to accept extra blueprints for registration.
    """
    storage = redis_storage
    engine = OrchestratorEngine(storage, config)

    # Pre-load client configs for tests
    current_dir = os.path.dirname(os.path.abspath(__file__))
    clients_toml_path = os.path.join(current_dir, "clients.toml")
    await load_client_configs_to_redis(storage, config_path=clients_toml_path)

    # Register blueprints passed from the test if any
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

    if hasattr(storage, "close"):
        await storage.close()

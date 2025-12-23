import os
from unittest.mock import AsyncMock

import pytest
from src.avtomatika.client_config_loader import load_client_configs_to_redis
from src.avtomatika.worker_config_loader import load_worker_configs_to_redis


@pytest.mark.asyncio
async def test_client_config_loader_invalid_toml():
    """Verifies that client config loader raises ValueError on invalid TOML."""
    storage = AsyncMock()
    path = "invalid_clients.toml"
    with open(path, "w") as f:
        f.write("this is [not] valid toml = = =")

    try:
        with pytest.raises(ValueError, match="Invalid client configuration file"):
            await load_client_configs_to_redis(storage, path)
    finally:
        if os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_client_config_loader_invalid_quota_type():
    """Verifies that client config loader validates monthly_attempts type."""
    storage = AsyncMock()
    path = "invalid_quota.toml"
    with open(path, "w") as f:
        f.write("""
[client]
token = "abc"
monthly_attempts = "not-an-int"
""")

    try:
        with pytest.raises(ValueError, match="Invalid quota type for client 'client'"):
            await load_client_configs_to_redis(storage, path)
    finally:
        if os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_worker_config_loader_invalid_structure():
    """Verifies that worker config loader validates that config is a table."""
    storage = AsyncMock()
    path = "invalid_struct.toml"
    with open(path, "w") as f:
        f.write("""
worker_id = "not-a-table"
""")

    try:
        with pytest.raises(ValueError, match="Invalid configuration for worker 'worker_id'"):
            await load_worker_configs_to_redis(storage, path)
    finally:
        if os.path.exists(path):
            os.remove(path)

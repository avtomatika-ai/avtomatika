import os
from unittest.mock import AsyncMock

import pytest
from src.avtomatika.client_config_loader import load_client_configs_to_redis


@pytest.mark.asyncio
async def test_load_client_configs_to_redis():
    """Tests that client configs are correctly loaded into Redis."""
    storage = AsyncMock()
    config_path = "test_clients.toml"

    # Create a dummy config file
    with open(config_path, "w") as f:
        f.write("""
[client-1]
token = "token-1"
plan = "premium"
monthly_attempts = 1000

[client-2]
token = "token-2"
plan = "free"
""")

    await load_client_configs_to_redis(storage, config_path)

    assert storage.save_client_config.call_count == 2
    storage.save_client_config.assert_any_call("token-1", {"token": "token-1", "plan": "premium"})
    storage.save_client_config.assert_any_call("token-2", {"token": "token-2", "plan": "free"})
    storage.initialize_client_quota.assert_called_once_with("token-1", 1000)

    os.remove(config_path)


@pytest.mark.asyncio
async def test_load_client_configs_file_not_found(caplog):
    """Tests that a warning is logged when the config file is not found."""
    storage = AsyncMock()
    await load_client_configs_to_redis(storage, "non_existent_file.toml")
    assert "Client config file not found" in caplog.text


@pytest.mark.asyncio
async def test_load_client_configs_missing_token(caplog):
    """Tests that a ValueError is raised when a client config is missing a token."""
    storage = AsyncMock()
    config_path = "missing_token_clients.toml"

    with open(config_path, "w") as f:
        f.write("""
[client-1]
plan = "premium"
""")

    try:
        with pytest.raises(ValueError, match="Missing token for client 'client-1'"):
            await load_client_configs_to_redis(storage, config_path)
    finally:
        if os.path.exists(config_path):
            os.remove(config_path)

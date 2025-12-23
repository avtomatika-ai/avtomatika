import hashlib
import os
from unittest.mock import AsyncMock

import pytest
from src.avtomatika.worker_config_loader import load_worker_configs_to_redis


@pytest.mark.asyncio
async def test_load_worker_configs_to_redis():
    """Tests that worker configs are correctly loaded into Redis."""
    storage = AsyncMock()
    config_path = "test_workers.toml"

    # Create a dummy config file
    with open(config_path, "w") as f:
        f.write("""
[worker-1]
token = "token-1"

[worker-2]
token = "token-2"
""")

    await load_worker_configs_to_redis(storage, config_path)

    hashed_token_1 = hashlib.sha256(b"token-1").hexdigest()
    hashed_token_2 = hashlib.sha256(b"token-2").hexdigest()

    assert storage.set_worker_token.call_count == 2
    storage.set_worker_token.assert_any_call("worker-1", hashed_token_1)
    storage.set_worker_token.assert_any_call("worker-2", hashed_token_2)

    os.remove(config_path)


@pytest.mark.asyncio
async def test_load_worker_configs_file_not_found(caplog):
    """Tests that a warning is logged when the config file is not found."""
    storage = AsyncMock()
    await load_worker_configs_to_redis(storage, "non_existent_file.toml")
    assert "Worker config file not found" in caplog.text


@pytest.mark.asyncio
async def test_load_worker_configs_parse_error(caplog):
    """Tests that an error is logged and ValueError is raised when the config file is invalid."""
    storage = AsyncMock()
    config_path = "invalid_workers.toml"

    with open(config_path, "w") as f:
        f.write("invalid toml")

    try:
        with pytest.raises(ValueError, match="Invalid worker configuration file"):
            await load_worker_configs_to_redis(storage, config_path)
    finally:
        if os.path.exists(config_path):
            os.remove(config_path)

    assert "Failed to load or parse worker config file" in caplog.text

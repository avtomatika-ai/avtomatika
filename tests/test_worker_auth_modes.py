# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from avtomatika.config import Config
from avtomatika.security import verify_worker_auth
from avtomatika.utils.crypto import encrypt_token
from avtomatika.worker_config_loader import load_worker_configs_to_redis


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.verify_worker_access_token = AsyncMock(return_value=None)
    storage.get_worker_token = AsyncMock(return_value=None)
    return storage


@pytest.fixture
def config():
    c = Config()
    c.GLOBAL_WORKER_TOKEN = "global-secret"
    return c


@pytest.mark.asyncio
async def test_mixed_mode_auth(mock_storage, config):
    """Mixed mode: both mTLS and tokens work."""
    config.WORKER_AUTH_MODE = "mixed"

    # 1. mTLS
    res = await verify_worker_auth(mock_storage, config, None, "worker1", "worker1")
    assert res == "worker1"

    # 2. Individual Token
    mock_storage.get_worker_token.return_value = "token1"
    res = await verify_worker_auth(mock_storage, config, "token1", None, "worker1")
    assert res == "worker1"

    # 3. Global Token
    mock_storage.get_worker_token.return_value = None  # Reset individual token
    res = await verify_worker_auth(mock_storage, config, "global-secret", None, "any-worker")
    assert res == "any-worker"


@pytest.mark.asyncio
async def test_mtls_only_mode_auth(mock_storage, config):
    """mtls-only: static tokens are rejected, mTLS and STS are accepted."""
    config.WORKER_AUTH_MODE = "mtls-only"

    # 1. mTLS - OK
    res = await verify_worker_auth(mock_storage, config, None, "worker1", "worker1")
    assert res == "worker1"

    # 2. STS Token - OK
    mock_storage.verify_worker_access_token.return_value = "worker1"
    res = await verify_worker_auth(mock_storage, config, "sts-token", None, "worker1")
    assert res == "worker1"

    # 3. Static Token - REJECTED
    mock_storage.verify_worker_access_token.return_value = None  # Ensure it fails STS check
    mock_storage.get_worker_token.return_value = "token1"
    with pytest.raises(PermissionError, match="Invalid authentication method"):
        await verify_worker_auth(mock_storage, config, "token1", None, "worker1")

    # 4. Global Token - REJECTED
    with pytest.raises(PermissionError, match="Invalid authentication method"):
        await verify_worker_auth(mock_storage, config, "global-secret", None, "any-worker")


@pytest.mark.asyncio
async def test_token_only_mode_auth(mock_storage, config):
    """token-only: mTLS certs are ignored/rejected, tokens are required."""
    config.WORKER_AUTH_MODE = "token-only"

    # 1. mTLS alone - REJECTED (Missing header)
    with pytest.raises(PermissionError, match="Missing X-Worker-Token header"):
        await verify_worker_auth(mock_storage, config, None, "worker1", "worker1")

    # 2. Static Token - OK
    mock_storage.get_worker_token.return_value = "token1"
    res = await verify_worker_auth(mock_storage, config, "token1", None, "worker1")
    assert res == "worker1"

    # 3. Global Token - OK
    mock_storage.get_worker_token.return_value = None  # Reset
    res = await verify_worker_auth(mock_storage, config, "global-secret", None, "any-worker")
    assert res == "any-worker"

    # 4. Token + mTLS - OK (mTLS is ignored in favor of token processing)
    res = await verify_worker_auth(mock_storage, config, "global-secret", "worker1", "any-worker")
    assert res == "any-worker"


@pytest.mark.asyncio
async def test_load_worker_configs_skips_in_mtls_only(mock_storage):
    """Verify that load_worker_configs_to_redis skips loading when mode is mtls-only."""
    # Mode is mtls-only
    await load_worker_configs_to_redis(mock_storage, "any_path.toml", auth_mode="mtls-only")

    # storage.set_worker_token should NOT have been called
    assert not mock_storage.set_worker_token.called


@pytest.mark.asyncio
async def test_worker_token_encryption(mock_storage, config):
    """Verify that worker tokens are encrypted in Redis and decrypted during auth."""
    config.REDIS_ENCRYPTION_KEY = "test-encryption-key"

    config.WORKER_AUTH_MODE = "mixed"
    token = "secret-token"
    worker_id = "worker1"

    # 1. Encrypt token for storage simulation
    cipher = encrypt_token(token, config.REDIS_ENCRYPTION_KEY)
    mock_storage.get_worker_token.return_value = cipher

    # 2. Authenticate - should succeed as it decrypts internally
    res = await verify_worker_auth(mock_storage, config, token, None, worker_id)
    assert res == worker_id

    # 3. Authenticate with wrong encryption key - should fail
    config.REDIS_ENCRYPTION_KEY = "wrong-key"
    with pytest.raises(PermissionError, match="Failed to decrypt worker token"):
        await verify_worker_auth(mock_storage, config, token, None, worker_id)


@pytest.mark.asyncio
async def test_config_validation():
    """Verify that invalid WORKER_AUTH_MODE raises ValueError."""
    with (
        patch.dict(os.environ, {"WORKER_AUTH_MODE": "invalid"}),
        pytest.raises(ValueError, match="Invalid WORKER_AUTH_MODE"),
    ):
        Config()

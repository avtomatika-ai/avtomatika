# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock

import pytest

from avtomatika.config import Config
from avtomatika.security import verify_worker_auth
from avtomatika.utils.crypto import encrypt_token


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.get_worker_token = AsyncMock(return_value=None)
    storage.verify_worker_access_token = AsyncMock(return_value=None)
    storage.find_worker_token = AsyncMock(return_value=None)
    storage.verify_worker_access_token = AsyncMock(return_value=None)
    storage.find_worker_token = AsyncMock(return_value=None)
    return storage


@pytest.fixture
def config():
    return Config()


@pytest.mark.asyncio
async def test_mixed_mode_auth(mock_storage, config):
    """Mixed mode: both mTLS and tokens work."""
    config.WORKER_AUTH_MODE = "mixed"

    # 1. mTLS
    res = await verify_worker_auth(
        mock_storage, config, None, "worker1", "worker1", metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == "worker1"

    # 2. Individual Token
    mock_storage.find_worker_token.return_value = "token1"
    res = await verify_worker_auth(
        mock_storage, config, "token1", None, "worker1", metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == "worker1"

    # 3. STS Token
    mock_storage.verify_worker_access_token.return_value = "worker1"
    res = await verify_worker_auth(
        mock_storage, config, "sts-token", None, "worker1", metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == "worker1"


@pytest.mark.asyncio
async def test_mtls_only_mode_auth(mock_storage, config):
    """mtls-only: static tokens are rejected, mTLS and STS are accepted."""
    config.WORKER_AUTH_MODE = "mtls-only"

    # 1. mTLS - OK
    res = await verify_worker_auth(
        mock_storage, config, None, "worker1", "worker1", metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == "worker1"

    # 2. STS Token - OK
    mock_storage.verify_worker_access_token.return_value = "worker1"
    res = await verify_worker_auth(
        mock_storage, config, "sts-token", None, "worker1", metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == "worker1"

    # 3. Static Token - REJECTED
    mock_storage.verify_worker_access_token.return_value = None  # Ensure it fails STS check
    mock_storage.find_worker_token.return_value = "token1"
    with pytest.raises(PermissionError, match="Invalid authentication method"):
        await verify_worker_auth(
            mock_storage, config, "token1", None, "worker1", metrics=MagicMock(), token_hash_cache={}
        )

    # 4. Global Token - REJECTED
    with pytest.raises(PermissionError, match="Invalid authentication method"):
        await verify_worker_auth(
            mock_storage, config, "global-secret", None, "any-worker", metrics=MagicMock(), token_hash_cache={}
        )


@pytest.mark.asyncio
async def test_token_only_mode_auth(mock_storage, config):
    """token-only: mTLS certs are ignored/rejected, tokens are required."""
    config.WORKER_AUTH_MODE = "token-only"

    # 1. mTLS alone - REJECTED (Missing header)
    with pytest.raises(PermissionError, match="Missing X-Worker-Token header"):
        await verify_worker_auth(
            mock_storage, config, None, "worker1", "worker1", metrics=MagicMock(), token_hash_cache={}
        )

    # 2. Static Token - OK
    mock_storage.find_worker_token.return_value = "token1"
    res = await verify_worker_auth(
        mock_storage, config, "token1", None, "worker1", metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == "worker1"


@pytest.mark.asyncio
async def test_worker_token_encryption(mock_storage, config):
    """Verify that worker tokens are encrypted in Redis and decrypted during auth."""
    config.REDIS_ENCRYPTION_KEY = "test-encryption-key"

    config.WORKER_AUTH_MODE = "mixed"
    token = "secret-token"
    worker_id = "worker1"

    # 1. Encrypt token for storage simulation
    cipher = encrypt_token(token, config.REDIS_ENCRYPTION_KEY)
    mock_storage.find_worker_token.return_value = cipher

    # 2. Authenticate - should succeed as it decrypts internally
    res = await verify_worker_auth(
        mock_storage, config, token, None, worker_id, metrics=MagicMock(), token_hash_cache={}
    )
    assert res[0] == worker_id

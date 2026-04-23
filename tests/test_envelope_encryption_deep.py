# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

import time
from unittest.mock import MagicMock, patch

import pytest
from rxon.security import sign_payload

from avtomatika.config import Config
from avtomatika.security import verify_worker_auth
from avtomatika.services.worker_service import WorkerService
from avtomatika.storage.memory import MemoryStorage
from avtomatika.utils.crypto import decrypt_token, encrypt_token
from avtomatika.worker_config_loader import load_worker_configs_to_redis


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def config():
    c = Config()
    c.WORKER_AUTH_MODE = "mixed"
    c.REDIS_ENCRYPTION_KEY = "master-secret-key"
    c.GLOBAL_WORKER_TOKEN = "global-secret"
    return c


@pytest.mark.asyncio
async def test_encryption_at_rest_in_storage(storage, config):
    """1. Physical check: Verify that tokens are actually encrypted in storage."""
    worker_id = "worker_1"
    raw_token = "very-secret-token-123"

    # Load via loader (this should trigger encryption)
    with (
        patch("avtomatika.worker_config_loader.exists", return_value=True),
        patch("avtomatika.worker_config_loader.load", return_value={worker_id: {"token": raw_token}}),
        patch("avtomatika.worker_config_loader.open", MagicMock()),
    ):
        await load_worker_configs_to_redis(storage, "mock.toml", config.WORKER_AUTH_MODE, config.REDIS_ENCRYPTION_KEY)

    # Directly access storage to see the "raw" value
    stored_value = await storage.get_worker_token(worker_id)

    assert stored_value is not None
    assert stored_value != raw_token
    assert stored_value.startswith("gAAAA")  # Fernet tokens start with this

    # Verify we can decrypt it manually
    decrypted = decrypt_token(stored_value, config.REDIS_ENCRYPTION_KEY)
    assert decrypted == raw_token


@pytest.mark.asyncio
async def test_auth_with_encryption(storage, config):
    """2. Auth check: verify_worker_auth should transparently decrypt."""
    worker_id = "worker_2"
    raw_token = "token-abc"

    # Manually put encrypted token into storage
    encrypted = encrypt_token(raw_token, config.REDIS_ENCRYPTION_KEY)
    await storage.set_worker_token(worker_id, encrypted)

    # Attempt authentication with the PLAIN token
    # It should pass because verify_worker_auth will decrypt the stored one for comparison
    authenticated_id = await verify_worker_auth(storage, config, raw_token, None, worker_id)
    assert authenticated_id == worker_id


@pytest.mark.asyncio
async def test_hmac_verification_with_encryption(storage, config):
    """3. HMAC check: worker_service should decrypt token to verify signatures."""
    worker_id = "worker_3"
    raw_token = "token-xyz"

    # 1. Encrypt and store
    encrypted = encrypt_token(raw_token, config.REDIS_ENCRYPTION_KEY)
    await storage.set_worker_token(worker_id, encrypted)

    # 2. Prepare worker_service
    service = WorkerService(storage, MagicMock(), config, MagicMock())

    # 3. Create a signed payload using the PLAIN token
    payload = {"event": "test", "timestamp": int(time.time()), "data": {"foo": "bar"}}
    signature = sign_payload(payload, raw_token)
    payload["security"] = {"signature": signature, "signer_id": worker_id}

    # 4. Verify - should pass as service will decrypt the key from Redis to check HMAC
    await service._verify_zero_trust(payload, worker_id)
    # If no exception raised, it passed


@pytest.mark.asyncio
async def test_wrong_encryption_key_fails(storage, config):
    """4. Security check: changing the master key invalidates existing tokens."""
    worker_id = "worker_4"
    raw_token = "token-123"

    # Store with Key A
    encrypted = encrypt_token(raw_token, "key-A")
    await storage.set_worker_token(worker_id, encrypted)

    # Try to authenticate with Key B in config
    config.REDIS_ENCRYPTION_KEY = "key-B"

    with pytest.raises(PermissionError, match="Failed to decrypt worker token"):
        await verify_worker_auth(storage, config, raw_token, None, worker_id)


@pytest.mark.asyncio
async def test_mixed_tokens_support(storage, config):
    """5. Migration check: encryption only happens if REDIS_ENCRYPTION_KEY is set."""
    worker_id_enc = "worker_enc"
    worker_id_plain = "worker_plain"

    # 1. Encrypted token
    config.REDIS_ENCRYPTION_KEY = "secret"
    await storage.set_worker_token(worker_id_enc, encrypt_token("pass1", "secret"))

    # 2. Plain token (simulating existing data or disabled encryption)
    # We bypass the encryption by temporarily removing the key
    with patch.object(config, "REDIS_ENCRYPTION_KEY", None):
        await storage.set_worker_token(worker_id_plain, "pass2")

    # Auth encrypted - OK
    assert await verify_worker_auth(storage, config, "pass1", None, worker_id_enc) == worker_id_enc

    # Auth plain - FAILS because encryption is ACTIVE globally and it tries to decrypt "pass2" as Fernet
    # This is intended: once encryption is on, all master tokens in Redis MUST be encrypted.
    with pytest.raises(PermissionError):
        await verify_worker_auth(storage, config, "pass2", None, worker_id_plain)

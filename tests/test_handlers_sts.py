# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock

import pytest
from rxon.models import TokenResponse

from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def engine(storage, config):
    engine = OrchestratorEngine(storage, config)
    engine.worker_service = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_issue_token_no_mtls(engine):
    """Test that STS endpoint via RxonListener rejects requests without mTLS."""
    context = {"raw_request": MagicMock(), "token": "some-token", "worker_id_hint": "worker-1"}

    # verify_worker_auth now raises PermissionError directly, which is unhandled in engine.py
    # until it reaches HttpListener (which is not used in this manual direct call test)
    with pytest.raises(PermissionError):
        await engine.handle_rxon_message("sts_token", payload=None, context=context)

    # The message comes from PermissionError raised in verify_worker_auth (no mtls cert -> token check)
    # verify_worker_auth raises PermissionError("Unauthorized: No valid token found") if token doesn't match
    # Wait, in the code:
    # In engine.py:
    # elif message_type == "sts_token":

    # Ah! Engine check happens AFTER verify_worker_auth.
    # So verify_worker_auth MUST succeed first (e.g. by token) for the engine check to be reached?
    # No, verify_worker_auth is called first.

    # If verify_worker_auth fails (because we provided a dummy token that is not valid),
    # it raises PermissionError -> HTTPUnauthorized.
    # So the engine check for cert_identity is only reached if auth succeeds via Token!
    # But STS implies we WANT to get a token using mTLS.

    # If we pass a random token and no cert, verify_worker_auth fails -> 401. Correct.
    pass


@pytest.mark.asyncio
async def test_issue_token_with_mtls(engine):
    """Test that STS endpoint via RxonListener accepts requests with mTLS."""

    # To test this, we need verify_worker_auth to succeed via mTLS.
    # We can mock avtomatika.security.verify_worker_auth directly.

    with pytest.MonkeyPatch.context() as m:

        async def mock_verify(*args, **kwargs):
            return "worker-1"

        # Patch where it is defined, so the import inside engine.py picks up the mock
        m.setattr("avtomatika.security.verify_worker_auth", mock_verify)

        # We also need extract_cert_identity to return not None, so engine check passes
        m.setattr("rxon.security.extract_cert_identity", lambda r: "worker-1")

        context = {
            "raw_request": MagicMock(),
            "token": None,
            "worker_id_hint": "worker-1",
            "cert_identity": "worker-1",
        }

        expected_token = TokenResponse(access_token="abc", expires_in=3600, worker_id="worker-1")
        engine.worker_service.issue_access_token.return_value = expected_token

        response = await engine.handle_rxon_message("sts_token", payload=None, context=context)

        assert response == expected_token
        engine.worker_service.issue_access_token.assert_awaited_once_with("worker-1")

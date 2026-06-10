# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from collections.abc import Awaitable, Callable
from hashlib import sha256
from logging import getLogger
from time import time
from typing import Any

from aiohttp import web
from rxon.constants import AUTH_HEADER_CLIENT, AUTH_HEADER_WORKER

from .app_keys import CLIENT_CONFIG_KEY
from .config import Config
from .metrics import Metrics
from .storage.base import StorageBackend
from .utils.crypto import decrypt_token

logger = getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.Response]]


async def verify_worker_auth(
    storage: StorageBackend,
    config: Config,
    token: str | None,
    cert_identity: str | None,
    worker_id_hint: str | None,
    metrics: Metrics,
    token_hash_cache: dict[str, tuple[float, str]],
    fernet_cache: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Verifies worker authentication using token or mTLS.
    Returns (authenticated_worker_id, credential_hash).
    Raises ValueError (400), PermissionError (401/403) on failure.
    """
    from .telemetry import trace  # noqa: PLC0415

    span = trace.get_current_span()
    mode = config.WORKER_AUTH_MODE
    span.set_attribute("auth.mode", mode)

    try:
        if mode != "token-only" and cert_identity:
            span.set_attribute("auth.method", "mtls")
            if worker_id_hint and cert_identity != worker_id_hint:
                metrics.security_identity_mismatch_total.add(1, {"cert_cn": cert_identity, "worker_id": worker_id_hint})
                raise PermissionError(
                    f"Unauthorized: Certificate CN '{cert_identity}' does not match worker_id '{worker_id_hint}'"
                )
            return cert_identity, cert_identity

        if not token:
            span.set_attribute("auth.method", "none")
            reason = "missing_credentials"
            metrics.security_auth_failures_total.add(1, {"reason": reason, "mode": mode})
            if mode == "mtls-only":
                raise PermissionError("Unauthorized: Client certificate required.")
            if mode == "token-only":
                raise PermissionError(f"Unauthorized: Missing {AUTH_HEADER_WORKER} header.")

            # Mixed mode
            raise PermissionError(f"Unauthorized: Missing {AUTH_HEADER_WORKER} header or client certificate.")

        now = time()
        if token in token_hash_cache:
            expiry, hashed_provided_token = token_hash_cache[token]
            if now > expiry:
                del token_hash_cache[token]
                hashed_provided_token = sha256(token.encode()).hexdigest()
                token_hash_cache[token] = (now + 60.0, hashed_provided_token)
        else:
            hashed_provided_token = sha256(token.encode()).hexdigest()
            if len(token_hash_cache) >= 10000:
                # Simple cleanup of expired items if cache is full
                expired_keys = [k for k, v in token_hash_cache.items() if now > v[0]]
                for k in expired_keys:
                    del token_hash_cache[k]
                if len(token_hash_cache) >= 10000:
                    token_hash_cache.clear()

            token_hash_cache[token] = (now + 60.0, hashed_provided_token)

        token_worker_id: str | None = await storage.verify_worker_access_token(hashed_provided_token)
        if token_worker_id:
            span.set_attribute("auth.method", "sts")
            if worker_id_hint and token_worker_id != worker_id_hint:
                metrics.security_auth_failures_total.add(1, {"reason": "worker_id_mismatch", "method": "sts"})
                raise PermissionError(
                    f"Unauthorized: Access Token belongs to '{token_worker_id}', but request is for '{worker_id_hint}'"
                )
            return token_worker_id, hashed_provided_token

        if mode == "mtls-only":
            metrics.security_auth_failures_total.add(1, {"reason": "mtls_required", "method": "token"})
            raise PermissionError("Unauthorized: Invalid authentication method.")

        if not worker_id_hint:
            if config.GLOBAL_WORKER_TOKEN and token == config.GLOBAL_WORKER_TOKEN:
                span.set_attribute("auth.method", "global_token")
                return "unknown_authenticated_by_global_token", hashed_provided_token

            metrics.security_auth_failures_total.add(1, {"reason": "missing_worker_id", "method": "token"})
            raise PermissionError("Unauthorized: Invalid token or missing worker_id hint")

        expected_token = await storage.find_worker_token(worker_id_hint)

        if expected_token:
            span.set_attribute("auth.method", "individual_token")
            # Decrypt if encryption is enabled
            if config.REDIS_ENCRYPTION_KEY and config.encrypt_worker_tokens:
                decrypted = decrypt_token(expected_token, config.REDIS_ENCRYPTION_KEY, cache=fernet_cache)
                if decrypted is None:
                    metrics.security_auth_failures_total.add(
                        1, {"reason": "decryption_failed", "worker_id": worker_id_hint}
                    )
                    raise PermissionError("Unauthorized: Failed to decrypt worker token. Key might be invalid.")
                expected_token = decrypted

            if token == expected_token:
                return worker_id_hint, hashed_provided_token

            metrics.security_auth_failures_total.add(1, {"reason": "invalid_token", "worker_id": worker_id_hint})
            raise PermissionError("Unauthorized: Invalid individual worker token")

        if config.GLOBAL_WORKER_TOKEN and token == config.GLOBAL_WORKER_TOKEN:
            span.set_attribute("auth.method", "global_token")
            return worker_id_hint, hashed_provided_token

        metrics.security_auth_failures_total.add(1, {"reason": "no_token_found", "worker_id": worker_id_hint})
        raise PermissionError("Unauthorized: No valid token found")
    except Exception as e:
        if not isinstance(e, PermissionError):
            span.record_exception(e)
        raise e


def client_auth_middleware_factory(
    storage: StorageBackend,
) -> Any:
    """Middleware factory for client authentication.
    It checks for a client token and attaches the client config to the request.
    """

    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.Response:
        token = request.headers.get(AUTH_HEADER_CLIENT)
        if not token:
            return web.json_response(
                {"error": f"Missing {AUTH_HEADER_CLIENT} header"},
                status=401,
            )

        client_config = await storage.get_client_config(token)
        if not client_config:
            return web.json_response(
                {"error": "Unauthorized: Invalid token"},
                status=401,
            )

        # Attach client config to the request for handlers to use
        request[CLIENT_CONFIG_KEY] = client_config
        return await handler(request)

    return middleware

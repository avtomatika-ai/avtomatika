# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from hashlib import sha256
from time import time
from typing import Any, Awaitable, Callable

from aiohttp import web

from .config import Config
from .constants import AUTH_HEADER_CLIENT, AUTH_HEADER_WORKER
from .storage.base import StorageBackend

Handler = Callable[[web.Request], Awaitable[web.Response]]


# HLN Optimization: Cache token hashes to avoid repeated SHA256 computation
# Format: {token: (expiry, hashed_token)}
_TOKEN_HASH_CACHE: dict[str, tuple[float, str]] = {}


async def verify_worker_auth(
    storage: StorageBackend,
    config: Config,
    token: str | None,
    cert_identity: str | None,
    worker_id_hint: str | None,
) -> str:
    """
    Verifies worker authentication using token or mTLS.
    Returns authenticated worker_id.
    Raises ValueError (400), PermissionError (401/403) on failure.
    """
    # mTLS Check
    if cert_identity:
        if worker_id_hint and cert_identity != worker_id_hint:
            raise PermissionError(
                f"Unauthorized: Certificate CN '{cert_identity}' does not match worker_id '{worker_id_hint}'"
            )
        return cert_identity

    # Token Check
    if not token:
        raise PermissionError(f"Missing {AUTH_HEADER_WORKER} header or client certificate")

    # Use cache for hash computation with 60s TTL
    now = time()
    if token in _TOKEN_HASH_CACHE:
        expiry, hashed_provided_token = _TOKEN_HASH_CACHE[token]
        if now > expiry:
            del _TOKEN_HASH_CACHE[token]
            hashed_provided_token = sha256(token.encode()).hexdigest()
            _TOKEN_HASH_CACHE[token] = (now + 60.0, hashed_provided_token)
    else:
        hashed_provided_token = sha256(token.encode()).hexdigest()
        if len(_TOKEN_HASH_CACHE) >= 10000:
            # Simple cleanup of expired items if cache is full
            expired_keys = [k for k, v in _TOKEN_HASH_CACHE.items() if now > v[0]]
            for k in expired_keys:
                del _TOKEN_HASH_CACHE[k]
            # If still full, clear entirely to prevent unbound growth
            if len(_TOKEN_HASH_CACHE) >= 10000:
                _TOKEN_HASH_CACHE.clear()

        _TOKEN_HASH_CACHE[token] = (now + 60.0, hashed_provided_token)

    # STS Access Token
    token_worker_id = await storage.verify_worker_access_token(hashed_provided_token)
    if token_worker_id:
        if worker_id_hint and token_worker_id != worker_id_hint:
            raise PermissionError(
                f"Unauthorized: Access Token belongs to '{token_worker_id}', but request is for '{worker_id_hint}'"
            )
        return token_worker_id

    # Individual/Global Token
    if not worker_id_hint:
        if config.GLOBAL_WORKER_TOKEN and token == config.GLOBAL_WORKER_TOKEN:
            return "unknown_authenticated_by_global_token"

        raise PermissionError("Unauthorized: Invalid token or missing worker_id hint")

    # Individual Token for specific worker
    expected_token_hash = await storage.get_worker_token(worker_id_hint)
    if expected_token_hash:
        if hashed_provided_token == expected_token_hash:
            return worker_id_hint
        raise PermissionError("Unauthorized: Invalid individual worker token")

    # Global Token Fallback
    if config.GLOBAL_WORKER_TOKEN and token == config.GLOBAL_WORKER_TOKEN:
        return worker_id_hint

    raise PermissionError("Unauthorized: No valid token found")


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
        request["client_config"] = client_config
        return await handler(request)

    return middleware

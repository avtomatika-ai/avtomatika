# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiohttp import web

from . import metrics
from .storage.base import StorageBackend

Handler = Callable[[web.Request], Awaitable[web.Response]]


def rate_limit_middleware_factory(
    storage: StorageBackend,
    default_limit: int,
    period: int,
    overrides: dict[str, int] | None = None,
) -> Any:
    """A factory that creates a rate-limiting middleware."""
    limit_overrides = overrides or {}

    @web.middleware
    async def rate_limit_middleware(
        request: web.Request,
        handler: Handler,
    ) -> web.Response:
        """Rate-limiting middleware that uses the provided storage backend."""
        key_identifier = request.match_info.get("worker_id", request.remote) or "unknown"

        limit = default_limit
        for path_substring, override_limit in limit_overrides.items():
            if path_substring in request.path:
                limit = override_limit
                break

        rate_limit_key = f"ratelimit:{key_identifier}:{request.path}"

        with suppress(Exception):
            count = await storage.increment_key_with_ttl(rate_limit_key, period)
            if count > limit:
                metrics.ratelimit_blocked_total.inc({"identifier": key_identifier, "path": request.path})
                return web.json_response({"error": "Too Many Requests"}, status=429)
        return await handler(request)

    return rate_limit_middleware

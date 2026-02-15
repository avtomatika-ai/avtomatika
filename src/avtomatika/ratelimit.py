from contextlib import suppress
from typing import Any, Awaitable, Callable

from aiohttp import web

from .storage.base import StorageBackend

# Define a type for the middleware handler
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
        # Determine the key for rate limiting (e.g., by worker_id or IP)
        key_identifier = request.match_info.get("worker_id", request.remote) or "unknown"

        # Determine the limit for this path
        limit = default_limit
        for path_substring, override_limit in limit_overrides.items():
            if path_substring in request.path:
                limit = override_limit
                break

        # Key by identifier and path to have per-endpoint limits
        rate_limit_key = f"ratelimit:{key_identifier}:{request.path}"

        with suppress(Exception):
            count = await storage.increment_key_with_ttl(rate_limit_key, period)
            if count > limit:
                from . import metrics

                metrics.ratelimit_blocked_total.inc({"identifier": key_identifier, "path": request.path})
                return web.json_response({"error": "Too Many Requests"}, status=429)
        return await handler(request)

    return rate_limit_middleware

from contextlib import suppress
from typing import Awaitable, Callable

from aiohttp import web

from .storage.base import StorageBackend

# Define a type for the middleware handler
Handler = Callable[[web.Request], Awaitable[web.Response]]


def rate_limit_middleware_factory(
    storage: StorageBackend,
    limit: int,
    period: int,
) -> Callable:
    """A factory that creates a rate-limiting middleware."""

    @web.middleware
    async def rate_limit_middleware(
        request: web.Request,
        handler: Handler,
    ) -> web.Response:
        """Rate-limiting middleware that uses the provided storage backend."""
        # Determine the key for rate limiting (e.g., by worker_id or IP)
        # For worker endpoints, we key by worker_id. For others, by IP.
        key_identifier = request.match_info.get("worker_id", request.remote) or "unknown"

        # Key by identifier and path to have per-endpoint limits
        rate_limit_key = f"ratelimit:{key_identifier}:{request.path}"

        with suppress(Exception):
            count = await storage.increment_key_with_ttl(rate_limit_key, period)
            if count > limit:
                return web.json_response({"error": "Too Many Requests"}, status=429)
        return await handler(request)

    return rate_limit_middleware

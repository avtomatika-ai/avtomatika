from typing import Any, Awaitable, Callable

from aiohttp import web

from .storage.base import StorageBackend

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def quota_middleware_factory(storage: StorageBackend) -> Any:
    """A factory that creates a quota-checking middleware.
    This middleware must run AFTER the client_auth_middleware.
    """

    @web.middleware
    async def quota_middleware(request: web.Request, handler: Handler) -> web.Response:
        """Checks if the client has enough quota to perform the request."""
        client_config = request.get("client_config")
        # If auth middleware did not run or failed to attach config, deny access.
        if not client_config or not client_config.get("token"):
            return web.json_response(
                {"error": "Client config not found in request"},
                status=500,
            )

        token = client_config.get("token")
        if not token:
            return web.json_response(
                {"error": "Token not found in client config"},
                status=500,
            )

        try:
            is_ok = await storage.check_and_decrement_quota(token)
            if not is_ok:
                return web.json_response(
                    {"error": "Quota exceeded or not configured"},
                    status=429,
                )
        except Exception:
            # If quota check fails, deny the request to be safe
            return web.json_response({"error": "Failed to check quota"}, status=500)

        return await handler(request)

    return quota_middleware

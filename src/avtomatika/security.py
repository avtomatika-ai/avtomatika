from typing import Any, Awaitable, Callable

from aiohttp import web

from .config import Config
from .storage.base import StorageBackend

AUTH_HEADER_AVTOMATIKA = "X-Avtomatika-Token"
AUTH_HEADER_WORKER = "X-Worker-Token"

Handler = Callable[[web.Request], Awaitable[web.Response]]


def client_auth_middleware_factory(
    storage: StorageBackend,
) -> Any:
    """Middleware factory for client authentication.
    It checks for a client token and attaches the client config to the request.
    """

    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.Response:
        token = request.headers.get(AUTH_HEADER_AVTOMATIKA)
        if not token:
            return web.json_response(
                {"error": "Missing X-Avtomatika-Token header"},
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


def worker_auth_middleware_factory(
    storage: StorageBackend,
    config: Config,
) -> Any:
    """
    Middleware factory for worker authentication.
    It supports both individual tokens and a global fallback token for backward compatibility.
    It also attaches the authenticated worker_id to the request.
    """

    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.Response:
        provided_token = request.headers.get(AUTH_HEADER_WORKER)
        if not provided_token:
            return web.json_response(
                {"error": f"Missing {AUTH_HEADER_WORKER} header"},
                status=401,
            )

        worker_id = request.match_info.get("worker_id")
        data = None

        # For specific endpoints, worker_id is in the body.
        # We need to read the body here, which can be tricky as it's a stream.
        # We clone the request to allow the handler to read the body again.
        if not worker_id and (request.path.endswith("/register") or request.path.endswith("/tasks/result")):
            try:
                cloned_request = request.clone()
                data = await cloned_request.json()
                worker_id = data.get("worker_id")
                # Attach the parsed data to the request so the handler doesn't need to re-parse
                if request.path.endswith("/register"):
                    request["worker_registration_data"] = data
            except Exception:
                return web.json_response({"error": "Invalid JSON body"}, status=400)

        # If no worker_id could be determined from path or body, we can only validate against the global token.
        if not worker_id:
            if provided_token == config.GLOBAL_WORKER_TOKEN:
                # We don't know the worker_id, so we can't attach it.
                return await handler(request)
            else:
                return web.json_response(
                    {"error": "Unauthorized: Invalid token or missing worker_id"},
                    status=401,
                )

        # --- Individual Token Check ---
        expected_token = await storage.get_worker_token(worker_id)
        if expected_token:
            if provided_token == expected_token:
                request["worker_id"] = worker_id  # Attach authenticated worker_id
                return await handler(request)
            else:
                # If an individual token exists, we do not fall back to the global token.
                return web.json_response(
                    {"error": "Unauthorized: Invalid individual worker token"},
                    status=401,
                )

        # --- Global Token Fallback ---
        if config.GLOBAL_WORKER_TOKEN and provided_token == config.GLOBAL_WORKER_TOKEN:
            request["worker_id"] = worker_id  # Attach authenticated worker_id
            return await handler(request)

        return web.json_response(
            {"error": "Unauthorized: No valid token found"},
            status=401,
        )

    return middleware

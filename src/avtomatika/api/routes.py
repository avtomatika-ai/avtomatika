# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..app_keys import ENGINE_KEY
from ..quota import quota_middleware_factory
from ..security import client_auth_middleware_factory
from .handlers import (
    cancel_job_handler,
    create_job_handler_factory,
    docs_handler,
    flush_db_handler,
    get_blueprint_graph_handler,
    get_dashboard_handler,
    get_job_file_download_handler,
    get_job_file_upload_handler,
    get_job_history_handler,
    get_job_status_handler,
    get_jobs_handler,
    get_quarantined_jobs_handler,
    get_workers_handler,
    human_approval_webhook_handler,
    metrics_handler,
    reload_worker_configs_handler,
    status_handler,
    stream_job_file_upload_handler,
)

if TYPE_CHECKING:
    from ..engine import OrchestratorEngine


def setup_routes(app: web.Application, engine: "OrchestratorEngine") -> None:
    """Sets up application routes for Public and Client APIs."""
    client_prefix = engine.config.CLIENT_API_PREFIX.strip("/")
    client_prefix_str = f"/{client_prefix}" if client_prefix else ""

    public_app = web.Application()
    public_app[ENGINE_KEY] = engine
    public_app.router.add_get("/status", status_handler)
    public_app.router.add_get("/metrics", metrics_handler)
    public_app.router.add_post("/webhooks/approval/{job_id}", human_approval_webhook_handler)
    public_app.router.add_post("/debug/flush_db", flush_db_handler)
    public_app.router.add_get("/docs", docs_handler)
    public_app.router.add_get("/jobs/quarantined", get_quarantined_jobs_handler)

    app.add_subapp("/_public/", public_app)

    if engine.config.ENABLE_CLIENT_API:
        auth_middleware = client_auth_middleware_factory(engine.storage)
        quota_middleware = quota_middleware_factory(engine.storage)
        api_middlewares = [auth_middleware, quota_middleware]

        protected_app = web.Application(middlewares=api_middlewares)
        protected_app[ENGINE_KEY] = engine
        versioned_apps: dict[str, web.Application] = {}
        has_unversioned_routes = False

        for bp in engine.blueprints.values():
            if not bp.api_endpoint:
                continue
            endpoint = bp.api_endpoint if bp.api_endpoint.startswith("/") else f"/{bp.api_endpoint}"

            handler = create_job_handler_factory(bp)

            if bp.api_version:
                if bp.api_version not in versioned_apps:
                    versioned_apps[bp.api_version] = web.Application(middlewares=api_middlewares)
                    versioned_apps[bp.api_version][ENGINE_KEY] = engine
                versioned_apps[bp.api_version].router.add_post(endpoint, handler)
            else:
                protected_app.router.add_post(endpoint, handler)
                has_unversioned_routes = True

        all_protected_apps = list(versioned_apps.values())
        if has_unversioned_routes:
            all_protected_apps.append(protected_app)

        for sub_app in all_protected_apps:
            _register_common_routes(sub_app, engine)

        if has_unversioned_routes:
            if client_prefix_str:
                app.add_subapp(f"{client_prefix_str}/", protected_app)
            else:
                # If client API is at root, we must apply middlewares to each route individually
                # to avoid affecting /_public/ or /_worker/
                def wrap(h: Any) -> Any:
                    async def wrapped_handler(request: web.Request) -> web.Response:
                        # Manually chain middlewares for this specific handler
                        current_h = h
                        for mw in reversed(api_middlewares):
                            # Create a closure for the next step in the chain
                            next_h = current_h

                            async def next_step(req: web.Request, nh: Any = next_h, m: Any = mw) -> web.Response:
                                return await m(req, nh)

                            current_h = next_step

                        return await current_h(request)

                    return wrapped_handler

                _register_common_routes(app, engine, wrap_func=wrap)

                # Register blueprint routes with middleware
                for bp in engine.blueprints.values():
                    if not bp.api_endpoint or bp.api_version:
                        continue
                    endpoint = bp.api_endpoint if bp.api_endpoint.startswith("/") else f"/{bp.api_endpoint}"
                    app.router.add_post(endpoint, wrap(create_job_handler_factory(bp)))

        for version, sub_app in versioned_apps.items():
            version_prefix = f"/{version}" if version else ""
            app.add_subapp(f"{client_prefix_str}{version_prefix}", sub_app)


def _register_common_routes(
    app: web.Application,
    engine: "OrchestratorEngine",
    wrap_func: Any = None,
) -> None:
    def add(method: str, path: str, handler: Any) -> None:
        if wrap_func:
            handler = wrap_func(handler)
        app.router.add_route(method, path, handler)

    add("GET", "/jobs/{job_id}", get_job_status_handler)
    add("POST", "/jobs/{job_id}/cancel", cancel_job_handler)
    add("GET", "/jobs/{job_id}/files/upload", get_job_file_upload_handler)
    add("PUT", "/jobs/{job_id}/files/content/{filename}", stream_job_file_upload_handler)
    add("GET", "/jobs/{job_id}/files/download/{filename}", get_job_file_download_handler)

    # Always register history route; the handler itself will handle NoOp state
    add("GET", "/jobs/{job_id}/history", get_job_history_handler)

    add("GET", "/blueprints/{blueprint_name}/graph", get_blueprint_graph_handler)
    add("GET", "/workers", get_workers_handler)
    add("GET", "/workers/catalog", get_dashboard_handler)  # Placeholder
    add("GET", "/jobs", get_jobs_handler)
    add("GET", "/dashboard", get_dashboard_handler)
    add("POST", "/admin/reload-workers", reload_worker_configs_handler)

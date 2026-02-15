from typing import TYPE_CHECKING

from aiohttp import web

from ..app_keys import ENGINE_KEY
from ..history.noop import NoOpHistoryStorage
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

    # --- Public API (Unprotected) ---
    public_app = web.Application()
    public_app[ENGINE_KEY] = engine
    public_app.router.add_get("/status", status_handler)
    public_app.router.add_get("/metrics", metrics_handler)
    public_app.router.add_post("/webhooks/approval/{job_id}", human_approval_webhook_handler)
    public_app.router.add_post("/debug/flush_db", flush_db_handler)
    public_app.router.add_get("/docs", docs_handler)
    public_app.router.add_get("/jobs/quarantined", get_quarantined_jobs_handler)
    app.add_subapp("/_public/", public_app)

    # --- Protected API (Client Access) ---
    if engine.config.ENABLE_CLIENT_API:
        auth_middleware = client_auth_middleware_factory(engine.storage)
        quota_middleware = quota_middleware_factory(engine.storage)
        api_middlewares = [auth_middleware, quota_middleware]

        protected_app = web.Application(middlewares=api_middlewares)
        protected_app[ENGINE_KEY] = engine
        versioned_apps: dict[str, web.Application] = {}
        has_unversioned_routes = False

        # Register Blueprint routes
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

        # Common routes for all protected apps
        all_protected_apps = list(versioned_apps.values())
        if has_unversioned_routes:
            all_protected_apps.append(protected_app)

        for sub_app in all_protected_apps:
            _register_common_routes(sub_app, engine)

        # Mount protected apps
        if has_unversioned_routes:
            app.add_subapp("/api/", protected_app)
        for version, sub_app in versioned_apps.items():
            app.add_subapp(f"/api/{version}", sub_app)


def _register_common_routes(app: web.Application, engine: "OrchestratorEngine") -> None:
    app.router.add_get("/jobs/{job_id}", get_job_status_handler)
    app.router.add_post("/jobs/{job_id}/cancel", cancel_job_handler)
    app.router.add_get("/jobs/{job_id}/files/upload", get_job_file_upload_handler)
    app.router.add_put("/jobs/{job_id}/files/content/{filename}", stream_job_file_upload_handler)
    app.router.add_get("/jobs/{job_id}/files/download/{filename}", get_job_file_download_handler)
    if not isinstance(engine.history_storage, NoOpHistoryStorage):
        app.router.add_get("/jobs/{job_id}/history", get_job_history_handler)
    app.router.add_get("/blueprints/{blueprint_name}/graph", get_blueprint_graph_handler)
    app.router.add_get("/workers", get_workers_handler)
    app.router.add_get("/jobs", get_jobs_handler)
    app.router.add_get("/dashboard", get_dashboard_handler)
    app.router.add_post("/admin/reload-workers", reload_worker_configs_handler)

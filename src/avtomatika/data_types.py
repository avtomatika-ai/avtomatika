from typing import TYPE_CHECKING, Any, NamedTuple

from rxon.models import InstalledModel, Resources

if TYPE_CHECKING:
    from .context import ActionFactory


class ClientConfig(NamedTuple):
    """Static client configuration, obtained from `clients.toml`."""

    token: str
    plan: str
    params: dict[str, Any]


class JobContext(NamedTuple):
    """Job execution context, passed to each handler."""

    job_id: str
    current_state: str
    initial_data: dict[str, Any]
    state_history: dict[str, Any]
    client: ClientConfig
    actions: "ActionFactory"
    data_stores: Any | None = None
    tracing_context: dict[str, Any] | None = None
    aggregation_results: dict[str, Any] | None = None
    webhook_url: str | None = None
    task_files: Any | None = None


class WorkerInfo(NamedTuple):
    """Complete information about the worker, transmitted upon registration."""

    worker_id: str
    address: str
    dynamic_token: str
    worker_type: str
    supported_tasks: list[str]
    resources: Resources
    installed_software: dict[str, str]
    installed_models: list[InstalledModel]

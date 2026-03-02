# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from typing import TYPE_CHECKING, Any, NamedTuple

from rxon.models import InstalledArtifact, Resources

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
    worker_type: str
    supported_skills: list[Any]  # list[SkillInfo]
    resources: Resources
    installed_software: dict[str, str]
    installed_artifacts: list[InstalledArtifact]
    capabilities: Any  # WorkerCapabilities
    skills_hash: str | None = None

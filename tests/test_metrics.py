# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from aioprometheus.collectors import REGISTRY
from src.avtomatika.metrics import init_metrics


def test_init_metrics():
    """Tests that metrics are initialized and registered correctly."""
    # Clear the registry to ensure a clean state
    REGISTRY.collectors.clear()

    init_metrics()

    assert "orchestrator_jobs_total" in REGISTRY.collectors
    assert "orchestrator_jobs_failed_total" in REGISTRY.collectors
    assert "orchestrator_job_duration_seconds" in REGISTRY.collectors
    assert "orchestrator_task_queue_length" in REGISTRY.collectors
    assert "orchestrator_active_workers" in REGISTRY.collectors


def test_init_metrics_idempotent():
    """Tests that calling init_metrics multiple times does not raise an error."""
    # Clear the registry to ensure a clean state
    REGISTRY.collectors.clear()

    init_metrics()
    init_metrics()

    assert "orchestrator_jobs_total" in REGISTRY.collectors


def test_loop_lag_metric_exists():
    """Verifies that the loop lag metric is registered."""
    init_metrics()
    assert "orchestrator_loop_lag_seconds" in REGISTRY.collectors

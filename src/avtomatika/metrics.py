# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from collections.abc import Callable, Iterable
from typing import Any

from .telemetry import metrics

LABEL_BLUEPRINT = "blueprint"


class Metrics:
    """Encapsulates OpenTelemetry metrics for the orchestrator.
    Avoiding global variables for better testability and clean architecture.
    """

    def __init__(self, meter: Any, instrument_cache: Any | None = None):
        # without using global module-level variables.
        self._cache = instrument_cache if instrument_cache is not None else {}

        def get_or_create(name: str, creator: Any, **kwargs: Any) -> Any:
            if name not in self._cache:
                self._cache[name] = creator(name, **kwargs)
            return self._cache[name]

        self.jobs_total = get_or_create(
            "orchestrator_jobs_total",
            meter.create_counter,
            unit="1",
            description="Total number of jobs created.",
        )
        self.jobs_failed_total = get_or_create(
            "orchestrator_jobs_failed_total",
            meter.create_counter,
            unit="1",
            description="Total number of jobs that have failed.",
        )
        self.job_duration_seconds = get_or_create(
            "orchestrator_job_duration_seconds",
            meter.create_histogram,
            unit="s",
            description="Time taken for a job to complete.",
        )

        self.security_auth_failures_total = get_or_create(
            "orchestrator_security_auth_failures_total",
            meter.create_counter,
            unit="1",
            description="Total number of failed worker authentication attempts.",
        )
        self.security_replay_detected_total = get_or_create(
            "orchestrator_security_replay_detected_total",
            meter.create_counter,
            unit="1",
            description="Total number of detected replay attacks.",
        )
        self.security_identity_mismatch_total = get_or_create(
            "orchestrator_security_identity_mismatch_total",
            meter.create_counter,
            unit="1",
            description="Total number of certificate identity mismatches.",
        )

        # Observable Gauges for state tracking
        self._gauge_values: dict[str, float] = {
            "task_queue_length": 0.0,
            "active_workers": 0.0,
            "loop_lag_seconds": 0.0,
        }

        self.task_queue_length = get_or_create(
            "orchestrator_task_queue_length",
            meter.create_observable_gauge,
            callbacks=[self._get_gauge_callback("task_queue_length")],
            description="Number of tasks waiting in the job queue.",
        )
        self.active_workers = get_or_create(
            "orchestrator_active_workers",
            meter.create_observable_gauge,
            callbacks=[self._get_gauge_callback("active_workers")],
            description="Number of active workers reporting to the orchestrator.",
        )
        self.loop_lag_seconds = get_or_create(
            "orchestrator_loop_lag_seconds",
            meter.create_observable_gauge,
            callbacks=[self._get_gauge_callback("loop_lag_seconds")],
            description="Delay in the asyncio event loop.",
        )

        self.ratelimit_blocked_total = get_or_create(
            "orchestrator_ratelimit_blocked_total",
            meter.create_counter,
            unit="1",
            description="Total requests blocked by rate limiter",
        )
        self.jobs_timeouts_total = get_or_create(
            "orchestrator_jobs_timeouts_total",
            meter.create_counter,
            unit="1",
            description="Total number of job timeouts",
        )
        self.tasks_ignored_total = get_or_create(
            "orchestrator_tasks_ignored_total",
            meter.create_counter,
            unit="1",
            description="Total number of task results ignored",
        )
        self.tasks_hot_dispatched_total = get_or_create(
            "orchestrator_tasks_hot_dispatched_total",
            meter.create_counter,
            unit="1",
            description="Total number of tasks dispatched to HOT workers",
        )

        self.s3_operations_total = get_or_create(
            "orchestrator_s3_operations_total",
            meter.create_counter,
            unit="1",
            description="Total number of S3 operations.",
        )
        self.s3_operation_duration_seconds = get_or_create(
            "orchestrator_s3_operation_duration_seconds",
            meter.create_histogram,
            unit="s",
            description="Duration of S3 operations.",
        )

        self.scheduler_jobs_triggered_total = get_or_create(
            "orchestrator_scheduler_jobs_triggered_total",
            meter.create_counter,
            unit="1",
            description="Total number of jobs triggered by the scheduler.",
        )

    def _get_gauge_callback(self, name: str) -> Callable[[Any], Iterable[metrics.Observation]]:
        def callback(options: Any) -> Iterable[metrics.Observation]:
            yield metrics.Observation(self._gauge_values.get(name, 0.0))

        return callback

    def set_gauge(self, name: str, value: float) -> None:
        """Helper to update gauge values stored for OTel callbacks."""
        if name in self._gauge_values:
            self._gauge_values[name] = value


def create_metrics(instrument_cache: Any | None = None) -> Metrics:
    """Factory to create a Metrics instance using the global OTel meter.
    Takes an optional cache to ensure idempotency across multiple instances.
    """
    meter = metrics.get_meter("avtomatika")
    return Metrics(meter, instrument_cache=instrument_cache)

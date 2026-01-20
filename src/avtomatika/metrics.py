from aioprometheus import Counter, Gauge, Summary
from aioprometheus.collectors import REGISTRY

# Constants for labels
LABEL_BLUEPRINT = "blueprint"

# Global variables for metrics
jobs_total: Counter
jobs_failed_total: Counter
job_duration_seconds: Summary
task_queue_length: Gauge
active_workers: Gauge


def init_metrics() -> None:
    """
    Initializes Prometheus metrics.
    Uses a registry check for idempotency, which is important for tests.
    """
    global jobs_total, jobs_failed_total, job_duration_seconds, task_queue_length, active_workers

    if "orchestrator_jobs_total" in REGISTRY.collectors:
        # Get existing metrics if they are already registered
        jobs_total = REGISTRY.collectors["orchestrator_jobs_total"]
        jobs_failed_total = REGISTRY.collectors["orchestrator_jobs_failed_total"]
        job_duration_seconds = REGISTRY.collectors["orchestrator_job_duration_seconds"]
        task_queue_length = REGISTRY.collectors["orchestrator_task_queue_length"]
        active_workers = REGISTRY.collectors["orchestrator_active_workers"]
        return

    jobs_total = Counter(
        "orchestrator_jobs_total",
        "Total number of jobs created.",
        const_labels={LABEL_BLUEPRINT: ""},
    )
    jobs_failed_total = Counter(
        "orchestrator_jobs_failed_total",
        "Total number of jobs that have failed.",
        const_labels={LABEL_BLUEPRINT: ""},
    )
    job_duration_seconds = Summary(
        "orchestrator_job_duration_seconds",
        "Time taken for a job to complete.",
        const_labels={LABEL_BLUEPRINT: ""},
    )
    task_queue_length = Gauge(
        "orchestrator_task_queue_length",
        "Number of tasks waiting in the job queue.",
    )
    active_workers = Gauge(
        "orchestrator_active_workers",
        "Number of active workers reporting to the orchestrator.",
    )

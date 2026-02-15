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
ratelimit_blocked_total: Counter
jobs_timeouts_total: Counter
tasks_ignored_total: Counter
tasks_hot_dispatched_total: Counter


def init_metrics() -> None:
    """
    Initializes Prometheus metrics.
    Uses a registry check for idempotency, which is important for tests.
    """
    global jobs_total, jobs_failed_total, job_duration_seconds, task_queue_length
    global active_workers, ratelimit_blocked_total, jobs_timeouts_total
    global tasks_ignored_total, tasks_hot_dispatched_total

    if "orchestrator_jobs_total" in REGISTRY.collectors:
        # Get existing metrics if they are already registered
        jobs_total = REGISTRY.collectors["orchestrator_jobs_total"]
        jobs_failed_total = REGISTRY.collectors["orchestrator_jobs_failed_total"]
        job_duration_seconds = REGISTRY.collectors["orchestrator_job_duration_seconds"]
        task_queue_length = REGISTRY.collectors["orchestrator_task_queue_length"]
        active_workers = REGISTRY.collectors["orchestrator_active_workers"]
        ratelimit_blocked_total = REGISTRY.collectors["orchestrator_ratelimit_blocked_total"]
        jobs_timeouts_total = REGISTRY.collectors.get("orchestrator_jobs_timeouts_total")
        tasks_ignored_total = REGISTRY.collectors.get("orchestrator_tasks_ignored_total")
        tasks_hot_dispatched_total = REGISTRY.collectors.get("orchestrator_tasks_hot_dispatched_total")
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
    ratelimit_blocked_total = Counter(
        "orchestrator_ratelimit_blocked_total",
        "Total requests blocked by rate limiter",
        const_labels={"identifier": "", "path": ""},
    )
    jobs_timeouts_total = Counter(
        "orchestrator_jobs_timeouts_total",
        "Total number of job timeouts",
        const_labels={LABEL_BLUEPRINT: "", "type": ""},
    )
    tasks_ignored_total = Counter(
        "orchestrator_tasks_ignored_total",
        "Total number of task results ignored",
        const_labels={LABEL_BLUEPRINT: "", "reason": ""},
    )
    tasks_hot_dispatched_total = Counter(
        "orchestrator_tasks_hot_dispatched_total",
        "Total number of tasks dispatched to HOT workers",
        const_labels={LABEL_BLUEPRINT: "", "kind": ""},  # kind: "hot_skill" or "hot_cache"
    )

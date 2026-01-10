from os import getenv
from socket import gethostname


class Config:
    """A class for managing the Orchestrator's configuration.
    Loads parameters from environment variables.
    """

    def __init__(self):
        # Instance identity
        self.INSTANCE_ID: str = getenv("INSTANCE_ID", gethostname())

        # Redis settings
        self.REDIS_HOST: str = getenv("REDIS_HOST", "")
        self.REDIS_PORT: int = int(getenv("REDIS_PORT", 6379))
        self.REDIS_DB: int = int(getenv("REDIS_DB", 0))

        # Postgres settings
        self.POSTGRES_DSN: str = getenv(
            "POSTGRES_DSN",
            "postgresql://user:password@localhost/db",
        )

        # API server settings
        self.API_HOST: str = getenv("API_HOST", "0.0.0.0")
        self.API_PORT: int = int(getenv("API_PORT", 8080))

        # Security settings
        self.CLIENT_TOKEN: str = getenv(
            "CLIENT_TOKEN",
            "secure-orchestrator-token",
        )
        self.GLOBAL_WORKER_TOKEN: str = getenv("GLOBAL_WORKER_TOKEN", "secure-worker-token")

        # Logging settings
        self.LOG_LEVEL: str = getenv("LOG_LEVEL", "INFO").upper()
        self.LOG_FORMAT: str = getenv("LOG_FORMAT", "json")  # "text" or "json"

        # Worker settings
        self.WORKER_TIMEOUT_SECONDS: int = int(getenv("WORKER_TIMEOUT_SECONDS", 300))
        self.WORKER_POLL_TIMEOUT_SECONDS: int = int(
            getenv("WORKER_POLL_TIMEOUT_SECONDS", 30),
        )
        self.WORKER_HEALTH_CHECK_INTERVAL_SECONDS: int = int(
            getenv("WORKER_HEALTH_CHECK_INTERVAL_SECONDS", 60),
        )
        self.JOB_MAX_RETRIES: int = int(getenv("JOB_MAX_RETRIES", 3))
        self.WATCHER_INTERVAL_SECONDS: int = int(
            getenv("WATCHER_INTERVAL_SECONDS", 20),
        )
        self.EXECUTOR_MAX_CONCURRENT_JOBS: int = int(
            getenv("EXECUTOR_MAX_CONCURRENT_JOBS", 100),
        )

        # History storage settings
        self.HISTORY_DATABASE_URI: str = getenv("HISTORY_DATABASE_URI", "")

        # Rate limiting settings
        self.RATE_LIMITING_ENABLED: bool = getenv("RATE_LIMITING_ENABLED", "true").lower() == "true"

        # External config files
        self.WORKERS_CONFIG_PATH: str = getenv("WORKERS_CONFIG_PATH", "")
        self.CLIENTS_CONFIG_PATH: str = getenv("CLIENTS_CONFIG_PATH", "")

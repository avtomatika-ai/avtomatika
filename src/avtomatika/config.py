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
        self.ENABLE_CLIENT_API: bool = getenv("ENABLE_CLIENT_API", "true").lower() == "true"

        # Security settings
        self.CLIENT_TOKEN: str = getenv(
            "CLIENT_TOKEN",
            "secure-orchestrator-token",
        )
        self.GLOBAL_WORKER_TOKEN: str = getenv("GLOBAL_WORKER_TOKEN", "secure-worker-token")

        # TLS / mTLS settings
        self.TLS_ENABLED: bool = getenv("TLS_ENABLED", "false").lower() == "true"
        self.TLS_CERT_PATH: str = getenv("TLS_CERT_PATH", "")
        self.TLS_KEY_PATH: str = getenv("TLS_KEY_PATH", "")
        self.TLS_CA_PATH: str = getenv("TLS_CA_PATH", "")
        self.TLS_REQUIRE_CLIENT_CERT: bool = getenv("TLS_REQUIRE_CLIENT_CERT", "false").lower() == "true"

        # Logging settings
        self.LOG_LEVEL: str = getenv("LOG_LEVEL", "INFO").upper()
        self.LOG_FORMAT: str = getenv("LOG_FORMAT", "json")  # "text" or "json"

        # Worker settings
        self.WORKER_TIMEOUT_SECONDS: int = int(getenv("WORKER_TIMEOUT_SECONDS", 300))
        self.TASK_FILES_DIR: str = getenv("TASK_FILES_DIR", "/tmp/avtomatika-payloads")
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
        self.REDIS_STREAM_BLOCK_MS: int = int(getenv("REDIS_STREAM_BLOCK_MS", 5000))

        # History storage settings
        self.HISTORY_DATABASE_URI: str = getenv("HISTORY_DATABASE_URI", "")

        # S3 settings
        self.S3_ENDPOINT_URL: str = getenv("S3_ENDPOINT_URL", "")
        self.S3_ACCESS_KEY: str = getenv("S3_ACCESS_KEY", "")
        self.S3_SECRET_KEY: str = getenv("S3_SECRET_KEY", "")
        self.S3_REGION: str = getenv("S3_REGION", "us-east-1")
        self.S3_DEFAULT_BUCKET: str = getenv("S3_DEFAULT_BUCKET", "avtomatika-payloads")
        self.S3_MAX_CONCURRENCY: int = int(getenv("S3_MAX_CONCURRENCY", 100))

        # Rate limiting settings
        self.RATE_LIMITING_ENABLED: bool = getenv("RATE_LIMITING_ENABLED", "true").lower() == "true"

        # External config files
        self.WORKERS_CONFIG_PATH: str = getenv("WORKERS_CONFIG_PATH", "")
        self.CLIENTS_CONFIG_PATH: str = getenv("CLIENTS_CONFIG_PATH", "")
        self.SCHEDULES_CONFIG_PATH: str = getenv("SCHEDULES_CONFIG_PATH", "")

        # Timezone settings
        self.TZ: str = getenv("TZ", "UTC")

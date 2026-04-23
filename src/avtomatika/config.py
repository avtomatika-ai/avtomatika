# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from os import getenv
from socket import gethostname


class Config:
    """A class for managing the Orchestrator's configuration.
    Loads parameters from environment variables.
    """

    def __init__(self) -> None:
        self.INSTANCE_ID: str = getenv("INSTANCE_ID", gethostname())

        self.REDIS_HOST: str = getenv("REDIS_HOST", "")
        self.REDIS_PORT: int = int(getenv("REDIS_PORT", 6379))
        self.REDIS_DB: int = int(getenv("REDIS_DB", 0))

        self.POSTGRES_DSN: str = getenv(
            "POSTGRES_DSN",
            "postgresql://user:password@localhost/db",
        )

        self.API_HOST: str = getenv("API_HOST", "0.0.0.0")
        self.API_PORT: int = int(getenv("API_PORT", 8080))
        self.CLIENT_API_PREFIX: str = getenv("CLIENT_API_PREFIX", "api").strip("/")
        self.ENABLE_CLIENT_API: bool = getenv("ENABLE_CLIENT_API", "true").lower() == "true"

        self.CLIENT_TOKEN: str = getenv(
            "CLIENT_TOKEN",
            "secure-orchestrator-token",
        )
        self.GLOBAL_WORKER_TOKEN: str = getenv("GLOBAL_WORKER_TOKEN", "secure-worker-token")

        # Worker authentication mode: 'mixed' (tokens + mTLS), 'mtls-only' (strict mTLS), 'token-only' (strict tokens)
        self.WORKER_AUTH_MODE: str = getenv("WORKER_AUTH_MODE", "mixed").lower()
        if self.WORKER_AUTH_MODE not in ("mixed", "mtls-only", "token-only"):
            raise ValueError(
                f"Invalid WORKER_AUTH_MODE: {self.WORKER_AUTH_MODE}. Must be 'mixed', 'mtls-only', or 'token-only'"
            )

        self.TLS_ENABLED: bool = getenv("TLS_ENABLED", "false").lower() == "true"
        self.TLS_CERT_PATH: str = getenv("TLS_CERT_PATH", "")
        self.TLS_KEY_PATH: str = getenv("TLS_KEY_PATH", "")
        self.TLS_CA_PATH: str = getenv("TLS_CA_PATH", "")
        self.TLS_REQUIRE_CLIENT_CERT: bool = getenv("TLS_REQUIRE_CLIENT_CERT", "false").lower() == "true"

        self.LOG_LEVEL: str = getenv("LOG_LEVEL", "INFO").upper()
        self.LOG_FORMAT: str = getenv("LOG_FORMAT", "json")  # "text" or "json"

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
        self.WATCHER_LIMIT: int = int(getenv("WATCHER_LIMIT", 500))
        self.STRICT_EVENT_VALIDATION: bool = getenv("STRICT_EVENT_VALIDATION", "true").lower() == "true"
        self.EXECUTOR_MAX_CONCURRENT_JOBS: int = int(
            getenv("EXECUTOR_MAX_CONCURRENT_JOBS", 1000),
        )

        self.REPUTATION_PENALTY_CONTRACT_VIOLATION: float = float(
            getenv("REPUTATION_PENALTY_CONTRACT_VIOLATION", "0.2")
        )
        self.REPUTATION_PENALTY_TASK_FAILURE: float = float(getenv("REPUTATION_PENALTY_TASK_FAILURE", "0.05"))
        self.REPUTATION_REWARD_SUCCESS: float = float(getenv("REPUTATION_REWARD_SUCCESS", "0.001"))
        self.REPUTATION_MIN_THRESHOLD: float = float(getenv("REPUTATION_MIN_THRESHOLD", "0.3"))

        self.REDIS_STREAM_BLOCK_MS: int = int(getenv("REDIS_STREAM_BLOCK_MS", 5000))

        # Security: Envelope Encryption for Master Tokens in Redis
        # If set, static worker tokens are encrypted in Redis.
        self.REDIS_ENCRYPTION_KEY: str | None = getenv("REDIS_ENCRYPTION_KEY")

        self.HISTORY_DATABASE_URI: str = getenv("HISTORY_DATABASE_URI", "")

        self.S3_ENDPOINT_URL: str = getenv("S3_ENDPOINT_URL", "")
        self.S3_ACCESS_KEY: str = getenv("S3_ACCESS_KEY", "")
        self.S3_SECRET_KEY: str = getenv("S3_SECRET_KEY", "")
        self.S3_REGION: str = getenv("S3_REGION", "us-east-1")
        self.S3_DEFAULT_BUCKET: str = getenv("S3_DEFAULT_BUCKET", "avtomatika-payloads")
        self.S3_MAX_CONCURRENCY: int = int(getenv("S3_MAX_CONCURRENCY", 100))
        self.S3_AUTO_CLEANUP: bool = getenv("S3_AUTO_CLEANUP", "true").lower() == "true"

        self.RATE_LIMITING_ENABLED: bool = getenv("RATE_LIMITING_ENABLED", "true").lower() == "true"
        self.RATE_LIMIT_LIMIT: int = int(getenv("RATE_LIMIT_LIMIT", 100))
        self.RATE_LIMIT_PERIOD: int = int(getenv("RATE_LIMIT_PERIOD", 60))
        self.RATE_LIMIT_HEARTBEAT_LIMIT: int = int(getenv("RATE_LIMIT_HEARTBEAT_LIMIT", 120))
        self.RATE_LIMIT_POLL_LIMIT: int = int(getenv("RATE_LIMIT_POLL_LIMIT", 60))

        self.WORKERS_CONFIG_PATH: str = getenv("WORKERS_CONFIG_PATH", "")
        self.CLIENTS_CONFIG_PATH: str = getenv("CLIENTS_CONFIG_PATH", "")
        self.SCHEDULES_CONFIG_PATH: str = getenv("SCHEDULES_CONFIG_PATH", "")
        self.BLUEPRINTS_DIR: str = getenv("BLUEPRINTS_DIR", "")

        self.TZ: str = getenv("TZ", "UTC")

        # Advanced Dispatching & Load Balancing
        self.DISPATCHER_SOFT_LIMIT: int = int(getenv("DISPATCHER_SOFT_LIMIT", 3))
        self.DISPATCHER_MAX_CANDIDATES: int = int(getenv("DISPATCHER_MAX_CANDIDATES", 50))

        self.WORK_STEALING_ENABLED: bool = getenv("WORK_STEALING_ENABLED", "true").lower() == "true"

        self.MAX_TRANSITIONS_PER_JOB: int = int(getenv("MAX_TRANSITIONS_PER_JOB", 100))

    @property
    def encrypt_worker_tokens(self) -> bool:
        """Returns True if envelope encryption for worker tokens is enabled."""
        return bool(self.REDIS_ENCRYPTION_KEY) and self.WORKER_AUTH_MODE != "mtls-only"

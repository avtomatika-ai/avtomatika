from hashlib import sha256
from logging import getLogger
from os.path import exists
from tomllib import load
from typing import Any

from .storage.base import StorageBackend

logger = getLogger(__name__)


async def load_worker_configs_to_redis(storage: StorageBackend, config_path: str):
    """
    Loads worker configurations from a TOML file into Redis.
    This allows for dynamic and secure management of worker tokens without
    restarting the orchestrator.
    """
    if not exists(config_path):
        logger.warning(
            f"Worker config file not found at '{config_path}'. "
            "Individual worker authentication will be disabled. "
            "The system will fall back to the global WORKER_TOKEN if set."
        )
        return

    try:
        with open(config_path, "rb") as f:
            workers_config: dict[str, Any] = load(f)
    except Exception as e:
        logger.error(f"Failed to load or parse worker config file '{config_path}': {e}")
        raise ValueError(f"Invalid worker configuration file: {e}") from e

    for worker_id, config in workers_config.items():
        if not isinstance(config, dict):
            logger.error(f"Worker '{worker_id}' configuration must be a table.")
            raise ValueError(f"Invalid configuration for worker '{worker_id}'")

        token = config.get("token")
        if not token:
            logger.warning(f"No token found for worker_id '{worker_id}' in {config_path}. Skipping.")
            # Skipping might be safer here if we want to allow partial configs, but strict is better.
            # Let's keep existing skip logic but log error? No, let's allow skip if user really wants.
            continue
        try:
            # Hash the token before storing it
            hashed_token = sha256(token.encode()).hexdigest()
            # Store the token in a way that's easily retrievable by worker_id
            await storage.set_worker_token(worker_id, hashed_token)
            logger.info(f"Loaded token for worker_id '{worker_id}'.")
        except Exception as e:
            logger.error(f"Failed to store token for worker_id '{worker_id}' in Redis: {e}")

    logger.info(f"Successfully loaded {len(workers_config)} worker configurations.")

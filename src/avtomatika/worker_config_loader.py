# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from logging import getLogger
from os.path import exists
from tomllib import load
from typing import Any

from .storage.base import StorageBackend
from .utils.crypto import encrypt_token

logger = getLogger(__name__)


async def load_worker_configs_to_redis(
    storage: StorageBackend, config_path: str, auth_mode: str = "mixed", redis_encryption_key: str | None = None
) -> None:
    """
    Loads worker configurations from a TOML file into Redis.
    This allows for dynamic and secure management of worker tokens without
    restarting the orchestrator.

    If auth_mode is 'mtls-only', raw tokens are NOT loaded into Redis for security.
    If redis_encryption_key is provided, tokens are stored encrypted.
    """
    if auth_mode == "mtls-only":
        logger.info(
            "WORKER_AUTH_MODE is 'mtls-only'. Skipping loading of static tokens "
            f"from '{config_path}' to Redis for enhanced security."
        )
        return

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
            continue
        try:
            stored_token = token
            if redis_encryption_key:
                stored_token = encrypt_token(token, redis_encryption_key)

            await storage.set_worker_token(worker_id, stored_token)
            logger.info(f"Loaded token for worker_id '{worker_id}'{' (encrypted)' if redis_encryption_key else ''}.")
        except Exception as e:
            logger.error(f"Failed to store token for worker_id '{worker_id}' in Redis: {e}")

    logger.info(f"Successfully loaded {len(workers_config)} worker configurations.")

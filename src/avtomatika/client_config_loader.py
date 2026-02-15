from logging import getLogger
from tomllib import load

from .storage.base import StorageBackend

logger = getLogger(__name__)


async def load_client_configs_to_redis(
    storage: StorageBackend,
    config_path: str = "clients.toml",
) -> None:
    """Reads client configurations from a TOML file and loads them into Redis.

    This function should be called on application startup. It populates Redis
    with both static client parameters (plan, languages, etc.) and initializes
    dynamic quota counters.
    """
    logger.info("Loading client configurations from '%s' into Redis...", config_path)
    try:
        with open(config_path, "rb") as f:
            clients_data = load(f)
    except FileNotFoundError:
        logger.warning(
            "Client config file not found at '%s'. No client configs loaded.",
            config_path,
        )
        return
    except Exception as e:
        logger.error(f"Failed to parse client config file '{config_path}': {e}")
        raise ValueError(f"Invalid client configuration file: {e}") from e

    loaded_count = 0
    for client_name, config in clients_data.items():
        if not isinstance(config, dict):
            logger.error(f"Client '{client_name}' configuration must be a table (dict).")
            raise ValueError(f"Invalid configuration for client '{client_name}'")

        token = config.get("token")
        if not token:
            logger.error(f"Client '{client_name}' is missing required 'token' field.")
            raise ValueError(f"Missing token for client '{client_name}'")

        if not isinstance(token, str):
            logger.error(f"Token for client '{client_name}' must be a string.")
            raise ValueError(f"Invalid token type for client '{client_name}'")

        # Separate static config from dynamic quota values
        static_config = {k: v for k, v in config.items() if k != "monthly_attempts"}
        quota = config.get("monthly_attempts")

        if quota is not None and not isinstance(quota, int):
            logger.error(f"Quota 'monthly_attempts' for client '{client_name}' must be an integer.")
            raise ValueError(f"Invalid quota type for client '{client_name}'")

        try:
            # Assume these storage methods will be implemented
            await storage.save_client_config(token, static_config)
            if quota is not None:
                await storage.initialize_client_quota(token, quota)

            loaded_count += 1
        except Exception as e:
            logger.error(
                "Failed to load config for client '%s' (token: %s...): %s",
                client_name,
                token[:4],
                e,
            )

    logger.info("Successfully loaded %d client configurations.", loaded_count)

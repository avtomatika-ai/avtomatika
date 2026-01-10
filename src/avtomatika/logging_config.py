from logging import DEBUG, Formatter, StreamHandler, getLogger
from sys import stdout

from pythonjsonlogger import json


def setup_logging(log_level: str = "INFO", log_format: str = "json"):
    """Configures structured logging for the entire application."""
    logger = getLogger("avtomatika")
    logger.setLevel(log_level)

    handler = StreamHandler(stdout)
    formatter: Formatter
    if log_format.lower() == "json":
        # Formatter for JSON logs
        formatter = json.JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d",
        )
    else:
        # Standard text formatter
        formatter = Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    handler.setFormatter(formatter)

    # Avoid duplicating handlers
    if not logger.handlers:
        logger.addHandler(handler)

    # Configure the root logger to see logs from libraries (aiohttp, etc.)
    root_logger = getLogger()
    # Set the root logger level so as not to filter messages
    # for child loggers prematurely.
    root_logger.setLevel(DEBUG)
    if not root_logger.handlers:
        root_handler = StreamHandler(stdout)
        root_handler.setFormatter(
            Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
        )
        root_logger.addHandler(root_handler)

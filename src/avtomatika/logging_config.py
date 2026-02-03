from datetime import datetime
from logging import DEBUG, Formatter, StreamHandler, getLogger
from sys import stdout
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from pythonjsonlogger import json


class TimezoneFormatter(Formatter):
    """Formatter that respects a custom timezone."""

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: Literal["%", "{", "$"] = "%",
        validate: bool = True,
        *,
        tz_name: str = "UTC",
    ) -> None:
        super().__init__(fmt, datefmt, style, validate)
        self.tz = ZoneInfo(tz_name)

    def converter(self, timestamp: float) -> datetime:  # type: ignore[override]
        return datetime.fromtimestamp(timestamp, self.tz)

    def formatTime(self, record: Any, datefmt: Optional[str] = None) -> str:
        dt = self.converter(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            try:
                s = dt.isoformat(timespec="milliseconds")
            except TypeError:
                s = dt.isoformat()
        return s


class TimezoneJsonFormatter(json.JsonFormatter):  # type: ignore[name-defined]
    """JSON Formatter that respects a custom timezone."""

    def __init__(self, *args: Any, tz_name: str = "UTC", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tz = ZoneInfo(tz_name)

    def formatTime(self, record: Any, datefmt: Optional[str] = None) -> str:
        # Override formatTime to use timezone-aware datetime
        dt = datetime.fromtimestamp(record.created, self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        # Return ISO format with offset
        return dt.isoformat()


def setup_logging(log_level: str = "INFO", log_format: str = "json", tz_name: str = "UTC") -> None:
    """Configures structured logging for the entire application."""
    logger = getLogger("avtomatika")
    logger.setLevel(log_level)

    handler = StreamHandler(stdout)
    formatter: Formatter
    if log_format.lower() == "json":
        # Formatter for JSON logs
        formatter = TimezoneJsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d",
            tz_name=tz_name,
        )
    else:
        # Standard text formatter
        formatter = TimezoneFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            tz_name=tz_name,
        )

    handler.setFormatter(formatter)

    # Avoid duplicating handlers
    if not logger.handlers:
        logger.addHandler(handler)

    # Configure the root logger to see logs from libraries (aiohttp, etc.)
    root_logger = getLogger()
    root_logger.setLevel(DEBUG)

    if not root_logger.handlers:
        root_handler = StreamHandler(stdout)
        root_formatter = TimezoneFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            tz_name=tz_name,
        )
        root_handler.setFormatter(root_formatter)
        root_logger.addHandler(root_handler)
    else:
        for h in root_logger.handlers:
            h.setFormatter(
                TimezoneFormatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    tz_name=tz_name,
                )
            )

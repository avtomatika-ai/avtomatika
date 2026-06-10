# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from datetime import datetime
from logging import DEBUG, Formatter, StreamHandler, getLogger
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from sys import stdout
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pythonjsonlogger import json


class TimezoneFormatter(Formatter):
    """Formatter that respects a custom timezone."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: Literal["%", "{", "$"] = "%",
        validate: bool = True,
        *,
        tz_name: str = "UTC",
    ) -> None:
        super().__init__(fmt, datefmt, style, validate)
        self.tz = ZoneInfo(tz_name)

    def converter(self, timestamp: float) -> datetime:  # type: ignore[override]
        return datetime.fromtimestamp(timestamp, self.tz)

    def formatTime(self, record: Any, datefmt: str | None = None) -> str:
        dt = self.converter(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            try:
                s = dt.isoformat(timespec="milliseconds")
            except TypeError:
                s = dt.isoformat()
        return s


class TimezoneJsonFormatter(json.JsonFormatter):
    """JSON Formatter that respects a custom timezone."""

    def __init__(self, *args: Any, tz_name: str = "UTC", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tz = ZoneInfo(tz_name)

    def formatTime(self, record: Any, datefmt: str | None = None) -> str:
        # Override formatTime to use timezone-aware datetime
        dt = datetime.fromtimestamp(record.created, self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


class LogManager:
    """Manages the lifecycle of the application logging system.
    Avoiding global variables by encapsulating the listener state.
    """

    def __init__(self) -> None:
        self._listener: QueueListener | None = None

    def setup_logging(self, log_level: str = "INFO", log_format: str = "json", tz_name: str = "UTC") -> None:
        """Configures structured logging."""
        if self._listener:
            self._listener.stop()

        log_queue: Queue = Queue(-1)

        formatter: Formatter
        if log_format.lower() == "json":
            formatter = TimezoneJsonFormatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d",
                tz_name=tz_name,
            )
        else:
            formatter = TimezoneFormatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                tz_name=tz_name,
            )

        stream_handler = StreamHandler(stdout)
        stream_handler.setFormatter(formatter)

        self._listener = QueueListener(log_queue, stream_handler)
        self._listener.start()

        queue_handler = QueueHandler(log_queue)

        # Explicitly clear existing handlers to avoid duplicates
        logger = getLogger("avtomatika")
        logger.setLevel(log_level)
        for h in logger.handlers[:]:
            logger.removeHandler(h)
        logger.addHandler(queue_handler)

        root_logger = getLogger()
        root_logger.setLevel(DEBUG)
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)
        root_logger.addHandler(queue_handler)

    def stop(self) -> None:
        """Stops the background logging listener."""
        if self._listener:
            self._listener.stop()
            self._listener = None

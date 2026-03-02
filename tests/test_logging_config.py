# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import logging
from unittest.mock import patch

import pytest

from avtomatika.logging_config import TimezoneFormatter, TimezoneJsonFormatter, setup_logging


@pytest.fixture(autouse=True)
def clear_handlers():
    logger = logging.getLogger("avtomatika")
    root = logging.getLogger()
    logger.handlers = []
    root.handlers = []
    yield
    logger.handlers = []
    root.handlers = []


def test_setup_logging_json():
    """Tests that logging is set up correctly with the JSON formatter."""
    with patch("logging.StreamHandler"):
        setup_logging(log_level="DEBUG", log_format="json")
        logger = logging.getLogger("avtomatika")
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0].formatter, TimezoneJsonFormatter)


def test_setup_logging_text():
    """Tests that logging is set up correctly with the text formatter."""
    with patch("logging.StreamHandler"):
        setup_logging(log_level="INFO", log_format="text")
        logger = logging.getLogger("avtomatika")
        assert logger.level == logging.INFO
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0].formatter, TimezoneFormatter)

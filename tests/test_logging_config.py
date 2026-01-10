import logging
from unittest.mock import patch

from pythonjsonlogger import json
from src.avtomatika.logging_config import setup_logging


def test_setup_logging_json():
    """Tests that logging is set up correctly with the JSON formatter."""
    with patch("logging.StreamHandler"):
        setup_logging(log_level="DEBUG", log_format="json")
        logger = logging.getLogger("avtomatika")
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0].formatter, json.JsonFormatter)


def test_setup_logging_text():
    """Tests that logging is set up correctly with the text formatter."""
    with patch("logging.StreamHandler"):
        setup_logging(log_level="INFO", log_format="text")
        logger = logging.getLogger("avtomatika")
        assert logger.level == logging.INFO
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0].formatter, logging.Formatter)

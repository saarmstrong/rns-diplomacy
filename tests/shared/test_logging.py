"""Tests for shared/logging.py."""

from __future__ import annotations

import logging

from shared.logging import configure_logging, get_logger


def test_configure_logging_sets_level_and_stream_handler():
    logger = configure_logging(level=logging.DEBUG)
    assert logger.name == "rns_diplomacy"
    assert logger.level == logging.DEBUG
    assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


def test_configure_logging_adds_file_handler(tmp_path):
    log_file = tmp_path / "test.log"
    logger = configure_logging(output_file=str(log_file))
    assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    logger.info("hello")
    for handler in logger.handlers:
        handler.flush()
    assert "hello" in log_file.read_text()


def test_configure_logging_replaces_previous_handlers():
    configure_logging()
    first_count = len(get_logger().handlers)
    configure_logging()
    assert len(get_logger().handlers) == first_count


def test_get_logger_default_is_root_namespace():
    assert get_logger().name == "rns_diplomacy"


def test_get_logger_with_name_is_a_child_logger():
    logger = get_logger("coordinator")
    assert logger.name == "rns_diplomacy.coordinator"
    assert logger.parent.name == "rns_diplomacy"

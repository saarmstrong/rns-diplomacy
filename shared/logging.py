"""Logging configuration for RNS Diplomacy.

A single ``rns_diplomacy`` logger namespace, with component sub-loggers
(``rns_diplomacy.coordinator``, ``rns_diplomacy.protocol``, ...) obtained
via ``get_logger``. Used for both operator-facing CLI output and
security-relevant event logging (rejected joins, validation failures —
see the threat model).
"""

from __future__ import annotations

import logging

_LOGGER_NAME = "rns_diplomacy"


def configure_logging(level: int = logging.INFO, output_file: str | None = None) -> logging.Logger:
    """Configure and return the shared ``rns_diplomacy`` logger.

    Replaces any handlers from a previous call, so this is safe to call
    once at process startup (e.g. in a CLI's ``main()``).
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if output_file is not None:
        file_handler = logging.FileHandler(output_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """A logger under the shared ``rns_diplomacy`` namespace, e.g. ``get_logger("coordinator")``."""
    full_name = _LOGGER_NAME if name is None else f"{_LOGGER_NAME}.{name}"
    return logging.getLogger(full_name)

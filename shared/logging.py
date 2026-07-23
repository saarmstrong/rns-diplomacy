"""Consistent, safe logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(level: int | str = logging.INFO, output_file: str | Path | None = None) -> logging.Logger:
    """Configure the project logger once and return it.

    Protocol payloads and private keys must never be logged by callers.
    """
    logger = logging.getLogger("rns_diplomacy")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler: logging.Handler
    if output_file is None:
        handler = logging.StreamHandler()
    else:
        path = Path(output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def game_logger() -> logging.Logger:
    return logging.getLogger("rns_diplomacy.game")


def protocol_logger() -> logging.Logger:
    return logging.getLogger("rns_diplomacy.protocol")

from __future__ import annotations

import logging as std_logging

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(*, verbose: bool = False) -> None:
    level = std_logging.DEBUG if verbose else std_logging.INFO
    std_logging.basicConfig(level=level, format=_DEFAULT_FORMAT)


def get_logger(name: str) -> std_logging.Logger:
    return std_logging.getLogger(name)

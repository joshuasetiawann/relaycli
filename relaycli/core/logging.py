"""Structured logging: a persistent file handler plus a concise stderr one.

Additive to the existing Rich console UX — nothing here replaces or competes
with what the user already sees; it's a second, durable channel for
diagnosing failures after the fact. Writes to
``~/.relaycli/logs/relaycli.log`` (created on first use) so a bad run can be
inspected later, not just watched live.
"""

from __future__ import annotations

import logging
import sys

from relaycli.core.config import CONFIG_DIR

LOG_DIR = CONFIG_DIR / "logs"
LOG_FILE = LOG_DIR / "relaycli.log"

_configured = False


def configure_logging(*, debug: bool = False) -> logging.Logger:
    """Configure the ``relaycli`` logger tree. Idempotent — safe to call more
    than once (e.g. once from ``cli.py`` with the real ``--debug`` value,
    and again implicitly via :func:`get_logger` from anywhere imported
    first); later calls only adjust the stderr handler's level.
    """
    global _configured
    logger = logging.getLogger("relaycli")

    if _configured:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr:
                handler.setLevel(logging.DEBUG if debug else logging.WARNING)
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(file_handler)
    except OSError:
        pass  # a logging setup failure must never take the CLI down with it

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(stderr_handler)

    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """A child logger under the ``relaycli`` tree, e.g. ``get_logger(__name__)``.

    Ensures the tree is configured (with defaults) even if ``cli.py`` never
    ran ``configure_logging`` first — e.g. when the module is used as a
    library or under test.
    """
    configure_logging()
    return logging.getLogger(name if name.startswith("relaycli") else f"relaycli.{name}")

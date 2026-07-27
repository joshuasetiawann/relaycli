"""Tests for relaycli.core.logging — the --debug flag's stderr verbosity
and the persistent log file, including a regression for a bug found by
the Phase 6 gate's G12 functional check (see MIGRATION_NOTES.md)."""

from __future__ import annotations

import logging
import sys

import pytest

from relaycli.core import logging as rl


@pytest.fixture
def clean_logger(tmp_path, monkeypatch):
    """Isolate the ``relaycli`` logger tree for one test: a tmp log file,
    no pre-existing handlers, restored afterward so other tests (which may
    run before or after this file and also touch this same process-wide
    logger via get_logger) are unaffected either way."""
    monkeypatch.setattr(rl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(rl, "LOG_FILE", tmp_path / "relaycli.log")

    logger = logging.getLogger("relaycli")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    saved_configured = rl._configured
    for h in saved_handlers:
        logger.removeHandler(h)
    rl._configured = False

    yield

    for h in logger.handlers[:]:
        logger.removeHandler(h)
        h.close()
    for h in saved_handlers:
        logger.addHandler(h)
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate
    rl._configured = saved_configured


def _stderr_level(logger):
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and h.stream is sys.stderr:
            return h.level
    return None


def test_configure_logging_debug_true_sets_debug_stderr(clean_logger):
    logger = rl.configure_logging(debug=True)
    assert _stderr_level(logger) == logging.DEBUG


def test_configure_logging_debug_false_sets_warning_stderr(clean_logger):
    logger = rl.configure_logging(debug=False)
    assert _stderr_level(logger) == logging.WARNING


def test_configure_logging_default_is_warning_on_first_setup(clean_logger):
    logger = rl.configure_logging()
    assert _stderr_level(logger) == logging.WARNING


def test_get_logger_does_not_downgrade_explicit_debug(clean_logger):
    """Regression: get_logger()'s bare configure_logging() call must not
    silently undo an earlier explicit configure_logging(debug=True) — this
    is exactly what defeated --debug for any module that acquires its
    logger lazily (agent/loop.py, tools/websearch.py) before the fix."""
    logger = rl.configure_logging(debug=True)
    assert _stderr_level(logger) == logging.DEBUG

    rl.get_logger("relaycli.somemodule")

    assert _stderr_level(logger) == logging.DEBUG


def test_configure_logging_explicit_false_still_overrides(clean_logger):
    """Only the implicit/default None is a no-op — an explicit debug=False
    passed after debug=True must still be able to change the level."""
    rl.configure_logging(debug=True)
    logger = rl.configure_logging(debug=False)
    assert _stderr_level(logger) == logging.WARNING


def test_get_logger_returns_child_under_relaycli_namespace(clean_logger):
    log = rl.get_logger("relaycli.tools.websearch")
    assert log.name == "relaycli.tools.websearch"

    log2 = rl.get_logger("bare_name")
    assert log2.name == "relaycli.bare_name"


def test_file_handler_always_debug_regardless_of_stderr_level(clean_logger):
    """The file handler must keep capturing DEBUG-level records even when
    stderr is at WARNING — the file is the durable, always-on channel."""
    rl.configure_logging(debug=False)
    log = rl.get_logger("relaycli.phase6")
    log.debug("hello from test")

    assert "hello from test" in rl.LOG_FILE.read_text(encoding="utf-8")

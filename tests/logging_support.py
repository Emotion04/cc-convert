# SPDX-License-Identifier: Apache-2.0
"""Helpers for tests that exercise ``cc_convert.main.setup_logging``.

``setup_logging`` attaches rotating file handlers to the root and ``"api"``
loggers and keeps them open for the lifetime of the process. That is the right
behaviour for a long-running server, but it means a test which points logging
at a temporary directory cannot delete that directory on Windows — the open
handle raises ``WinError 32``. These helpers release those handles explicitly.
"""

import contextlib
import logging
import tempfile


def close_file_handlers() -> None:
    """Detach and close every file handler on the root and ``"api"`` loggers.

    Only ``FileHandler`` instances are touched, so pytest's own capture
    handlers (``StreamHandler``-based) are left alone.
    """
    for name in ("", "api"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    # Cleanup must never mask the real test result.
                    pass


@contextlib.contextmanager
def temp_log_dir():
    """Like :class:`tempfile.TemporaryDirectory`, but safe for log output.

    Handlers are closed *before* the directory is removed, which is what makes
    the difference on Windows.
    """
    with tempfile.TemporaryDirectory() as directory:
        try:
            yield directory
        finally:
            close_file_handlers()

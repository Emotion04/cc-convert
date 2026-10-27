# SPDX-License-Identifier: Apache-2.0
"""Shared test configuration: keep the suite out of the developer's real state.

The setup below deliberately runs at **import time** rather than inside a
fixture. ``cc_convert.daemon`` binds its manager paths to module-level
constants the moment it is imported (``MANAGER_PID_FILE``,
``MANAGER_CONFIG_FILE``, ``MANAGER_LOG_FILE``), and pytest imports test modules
during collection — before any fixture has had a chance to run. Setting the
environment here is the only point early enough to redirect them.

Run pytest from the repository root (``uv run pytest``) so this file is loaded;
``testpaths = ["tests"]`` in ``pyproject.toml`` makes that the default.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tests.logging_support import close_file_handlers

_SANDBOX = Path(tempfile.mkdtemp(prefix="ccconvert-pytest-"))

_SANDBOX_ENV = {
    "CCCONVERT_CONFIG_DIR": str(_SANDBOX / "config"),
    "CCCONVERT_RUNTIME_DIR": str(_SANDBOX / "runtime"),
}
os.environ.update(_SANDBOX_ENV)

atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)

#: The developer's real config file — the one the suite must never modify.
_REAL_CONFIG = Path.home() / ".ccconvert" / "config.toml"

#: Fail if collection collapses silently (e.g. one module stops importing and
#: takes that whole file's tests with it). Kept well below the current count so
#: ordinary test churn does not trip it.
_MIN_EXPECTED_TESTS = 500


def _fingerprint(path: Path):
    """Cheap change detector: ``None`` when absent, else (mtime, size)."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


@pytest.fixture(scope="session", autouse=True)
def _guard_real_user_config():
    """Fail the session if any test writes to the real ``~/.ccconvert/config.toml``.

    Regression guard for a real defect: a test built a ``ProfileStore`` in
    memory and then saved it, which resolved the *ambient* config path and
    atomically overwrote the developer's live instance configuration. That path
    has no backup and no merge, so the damage was silent and total.

    Turning that into a loud failure is the point of this fixture.
    """
    before = _fingerprint(_REAL_CONFIG)
    yield
    after = _fingerprint(_REAL_CONFIG)
    assert before == after, (
        f"TEST POLLUTION: the real config at {_REAL_CONFIG} changed during this "
        f"test session ({before} -> {after}). A test escaped the sandbox.\n"
        f"  CCCONVERT_CONFIG_DIR  = {os.environ.get('CCCONVERT_CONFIG_DIR')}\n"
        f"  CCCONVERT_RUNTIME_DIR = {os.environ.get('CCCONVERT_RUNTIME_DIR')}"
    )


@pytest.fixture(autouse=True)
def _restore_sandbox_env():
    """Re-assert the sandbox env vars around every test.

    Some tests use ``patch.dict(os.environ, {}, clear=True)``, which wipes the
    sandbox variables for the duration of that test. Restoring them keeps such
    a test from leaking ambient path resolution into whatever runs next.
    """
    os.environ.update(_SANDBOX_ENV)
    yield
    os.environ.update(_SANDBOX_ENV)


@pytest.fixture(autouse=True)
def _disable_upstream_probe(monkeypatch):
    """Keep tests offline: the save-time reachability check makes real calls.

    Tests that assert on warnings patch this back themselves.
    """
    from cc_convert.web import routes

    async def _noop(base_url, api_key="", timeout=4.0):
        return []

    monkeypatch.setattr(routes, "check_upstream_base_url", _noop)


@pytest.fixture(autouse=True)
def _release_log_file_handlers():
    """Close file handlers that ``setup_logging`` leaves attached to loggers.

    ``setup_logging`` attaches rotating file handlers to the root and ``"api"``
    loggers and holds them open for the process lifetime — correct for a server,
    but it means a test that points it at a ``TemporaryDirectory`` cannot delete
    that directory on Windows (``WinError 32``, file in use).

    Only ``FileHandler`` instances are touched, so pytest's own capture handlers
    (``StreamHandler``-based) are left alone.
    """
    yield
    close_file_handlers()


def pytest_collection_modifyitems(session, config, items):
    """Abort if a *full-suite* run collects suspiciously few tests.

    Skipped for partial runs such as ``pytest tests/test_logging.py``, where a
    small count is the expected result rather than a symptom.
    """
    requested = {str(arg).replace("\\", "/").rstrip("/") for arg in config.args}
    if requested - {"tests", ""}:
        return
    if len(items) < _MIN_EXPECTED_TESTS:
        pytest.exit(
            f"Only {len(items)} tests were collected, expected at least "
            f"{_MIN_EXPECTED_TESTS}. A test module most likely stopped "
            f"importing, or a conftest started ignoring a directory.",
            returncode=1,
        )

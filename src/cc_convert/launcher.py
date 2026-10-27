# SPDX-License-Identifier: Apache-2.0
"""Command lines for the daemon/worker subprocesses.

A source checkout starts them as ``python daemon_runner.py`` /
``python worker_runner.py``. A frozen build (PyInstaller) has no ``.py`` files
on disk, so it re-executes the packaged binary with an internal argument that
``main()`` dispatches on. Both modes share the same state and config, so a
manager started by either one is visible to the other.
"""

import sys
from pathlib import Path
from typing import Literal

RunnerKind = Literal["daemon", "worker"]

#: argv[1] values used by frozen builds to re-enter a runner.
INTERNAL_ARGS: dict[str, str] = {"daemon": "__daemon", "worker": "__worker"}

_RUNNER_FILES = {"daemon": "daemon_runner.py", "worker": "worker_runner.py"}


def is_frozen() -> bool:
    """True when running from a bundled executable (PyInstaller/Nuitka)."""
    return bool(getattr(sys, "frozen", False))


def runner_command(kind: RunnerKind) -> list[str]:
    """Return the argv used to start the given runner process."""
    if is_frozen():
        return [sys.executable, INTERNAL_ARGS[kind]]
    return [sys.executable, str(Path(__file__).resolve().parent / _RUNNER_FILES[kind])]

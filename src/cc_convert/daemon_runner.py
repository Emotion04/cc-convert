#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Daemon runner - executed as a standalone child process by daemon.py.

Starts the CC Convert manager/dashboard app with InstanceSupervisor.
Workers are managed by the supervisor, NOT spawned directly here.
"""

import asyncio
import atexit
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

import uvicorn

from cc_convert.main import create_manager_app
from cc_convert.profile_store import load_profile_store
from cc_convert.runtime_paths import get_manager_log_dir, get_runtime_dir
from cc_convert.supervisor import InstanceSupervisor


def log_message(msg: str) -> None:
    """Write timestamped message to stderr (captured by manager log file)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", file=sys.stderr, flush=True)


def _write_manager_pid() -> None:
    """Write this process's PID so 'ccconvert stop' can find the manager."""
    try:
        pid_file = get_runtime_dir() / "manager.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _remove_manager_pid() -> None:
    """Remove the manager PID file on shutdown."""
    try:
        pid_file = get_runtime_dir() / "manager.pid"
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass


def run_manager() -> None:
    """Run the CC Convert manager app."""
    try:
        # 0. Write our PID for reliable stop
        _write_manager_pid()
        atexit.register(_remove_manager_pid)

        # 1. Read manager port from ProfileStore
        store = load_profile_store()
        manager_port = store.manager.port
        manager_host = store.manager.host

        log_message(f"Loaded ProfileStore (revision {store.revision})")
        log_message(f"Manager port: {manager_port}")

        # 2. Set up logging to manager log file
        log_dir = get_manager_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "manager.log"

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        handler = logging.FileHandler(str(log_file), encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        ))
        root_logger.addHandler(handler)

        log_message(f"Logging to {log_file}")

        # 3. Create InstanceSupervisor
        runtime_dir = get_runtime_dir()
        supervisor = InstanceSupervisor(store, runtime_dir)

        # 4. Create manager app via main.create_manager_app(store, supervisor)
        app = create_manager_app(store, supervisor)

        # Store started_at in app state for uptime tracking
        app.state.started_at = datetime.now().timestamp()

        # 5. On startup: launch auto-start instances
        log_message("Starting auto-start instances...")

        async def _startup() -> None:
            await supervisor.startup_all()
            running = supervisor.get_running_instances()
            log_message(f"Auto-start complete: {len(running)} instance(s) running")

        asyncio.run(_startup())

        # 6. Register atexit handler to call supervisor.shutdown_all()
        def _shutdown() -> None:
            log_message("Shutting down all instances...")
            try:
                asyncio.run(supervisor.shutdown_all())
                log_message("All instances stopped")
            except Exception as e:
                log_message(f"Error during shutdown: {e}")

        atexit.register(_shutdown)

        # 7. Handle termination signals for graceful shutdown
        def _signal_handler(signum, frame) -> None:
            sig_name = signal.Signals(signum).name
            log_message(f"Received signal {sig_name}, shutting down...")
            _shutdown()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

        # 8. Start uvicorn with the manager app on manager port
        log_message(f"Starting manager dashboard on http://{manager_host}:{manager_port}")
        uvicorn.run(
            app,
            host=manager_host,
            port=manager_port,
            log_level="info",
            timeout_keep_alive=300,
        )

    except Exception as e:
        log_message(f"Fatal error in manager daemon: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_manager()

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Worker runner - executed as a child process by the supervisor.

Reads a JSON settings snapshot from stdin (one line), validates it,
closes stdin, then starts a Uvicorn proxy app for this instance.
"""

import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from cc_convert.config import Settings
from cc_convert.main import create_proxy_app, setup_logging
from cc_convert.runtime_paths import (
    get_instance_log_dir,
    get_instance_pid_file,
)


def _read_stdin(timeout: float = 5.0) -> str | None:
    """Read exactly one line from stdin with a timeout."""
    result: list[str] = []

    def _reader():
        line = sys.stdin.readline()
        if line:
            result.append(line)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if not result:
        return None
    return result[0].strip()


def _write_pid(pid_path: Path) -> None:
    """Write PID to pidfile."""
    try:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))
    except Exception as e:
        logging.error("Failed to write PID file %s: %s", pid_path, e)


def _remove_pid(pid_path: Path) -> None:
    """Remove pidfile."""
    try:
        if pid_path.exists():
            pid_path.unlink()
    except OSError:
        pass


def run_worker() -> None:
    """Main entry point: read config from stdin, start worker.

    This is intentionally a regular function — uvicorn.run() is synchronous
    (it calls asyncio.run() internally) so nesting would fail."""

    # Step 1: Read one line from stdin with timeout
    line = _read_stdin(timeout=5.0)
    if not line:
        logging.error("Timed out or empty input on stdin")
        sys.exit(1)

    # Step 2: Parse JSON
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        logging.error("Invalid JSON on stdin: %s", e)
        sys.exit(1)

    instance_id = data.get("instance_id")
    settings_kwargs = data.get("settings_kwargs", {})
    internal_token = data.get("internal_token")
    revision = int(data.get("revision", 0))
    host = data.get("host", "127.0.0.1")
    port = data.get("port")

    if not instance_id or not internal_token or port is None:
        logging.error("Missing required keys (instance_id, internal_token, port)")
        sys.exit(1)

    # Step 3: Instantiate Settings
    settings = Settings(**settings_kwargs)
    settings.host = host
    settings.port = port

    # Install the reasoning-history context (rules, corrections, fingerprint).
    from cc_convert import thinking_policy

    thinking_policy.configure(
        instance_id=instance_id,
        base_url=settings.openai_base_url,
        model=settings.default_model,
        upstream_format=settings.upstream_format,
        auto_correct=settings.thinking_history_auto_correct,
        force_manual=settings.thinking_history_force_manual,
    )

    if not settings.openai_api_key:
        logging.error("openai_api_key is required but not set")
        sys.exit(1)

    # Step 4: Close stdin
    try:
        os.close(0)
    except OSError:
        pass
    sys.stdin = open(os.devnull, "r")

    # Step 5: Setup logging (instance-scoped)
    log_dir = get_instance_log_dir(instance_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.log_level, str(log_dir))

    logger = logging.getLogger(__name__)

    # Step 6: Write PID file
    pid_path = get_instance_pid_file(instance_id)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    _write_pid(pid_path)

    # Step 7: Create proxy app
    app = create_proxy_app(settings, instance_id, internal_token, revision=revision)

    # Step 8: Register signal handlers
    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received signal %s, shutting down...", sig_name)
        _remove_pid(pid_path)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Step 9: Start uvicorn
    import uvicorn

    logger.info(
        "Starting uvicorn on %s:%s (instance=%s)",
        host, port, instance_id,
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=300,
    )


if __name__ == "__main__":
    run_worker()

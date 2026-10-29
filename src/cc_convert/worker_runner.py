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
    parent_pid = data.get("parent_pid")

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

    # Step 6.1: Watch the manager. A hard stop (`ccconvert stop -f`, a crash)
    # cannot run the supervisor's shutdown path, and on Windows the worker is
    # not part of the manager's job object, so it would keep the port bound and
    # make the next start fail with WinError 10048. Exit as soon as the manager
    # disappears.
    if isinstance(parent_pid, int) and parent_pid > 0:

        def _watch_parent() -> None:
            import time

            from cc_convert.daemon import _is_process_running

            while True:
                time.sleep(2.0)
                if not _is_process_running(parent_pid):
                    try:
                        logger.info(
                            "Manager (PID %d) is gone, worker shutting down",
                            parent_pid,
                        )
                        _remove_pid(pid_path)
                    finally:
                        os._exit(0)

        threading.Thread(
            target=_watch_parent,
            name="parent-watch",
            daemon=True,
        ).start()

    # Step 6.5: Resolve "auto" once, at startup.
    # Previously "auto" was stored but never probed at runtime, so it silently
    # behaved like chat/completions. Probe now, with the real model when known,
    # and keep the conservative default when the probe is inconclusive.
    if settings.upstream_format == "auto":
        import asyncio

        from cc_convert.profile_store import detect_upstream_format

        try:
            detected = asyncio.run(
                detect_upstream_format(
                    settings.openai_base_url,
                    settings.openai_api_key,
                    timeout=8.0,
                    model=settings.default_model or "ping",
                )
            )
        except Exception as exc:  # never block startup on a probe
            logger.warning("Upstream format probe failed (%s), using chat", exc)
            detected = "chat_completions"
        settings.upstream_format = detected
        logger.info("Auto-detected upstream format: %s", detected)
        # Re-key the learned-correction fingerprint on the resolved format, so
        # switching between "auto" and a concrete value does not look like an
        # upstream change.
        thinking_policy.configure(
            instance_id=instance_id,
            base_url=settings.openai_base_url,
            model=settings.default_model,
            upstream_format=settings.upstream_format,
            auto_correct=settings.thinking_history_auto_correct,
            force_manual=settings.thinking_history_force_manual,
        )

    # "upstream" mode means a real probe, not just the built-in rules.
    #
    # Run it on a background thread: a probe against a reasoning relay can take
    # 15–45 s (measured on aiaimax), and waiting here would keep the instance
    # port closed for that whole time. Until the probe returns, the built-in
    # rules are used, which is exactly the old behaviour for an inconclusive
    # probe anyway.
    if settings.thinking_history == "upstream":

        def _probe_thinking_history() -> None:
            import asyncio

            from cc_convert.thinking_policy import (
                STARTUP_PROBE_TIMEOUT,
                probe_upstream,
                set_probe_result,
            )

            try:
                probe = asyncio.run(
                    probe_upstream(
                        settings.openai_base_url,
                        settings.openai_api_key,
                        model=settings.default_model or "ping",
                        timeout=STARTUP_PROBE_TIMEOUT,
                        upstream_format=settings.upstream_format,
                    )
                )
            except Exception as exc:  # never crash the worker on a probe
                logger.warning("Thinking-history probe failed: %s", exc)
                set_probe_result(None)
                return

            policy = probe.get("policy")
            if not probe.get("ok") or policy not in ("turn", "keep", "drop"):
                logger.info(
                    "Thinking-history probe inconclusive, keeping the rules: %s",
                    probe.get("evidence", ""),
                )
                set_probe_result(None)
                return

            set_probe_result(policy)
            logger.info(
                "Thinking-history probe: %s (%s)",
                policy,
                probe.get("evidence", ""),
            )

        threading.Thread(
            target=_probe_thinking_history,
            name="thinking-probe",
            daemon=True,
        ).start()

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

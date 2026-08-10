# SPDX-License-Identifier: Apache-2.0
"""
Daemon process management for the CC Convert manager/dashboard process only.

Proxy workers are managed by the supervisor module.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from cc_convert.runtime_paths import ensure_dirs, get_runtime_dir

# Paths (runtime_paths-based, replaces old DATA_DIR / PID_FILE / CONFIG_FILE / LOG_FILE)
MANAGER_PID_FILE = get_runtime_dir() / "manager.pid"
MANAGER_CONFIG_FILE = get_runtime_dir() / "manager.json"
MANAGER_LOG_FILE = get_runtime_dir() / "manager.log"


def _read_pid() -> Optional[int]:
    """Read PID from manager pidfile."""
    try:
        if MANAGER_PID_FILE.exists():
            return int(MANAGER_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        pass
    return None


def _remove_pid() -> None:
    """Remove manager pidfile."""
    try:
        if MANAGER_PID_FILE.exists():
            MANAGER_PID_FILE.unlink()
    except OSError:
        pass


def _save_daemon_config(port: int, pid: int | None = None) -> None:
    """Save manager config {port, started_at} to manager config file."""
    ensure_dirs()
    config = {
        "port": port,
        "started_at": time.time(),
    }
    if pid is not None:
        config["pid"] = pid
    try:
        MANAGER_CONFIG_FILE.write_text(json.dumps(config))
    except OSError:
        pass


def _load_daemon_config() -> Optional[dict]:
    """Load manager config from manager config file."""
    try:
        if MANAGER_CONFIG_FILE.exists():
            return json.loads(MANAGER_CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _remove_daemon_config() -> None:
    """Remove manager config file."""
    try:
        if MANAGER_CONFIG_FILE.exists():
            MANAGER_CONFIG_FILE.unlink()
    except OSError:
        pass


def _is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running (cross-platform).

    ``os.kill(pid, 0)`` is unreliable on Windows (always reports the process
    as gone), so query the process handle via ``OpenProcess`` there.
    """
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is already in use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def _find_pid_by_port(port: int) -> Optional[int]:
    """Find the PID of the process listening on *port* (cross-platform).

    Windows uses ``netstat -ano``; POSIX uses ``lsof``.
    """
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in line:
                    # netstat local address format: 0.0.0.0:21389 or [::]:21389
                    local = parts[1]
                    try:
                        if local.rsplit(":", 1)[-1] == str(port):
                            return int(parts[-1])
                    except ValueError:
                        continue
            return None
        else:
            result = subprocess.run(
                ["lsof", "-t", "-i", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().split()[0])
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        pass
    return None


def _find_free_port_from(start: int, exclude: Optional[set[int]] = None) -> Optional[int]:
    """Return the first free port scanning upward from *start*.

    Uses a ``bind()`` probe rather than ``connect_ex``: connect_ex can report
    a listener's backlog-full port as free, while bind() reliably raises
    EADDRINUSE on any occupied port.
    """
    exclude = exclude or set()
    for port in range(start, start + 2000):
        if port in exclude:
            continue
        try:
            # NOTE: no SO_REUSEADDR — on Windows it permits double-binding and
            # would make the probe report an occupied port as free.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    return None


def _cleanup_stale_pidfile() -> None:
    """Remove manager pidfile if the process is not running."""
    pid = _read_pid()
    if pid is not None and not _is_process_running(pid):
        _remove_pid()
        _remove_daemon_config()


def get_status() -> tuple[bool, Optional[int], Optional[dict]]:
    """
    Get manager daemon status.

    Returns:
        Tuple of (is_running, pid, config_dict_with_port)
    """
    _cleanup_stale_pidfile()
    pid = _read_pid()
    config = _load_daemon_config()
    if pid is not None and _is_process_running(pid):
        return True, pid, config
    return False, None, None


def start_daemon(port: int | None = None) -> bool:
    """
    Start the manager/dashboard process as a background daemon.

    Reads the manager port from ProfileStore (or uses the provided override).

    Args:
        port: Manager port override (default: from ProfileStore)

    Returns:
        True if started successfully, False otherwise
    """
    _cleanup_stale_pidfile()

    pid = _read_pid()
    if pid is not None:
        config = _load_daemon_config()
        actual_port = config.get("port", port) if config else port
        print(f"Manager is already running (PID: {pid}, port: {actual_port})", file=sys.stderr)
        print(f"Use 'ccconvert logs' to view output", file=sys.stderr)
        return False

    # Resolve manager port from ProfileStore if not overridden
    from cc_convert.profile_store import load_profile_store, save_profile_store

    store = load_profile_store()
    if port is None:
        port = store.manager.port

    # If the configured port is taken (e.g. an orphaned process is still
    # holding it), scan upward for the next free port and persist it so the
    # Dashboard URL stays stable for subsequent runs.
    if _is_port_in_use(port):
        print(f"Port {port} is in use, finding a free port...", file=sys.stderr)
        new_port = _find_free_port_from(port)
        if new_port is None:
            print(f"Error: no free port found starting from {port}", file=sys.stderr)
            return False
        print(f"Using port {new_port} instead", file=sys.stderr)
        port = new_port
        store.manager.port = port
        save_profile_store(store)

    ensure_dirs()

    # Prepare the command to run the daemon runner as a separate script
    daemon_runner_path = Path(__file__).parent / "daemon_runner.py"
    env = os.environ.copy()
    cmd = [sys.executable, str(daemon_runner_path)]

    try:
        # Open log file
        log_fd = open(MANAGER_LOG_FILE, "a")
        log_fd.write(f"\n\n[{datetime.now()}] Starting ccconvert manager daemon...\n")
        log_fd.flush()

        # Start the process - use new session on Unix to survive parent exit
        kwargs: dict = {
            "stdout": log_fd,
            "stderr": subprocess.STDOUT,
            "env": env,
        }

        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        process = subprocess.Popen(cmd, **kwargs)

        # Close file descriptor in parent (child keeps it open)
        log_fd.close()

        # Give the process a moment to fail (e.g., port in use)
        time.sleep(0.5)

        # Check if process is still running
        if process.poll() is not None:
            print("Failed to start manager - check logs with 'ccconvert logs'", file=sys.stderr)
            return False

        # Wait for port to become active
        time.sleep(0.5)
        for _ in range(10):
            if _is_port_in_use(port):
                break
            time.sleep(0.2)
        else:
            # Port never became active, check if process died
            if process.poll() is not None:
                print("Manager process exited unexpectedly - check logs", file=sys.stderr)
                return False

        # Save the configuration (with child PID for reliable stop)
        _save_daemon_config(port, process.pid)

        print(f"Dashboard started at http://127.0.0.1:{port}")

        return True

    except Exception as e:
        print(f"Failed to start manager: {e}", file=sys.stderr)
        return False


def stop_daemon(force: bool = False) -> bool:
    """
    Stop the manager daemon process.

    Handles the orphaned-process case: if the PID file is gone but the
    configured port is still held (e.g. after a crash), the process is
    located by port and terminated.

    Args:
        force: If True, use SIGKILL instead of SIGTERM

    Returns:
        True if stopped successfully, False otherwise
    """
    config = _load_daemon_config()
    configured_port = config.get("port") if config else None

    pid = _read_pid()
    _cleanup_stale_pidfile()  # removes pid/config if the recorded process is dead

    if pid is None:
        # PID file gone — a leftover process may still hold the port.
        if configured_port and _is_port_in_use(configured_port):
            found = _find_pid_by_port(configured_port)
            if found is not None:
                print(
                    f"Found orphaned process (PID {found}) holding port "
                    f"{configured_port}, stopping it...",
                    file=sys.stderr,
                )
                pid = found
            else:
                print("Manager is not running")
                return True
        else:
            print("Manager is not running")
            return True

    try:
        # Send signal. Windows has no SIGKILL — use taskkill /F for force.
        if sys.platform == "win32":
            if force:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        else:
            signal_num = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, signal_num)

        # Wait for process to terminate
        for _ in range(50):  # Wait up to 5 seconds
            if not _is_process_running(pid):
                break
            time.sleep(0.1)

        if _is_process_running(pid):
            if not force:
                print("Manager did not stop gracefully, use -f to force kill", file=sys.stderr)
                return False
            # Force kill — Windows has no SIGKILL, use taskkill /F.
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)

        _remove_pid()
        _remove_daemon_config()
        print("Manager stopped")
        return True

    except (OSError, ProcessLookupError):
        _remove_pid()
        _remove_daemon_config()
        print("Manager stopped")
        return True
    except Exception as e:
        print(f"Failed to stop manager: {e}", file=sys.stderr)
        return False


def restart_daemon() -> bool:
    """
    Restart the manager daemon (stop + start).
    """
    print("Restarting manager...")
    # Use force=True to ensure the old process is killed even if it's stuck
    stop_daemon(force=True)
    # Longer delay to ensure port is fully released
    time.sleep(1)
    return start_daemon()


def show_logs(follow: bool = False, lines: int = 50) -> bool:
    """
    Show manager logs.

    Args:
        follow: If True, follow log output (like tail -f)
        lines: Number of lines to show from the end

    Returns:
        True if successful, False otherwise
    """
    if not MANAGER_LOG_FILE.exists():
        print("No manager log file found", file=sys.stderr)
        return False

    try:
        if follow:
            if sys.platform == "win32":
                # Windows: polling read-loop (tail not available)
                try:
                    with open(MANAGER_LOG_FILE, "r") as f:
                        # Print last N lines first
                        all_lines = f.readlines()
                        for line in all_lines[-lines:]:
                            print(line, end="")
                        # Then poll for new lines
                        while True:
                            line = f.readline()
                            if line:
                                print(line, end="")
                            else:
                                time.sleep(0.1)
                except KeyboardInterrupt:
                    pass
            else:
                # Unix: use tail -f
                try:
                    subprocess.run(
                        ["tail", "-f", "-n", str(lines), str(MANAGER_LOG_FILE)],
                        check=True,
                    )
                except KeyboardInterrupt:
                    pass
        else:
            # Read and print last N lines
            with open(MANAGER_LOG_FILE, "r") as f:
                content = f.readlines()
                for line in content[-lines:]:
                    print(line, end="")

        return True

    except Exception as e:
        print(f"Failed to read logs: {e}", file=sys.stderr)
        return False

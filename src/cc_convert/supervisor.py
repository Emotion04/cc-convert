# SPDX-License-Identifier: Apache-2.0
"""Instance supervisor -- manages subprocess lifecycle for CC Convert worker instances."""

import asyncio
import json
import logging
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from cc_convert.profile_store import (
    InstanceProfile,
    ProfileStore,
    _find_free_port,
    build_worker_settings,
)
from cc_convert.runtime_paths import (
    get_instance_log_dir,
    get_instance_meta_file,
    get_instance_pid_file,
)

logger = logging.getLogger(__name__)


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is already in use on the given host via socket connect."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def find_free_port(start: int = 18000, end: int = 29999, exclude: Optional[set[int]] = None) -> int:
    """Find a free TCP port in [start, end], excluding specified ports."""
    return _find_free_port(start=start, end=end, exclude=exclude)


def _find_pid_by_port(port: int) -> Optional[int]:
    """Find the PID of the process listening on *port* (cross-platform).

    Windows uses ``netstat -ano``; POSIX uses ``lsof``.
    """
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in line:
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


def _process_name(pid: int) -> str:
    """Return a short process name for *pid* (best-effort)."""
    try:
        if platform.system() == "Windows":
            # tasklist rather than wmic: Windows 11 24H2+ no longer ships wmic,
            # so this silently returned "" and left the port owner unidentified
            # in the dashboard's port-conflict view.
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                # Rows look like: "python.exe","12345","Console","1","50,000 K".
                # When nothing matches, tasklist prints a localised message
                # instead of a CSV row, so only trust rows starting with a quote.
                if line.startswith('"'):
                    return line.split('","')[0].strip('"')
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip().split()[0]
    except (subprocess.SubprocessError, OSError):
        pass
    return ""


def _upstream_reachable(upstream_base_url: str, timeout: float = 3.0) -> bool:
    """Check whether the upstream host:port is reachable (TCP connect only)."""
    from urllib.parse import urlparse

    try:
        u = urlparse(upstream_base_url)
        host = u.hostname
        if not host:
            return False
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


class InstanceSupervisor:
    """Manages instance_id -> subprocess.Popen mappings.

    Initialized with a ProfileStore reference and a runtime directory path.
    """

    def __init__(self, store: ProfileStore, runtime_dir: Path):
        self._processes: dict[str, subprocess.Popen] = {}
        self._store = store
        self._runtime_dir = runtime_dir
        # Shared HTTP client for local health probes, created lazily inside
        # the event loop that actually uses it. Creating a fresh
        # httpx.AsyncClient per request is slow on Windows (~0.75s each) and a
        # client created in one event loop cannot be reused in another, so it
        # must be created once inside the uvicorn loop (see ensure_client).
        self._client: httpx.AsyncClient | None = None

    async def ensure_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it in the current event loop."""
        if self._client is None or self._client.is_closed:
            # trust_env=False keeps the local health probe off any system HTTP
            # proxy (Clash etc.), which would otherwise stall it.
            self._client = httpx.AsyncClient(timeout=2, trust_env=False)
        return self._client

    # ------------------------------------------------------------------
    # Instance lifecycle
    # ------------------------------------------------------------------

    async def start_instance(self, instance: InstanceProfile) -> bool:
        """Start a worker subprocess for the given instance profile.

        Validates config, checks port availability, spawns the child,
        polls the health endpoint, and registers the process on success.
        """
        # 1. Validate instance config. target_model is OPTIONAL (empty =
        #    passthrough), so it is intentionally not required here.
        if not instance.name or not instance.upstream_base_url:
            return False
        if instance.port < 1 or instance.port > 65535:
            return False

        # 2. Port recovery. If the port is held by a dead/crashed worker (or
        #    any bystander), evict it so the new worker can bind.
        port_owner = _find_pid_by_port(instance.port)
        if port_owner is not None:
            our_proc = self._processes.get(instance.id)
            if our_proc is not None and our_proc.pid == port_owner:
                # Already our own process — port is correctly held.
                pass
            else:
                # Stale occupant: forcibly evict.
                logging.warning(
                    "Port %d is held by PID %d (%s) — evicting for instance %s",
                    instance.port, port_owner, _process_name(port_owner) or "?",
                    instance.id,
                )
                self._terminate_pid(port_owner)
                # Wait up to 4 s for the OS to release the port.
                for _ in range(20):
                    if not _is_port_in_use(instance.port):
                        break
                    await asyncio.sleep(0.2)
                if _is_port_in_use(instance.port):
                    logging.error("Port %d still in use after evicting PID %d",
                                  instance.port, port_owner)
                    return False

        # 3. Build settings kwargs via profile_store.build_worker_settings.
        settings_kwargs = build_worker_settings(self._store, instance)

        # 4. Generate internal token for health-check authentication.
        internal_token = secrets.token_urlsafe(32)

        # 5. Spawn worker_runner.py as a child process.
        worker_runner_path = Path(__file__).parent / "worker_runner.py"

        env = os.environ.copy()
        # Only pass PYTHONPATH — secrets go through stdin, never env vars.
        # supervisor.py lives at <src>/cc_convert/, so the package
        # root is two parents up: <src>.
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

        creationflags = 0
        start_new_session = False
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            start_new_session = True

        try:
            proc = subprocess.Popen(
                [sys.executable, str(worker_runner_path)],
                env=env,
                stdin=subprocess.PIPE,
                # Worker logs to its own files; discard stdout so the pipe
                # never fills up. Keep stderr so failures can be surfaced.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except Exception as e:
            logging.warning("Failed to spawn worker for %s: %s", instance.id, e)
            return False

        # 6. Pass settings snapshot as a single JSON line to stdin, then close.
        stdin_payload = json.dumps({
            "instance_id": instance.id,
            "internal_token": internal_token,
            "revision": self._store.revision,
            "settings_kwargs": settings_kwargs,
            "host": "127.0.0.1",
            "port": instance.port,
        })
        try:
            proc.stdin.write(stdin_payload.encode("utf-8"))
            proc.stdin.close()
        except Exception:
            self._terminate_process(proc)
            return False

        # 7. Poll /_ccconvert/status on http://127.0.0.1:{port}/_ccconvert/status
        #    with Authorization: Bearer {internal_token}.  Retry up to 60
        #    times at 200 ms intervals (~12s) — cold start on Windows can
        #    exceed a few seconds. trust_env=False keeps the health probe off
        #    any system HTTP proxy (Clash etc.).
        client = await self.ensure_client()
        for _ in range(60):
            # If process exited early, bail immediately.
            if proc.poll() is not None:
                stderr_tail = ""
                try:
                    if proc.stderr is not None:
                        stderr_tail = proc.stderr.read().decode("utf-8", errors="replace")[-500:]
                except Exception:
                    pass
                logging.error(
                    "Worker for %s exited early (rc=%s) on port %s: %s",
                    instance.id, proc.returncode, instance.port, stderr_tail,
                )
                self._terminate_process(proc)
                return False

            try:
                resp = await client.get(
                    f"http://127.0.0.1:{instance.port}/_ccconvert/status",
                    headers={"Authorization": f"Bearer {internal_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if (
                        data.get("instance_id") == instance.id
                        and data.get("revision") == self._store.revision
                    ):
                        # 8. Success -- store handle, write meta.
                        self._processes[instance.id] = proc
                        self._write_instance_meta(instance, proc.pid, internal_token)
                        return True
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                logging.debug("health poll %s: %r", instance.port, e)
                pass
            await asyncio.sleep(0.2)

        # 9. Health check failed -- tear down.
        self._terminate_process(proc)
        return False

    async def stop_instance(self, instance_id: str, force: bool = False) -> bool:
        """Stop a running instance.

        Sends SIGTERM (CTRL_BREAK_EVENT on Windows).  Waits up to 5 s.
        If still alive and *force* is True, kills the process.
        Cleans up PID file, meta file, and removes from the process map.
        """
        proc = self._processes.pop(instance_id, None)
        if proc is None:
            return True  # already stopped

        if proc.poll() is not None:
            # Already dead -- just clean up files.
            self._cleanup_instance_files(instance_id)
            return True

        try:
            if platform.system() == "Windows":
                # proc.terminate() maps to TerminateProcess on Windows — reliable.
                # CTRL_BREAK_EVENT is not (the child must opt in to handle it).
                proc.terminate()
            else:
                proc.terminate()

            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if force:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception:
            pass

        self._cleanup_instance_files(instance_id)
        return True

    async def restart_instance(self, instance_id: str) -> bool:
        """Get the current instance config, stop, then start."""
        instance = self._find_instance(instance_id)
        if instance is None:
            return False
        await self.stop_instance(instance_id)
        return await self.start_instance(instance)

    async def update_instance(self, instance_id: str, patch: dict) -> dict:
        """Partially update an instance config with rollback on failure.

        Allowed patch keys: name, port, upstream_base_url, upstream_api_key,
        target_model, auto_start.

        - If only name/auto_start are patched: just save store, no restart.
        - If port unchanged and instance running: stop, start candidate,
          health-check.  On failure: restore old config and restart old.
        - If port changed and instance running: start candidate on new port,
          health-check, stop old, save store.  On failure: kill candidate,
          old instance is untouched.
        """
        allowed = {"name", "port", "upstream_base_url", "upstream_api_key", "target_model", "auto_start", "upstream_format",
                   "client_model", "client_sonnet_model", "client_opus_model", "client_haiku_model"}
        patch = {k: v for k, v in patch.items() if k in allowed}
        if not patch:
            return {"success": True}

        instance = self._find_instance(instance_id)
        if instance is None:
            return {"success": False, "error": "Instance not found"}

        # Snapshot original values for rollback.
        orig = {
            "name": instance.name,
            "port": instance.port,
            "upstream_base_url": instance.upstream_base_url,
            "upstream_api_key": instance.upstream_api_key,
            "target_model": instance.target_model,
            "client_model": instance.client_model,
            "client_sonnet_model": instance.client_sonnet_model,
            "client_opus_model": instance.client_opus_model,
            "client_haiku_model": instance.client_haiku_model,
            "upstream_format": instance.upstream_format,
            "auto_start": instance.auto_start,
        }

        # If only safe-to-apply-without-restart fields changed.
        only_safe = all(k in ("name", "auto_start", "client_model", "client_sonnet_model", "client_opus_model", "client_haiku_model") for k in patch)

        # Apply patch to profile in-memory.
        for k, v in patch.items():
            setattr(instance, k, v)

        was_running = instance_id in self._processes

        if only_safe or not was_running:
            # Just save store -- no restart needed.
            try:
                from cc_convert.profile_store import save_profile_store
                save_profile_store(self._store)
                return {"success": True}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        # --- Restart needed ---
        port_changed = "port" in patch and patch["port"] != orig["port"]

        if not port_changed:
            # Same port: stop (force — the old worker must release the port),
            # start candidate, health-check.
            await self.stop_instance(instance_id, force=True)
            ok = await self.start_instance(instance)
            if ok:
                try:
                    from cc_convert.profile_store import save_profile_store
                    save_profile_store(self._store)
                except Exception:
                    # The instance did restart successfully, so this remains a
                    # success for the caller — reporting failure here would show
                    # the UI an error while the proxy is actually running on the
                    # new port. But the change is lost on next restart, so say so.
                    logger.exception(
                        "Instance %s restarted but its config was not persisted",
                        instance_id,
                    )
                return {"success": True}

            # Rollback: restore old config, restart old instance.
            for k, v in orig.items():
                setattr(instance, k, v)
            await self.start_instance(instance)
            return {"success": False, "error": "Instance failed health check after update, rolled back"}
        else:
            # Port changed: start candidate on new port first.
            ok = await self.start_instance(instance)
            if not ok:
                # Revert patch in memory.
                for k, v in orig.items():
                    setattr(instance, k, v)
                return {"success": False, "error": "Failed to start on new port, old instance untouched"}

            # Candidate is healthy -- stop old, save config.
            await self.stop_instance(instance_id, force=True)
            try:
                from cc_convert.profile_store import save_profile_store
                save_profile_store(self._store)
            except Exception:
                # See the note above: the new port is live, so this stays a
                # success — but the change will not survive a restart.
                logger.exception(
                    "Instance %s moved ports but its config was not persisted",
                    instance_id,
                )
            return {"success": True}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_instance_status(self, instance_id: str) -> dict:
        """Get live status of an instance.

        If running, polls /_ccconvert/status.  If stopped but config exists,
        returns {"status": "stopped"}.
        """
        proc = self._processes.get(instance_id)
        if proc is not None and proc.poll() is None:
            # Process is alive -- try to poll.
            instance = self._find_instance(instance_id)
            port = instance.port if instance else None
            meta = self._read_instance_meta(instance_id)
            token = meta.get("internal_token", "") if meta else ""

            if port:
                # trust_env=False keeps the local health probe off any system
                # HTTP proxy (Clash etc.); reuse the shared client to avoid the
                # ~0.75s cost of creating a fresh httpx.AsyncClient on Windows.
                client = await self.ensure_client()
                try:
                    resp = await client.get(
                        f"http://127.0.0.1:{port}/_ccconvert/status",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if resp.status_code == 200:
                        return resp.json()
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass

            # Fallback: process exists but we couldn't reach it.
            return {
                "status": "running",
                "pid": proc.pid,
                "port": port or 0,
            }

        # Check if config exists (stopped instance).
        instance = self._find_instance(instance_id)
        if instance is not None:
            return {"status": "stopped", "port": instance.port}

        return {"status": "stopped"}

    async def get_instance_logs(self, instance_id: str, lines: int = 100, log_type: str = "server") -> list[dict]:
        """Read the tail of the instance log file and return parsed entries."""
        log_dir = get_instance_log_dir(instance_id)
        log_file = log_dir / f"{log_type}.log"
        if not log_file.exists():
            return []

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
        except OSError:
            return []

        tail = all_lines[-lines:]
        entries = []
        for i, line in enumerate(tail):
            line = line.strip()
            if not line:
                continue
            entry = {"id": i, "timestamp": "", "level": "INFO", "message": line}
            parts = line.split(" - ", 3)
            if len(parts) >= 4:
                entry["timestamp"] = parts[0]
                entry["level"] = parts[2].upper()
                entry["message"] = parts[3]
            elif len(parts) >= 3:
                entry["timestamp"] = parts[0]
                entry["message"] = parts[2]
            entries.append(entry)
        return entries

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    async def startup_all(self) -> None:
        """Start every instance with auto_start=True.  Log failures, don't block."""
        for inst in self._store.instances:
            if inst.auto_start:
                ok = await self.start_instance(inst)
                if not ok:
                    # Log and continue. Previously a bare `pass`, which made
                    # every auto-start failure on manager boot invisible.
                    logger.warning(
                        "Auto-start failed for instance %s (%s)", inst.id, inst.name
                    )

    async def shutdown_all(self) -> None:
        """Stop every running instance and clean up."""
        for inst_id in list(self._processes.keys()):
            await self.stop_instance(inst_id)
        self._processes.clear()
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def get_running_instances(self) -> list[str]:
        """Return list of instance IDs with live process handles."""
        return [i for i, p in self._processes.items() if p.poll() is None]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_instance(self, instance_id: str) -> Optional[InstanceProfile]:
        for inst in self._store.instances:
            if inst.id == instance_id:
                return inst
        return None

    def _write_instance_meta(self, instance: InstanceProfile, pid: int, internal_token: str) -> None:
        """Write instance metadata JSON to the runtime directory."""
        meta_path = get_instance_meta_file(instance.id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "instance_id": instance.id,
            "name": instance.name,
            "port": instance.port,
            "pid": pid,
            "internal_token": internal_token,
            "revision": self._store.revision,
            "started_at": time.time(),
        }
        try:
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError:
            pass

        pid_path = get_instance_pid_file(instance.id)
        try:
            pid_path.write_text(str(pid))
        except OSError:
            pass

    def _read_instance_meta(self, instance_id: str) -> dict:
        """Read instance metadata from the runtime directory."""
        meta_path = get_instance_meta_file(instance_id)
        try:
            if meta_path.exists():
                return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _cleanup_instance_files(self, instance_id: str) -> None:
        """Remove PID file and meta file for an instance."""
        for path in (get_instance_pid_file(instance_id), get_instance_meta_file(instance_id)):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    @staticmethod
    def _terminate_pid(pid: int) -> None:
        """Kill a process by PID, cross-platform."""
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=10,
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception as exc:
            logging.warning("Failed to kill PID %d: %s", pid, exc)

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        """Safely terminate a subprocess, ignoring errors."""
        try:
            if proc.poll() is None:
                if platform.system() == "Windows":
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Port health management
    # ------------------------------------------------------------------

    def check_instance_port(self, instance_id: str) -> dict:
        """Report health of an instance's local port and its upstream.

        - ``listening``: whether anything is bound to the instance port
        - ``pid``: PID holding the port (from netstat/lsof), if any
        - ``our_pid``: the PID of the worker process we spawned, if alive
        - ``status``: healthy | conflict | down
        - ``upstream``: whether the upstream host:port is TCP-reachable
        """
        instance = self._find_instance(instance_id)
        if instance is None:
            return {"instance_id": instance_id, "error": "Instance not found"}

        port = instance.port
        listening = _is_port_in_use(port)
        pid = _find_pid_by_port(port) if listening else None
        proc = self._processes.get(instance_id)
        our_pid = proc.pid if proc is not None and proc.poll() is None else None

        if listening and pid is not None and pid == our_pid:
            status = "healthy"
        elif listening:
            status = "conflict"  # occupied by another process
        else:
            status = "down"

        return {
            "instance_id": instance_id,
            "name": instance.name,
            "port": port,
            "listening": listening,
            "pid": pid,
            "pid_name": _process_name(pid) if pid else None,
            "our_pid": our_pid,
            "status": status,
            "upstream": _upstream_reachable(instance.upstream_base_url),
        }

    def get_port_overview(self) -> list[dict]:
        """Health summary for the manager port and every instance port."""
        rows = []
        manager_port = self._store.manager.port
        manager_listening = _is_port_in_use(manager_port)
        manager_pid = _find_pid_by_port(manager_port) if manager_listening else None
        rows.append(
            {
                "port": manager_port,
                "name": "Manager (Dashboard)",
                "kind": "manager",
                "listening": manager_listening,
                "pid": manager_pid,
                "pid_name": _process_name(manager_pid) if manager_pid else None,
                "status": "healthy" if manager_listening else "down",
                "upstream": None,
            }
        )
        for inst in self._store.instances:
            row = self.check_instance_port(inst.id)
            row["kind"] = "instance"
            rows.append(row)
        return rows

    async def rebind_instance_port(self, instance_id: str) -> dict:
        """Assign the instance a new free port and restart it if running.

        Used when an instance's port is hijacked by another process.
        """
        instance = self._find_instance(instance_id)
        if instance is None:
            return {"success": False, "error": "Instance not found"}

        from cc_convert.profile_store import _find_free_port, save_profile_store

        used = {self._store.manager.port}
        used.update(inst.port for inst in self._store.instances)
        try:
            new_port = _find_free_port(start=instance.port + 1, end=29999, exclude=used)
        except RuntimeError:
            # Range exhausted upward — fall back to a fresh scan from 18401
            try:
                new_port = _find_free_port(start=18401, end=29999, exclude=used)
            except RuntimeError as e:
                return {"success": False, "error": str(e)}

        old_port = instance.port
        was_running = instance_id in self._processes

        instance.port = new_port
        save_profile_store(self._store)

        if was_running:
            await self.restart_instance(instance_id)

        return {
            "success": True,
            "old_port": old_port,
            "new_port": new_port,
            "restarted": was_running,
        }

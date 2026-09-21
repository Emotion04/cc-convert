"""
Advanced tests for daemon module covering subprocess and edge cases.
"""

import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cc_convert import daemon

# signal.SIGKILL simply does not exist on Windows, and daemon.py never uses it
# there (the win32 force path shells out to `taskkill /F` instead).
_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="signal.SIGKILL is POSIX-only"
)


@pytest.fixture(autouse=True)
def _isolated_daemon_paths(tmp_path, monkeypatch):
    """Point daemon's module-level paths at a real, existing temp directory.

    ``daemon`` binds ``MANAGER_PID_FILE`` / ``MANAGER_CONFIG_FILE`` /
    ``MANAGER_LOG_FILE`` at import time, so they cannot pick up the sandbox
    conftest sets up. Rebinding them here also guarantees the directory exists,
    which ``start_daemon`` needs because it opens the manager log for append.
    """
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(daemon, "MANAGER_PID_FILE", runtime / "manager.pid")
    monkeypatch.setattr(daemon, "MANAGER_CONFIG_FILE", runtime / "manager.json")
    monkeypatch.setattr(daemon, "MANAGER_LOG_FILE", runtime / "manager.log")


class TestStartDaemonAdvanced:
    """Advanced tests for start_daemon function."""

    def test_start_daemon_success(self):
        """Test successful daemon start."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll = MagicMock(return_value=None)

        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=False):
                    with patch.object(daemon, "ensure_dirs"):
                        with patch("subprocess.Popen", return_value=mock_process):
                            with patch("time.sleep"):
                                with patch.object(daemon, "_save_daemon_config") as mock_save:
                                    with patch.object(daemon, "_is_port_in_use", side_effect=[False, True]):
                                        result = daemon.start_daemon(8080)
                                        assert result is True
                                        mock_save.assert_called_once()

    def test_start_daemon_skips_ports_assigned_to_instances(self):
        """The manager must not take a port an instance is configured to use.

        On a fresh install the configured manager port (18400) is often already
        taken, and the scan upward used to ignore instance ports — so the
        manager could squat on the default instance's 18401. The instance then
        could never start, yet status still reported it "running", because
        something *was* listening on its port.
        """
        from cc_convert.profile_store import (
            InstanceProfile,
            ManagerConfig,
            ProfileStore,
        )

        store = ProfileStore(
            manager=ManagerConfig(port=18400),
            instances=[
                InstanceProfile(
                    name="default",
                    port=18401,
                    upstream_base_url="http://127.0.0.1:9/v1",
                )
            ],
        )

        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch(
                    "cc_convert.profile_store.load_profile_store", return_value=store
                ):
                    with patch.object(daemon, "_is_port_in_use", return_value=True):
                        with patch.object(
                            daemon, "_find_free_port_from", return_value=None
                        ) as mock_find:
                            daemon.start_daemon()

        assert mock_find.call_args.kwargs["exclude"] == {18401}

    def test_start_daemon_process_exits_immediately(self):
        """Test when daemon process exits immediately."""
        mock_process = MagicMock()
        mock_process.poll = MagicMock(return_value=1)  # Process exited

        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=False):
                    with patch.object(daemon, "ensure_dirs"):
                        with patch("subprocess.Popen", return_value=mock_process):
                            with patch("time.sleep"):
                                result = daemon.start_daemon()
                                assert result is False

    def test_start_daemon_port_never_active(self):
        """Test when port never becomes active and process dies."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        # First poll returns None (running), second poll returns exit code (died)
        mock_process.poll = MagicMock(side_effect=[None, 1])

        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=False):
                    with patch.object(daemon, "ensure_dirs"):
                        with patch("subprocess.Popen", return_value=mock_process):
                            with patch("time.sleep"):
                                result = daemon.start_daemon()
                                assert result is False

    def test_start_daemon_exception(self):
        """Test exception handling during start - exception in subprocess.Popen."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=False):
                    with patch.object(daemon, "ensure_dirs"):
                        # Patch subprocess.Popen to raise exception (inside try block)
                        with patch("subprocess.Popen", side_effect=Exception("Failed to start")):
                            result = daemon.start_daemon()
                            assert result is False

    def test_start_daemon_setsid_on_unix(self):
        """Test that setsid is used on Unix platforms."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll = MagicMock(return_value=None)

        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=False):
                    with patch.object(daemon, "ensure_dirs"):
                        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
                            with patch("time.sleep"):
                                with patch.object(daemon, "_save_daemon_config"):
                                    with patch.object(daemon, "_is_port_in_use", side_effect=[False, True]):
                                        with patch.object(sys, "platform", "linux"):
                                            daemon.start_daemon(8080)
                                            # Check that start_new_session was passed
                                            call_kwargs = mock_popen.call_args[1]
                                            assert call_kwargs.get("start_new_session") is True

    def test_start_daemon_no_setsid_on_windows(self):
        """Test that setsid is not used on Windows."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll = MagicMock(return_value=None)

        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=False):
                    with patch.object(daemon, "ensure_dirs"):
                        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
                            with patch("time.sleep"):
                                with patch.object(daemon, "_save_daemon_config"):
                                    with patch.object(daemon, "_is_port_in_use", side_effect=[False, True]):
                                        with patch.object(sys, "platform", "win32"):
                                            daemon.start_daemon(8080)
                                            # Check that start_new_session was not passed or is False
                                            call_kwargs = mock_popen.call_args[1]
                                            assert call_kwargs.get("start_new_session") is not True


class TestStopDaemonAdvanced:
    """Advanced tests for stop_daemon function."""

    @_POSIX_ONLY
    def test_stop_daemon_force_kill_after_timeout(self):
        """Test force kill when graceful stop times out."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                # Process keeps running
                with patch.object(daemon, "_is_process_running", return_value=True):
                    with patch("os.kill") as mock_kill:
                        with patch.object(daemon, "_remove_pid"):
                            with patch.object(daemon, "_remove_daemon_config"):
                                with patch("time.sleep"):
                                    # Exercise the POSIX branch deliberately.
                                    # On win32 the force path shells out to
                                    # `taskkill /F`, which would kill a real
                                    # process (12345 could be anything) and
                                    # never calls os.kill at all.
                                    with patch.object(sys, "platform", "linux"):
                                        result = daemon.stop_daemon(force=True)
                                        assert result is True
                                        # Should be called with SIGKILL
                                        mock_kill.assert_called_with(12345, signal.SIGKILL)

    @_POSIX_ONLY
    def test_stop_daemon_force_flag_immediate(self):
        """Test immediate SIGKILL with force flag."""
        # Test that force=True uses SIGKILL - simplified test
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                # POSIX branch: the assertions below are about os.kill/SIGKILL,
                # which the win32 force path never uses.
                with patch.object(sys, "platform", "linux"):
                    # First call returns True (process running), second returns False (exits loop)
                    # Third call returns False (final check)
                    with patch.object(daemon, "_is_process_running", side_effect=[True, False, False]):
                        with patch("os.kill") as mock_kill:
                            with patch.object(daemon, "_remove_pid"):
                                with patch.object(daemon, "_remove_daemon_config"):
                                    result = daemon.stop_daemon(force=True)
                                    assert result is True
                                    # Verify SIGKILL was used (not SIGTERM)
                                    calls = mock_kill.call_args_list
                                    assert len(calls) >= 1
                                    assert calls[0][0][1] == signal.SIGKILL

    def test_stop_daemon_force_uses_taskkill_on_windows(self):
        """On win32 the force path must use `taskkill /F`, never os.kill.

        This branch used to execute for real during the test suite — only
        os.kill was mocked, so `taskkill /F /PID 12345` actually ran and could
        have killed an unrelated process. It is asserted here instead.
        """
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                # Alive during the stale-pidfile check, gone by the first
                # wait-loop iteration (the kill worked).
                with patch.object(
                    daemon, "_is_process_running", side_effect=[True, False, False]
                ):
                    with patch("os.kill") as mock_kill:
                        with patch("time.sleep"):
                            with patch.object(daemon, "_remove_pid"):
                                with patch.object(daemon, "_remove_daemon_config"):
                                    with patch("subprocess.run") as mock_run:
                                        with patch.object(sys, "platform", "win32"):
                                            result = daemon.stop_daemon(force=True)

                                        assert result is True
                                        mock_run.assert_called_with(
                                            ["taskkill", "/F", "/PID", "12345"],
                                            capture_output=True,
                                            timeout=10,
                                        )
                                        mock_kill.assert_not_called()

    def test_stop_daemon_reports_failure_when_process_survives(self):
        """A stop that does not actually stop anything must not report success.

        The force-kill path used to delete the pidfile and return True without
        checking whether the process had died, so a failed stop looked like a
        clean one *and* destroyed the only record of the surviving process.
        """
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch.object(daemon, "_is_process_running", return_value=True):
                    with patch("os.kill"):
                        with patch("time.sleep"):
                            with patch.object(daemon, "_remove_pid") as mock_rm_pid:
                                with patch.object(
                                    daemon, "_remove_daemon_config"
                                ) as mock_rm_cfg:
                                    with patch("subprocess.run"):
                                        with patch.object(sys, "platform", "win32"):
                                            result = daemon.stop_daemon(force=True)

                                        assert result is False
                                        # The pidfile is the only record of the
                                        # process still holding the port.
                                        mock_rm_pid.assert_not_called()
                                        mock_rm_cfg.assert_not_called()

    def test_stop_daemon_refuses_to_kill_unrelated_port_holder(self):
        """A process found only by port must be verified before being killed."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_load_daemon_config", return_value={"port": 18400}):
                    with patch.object(daemon, "_is_port_in_use", return_value=True):
                        with patch.object(daemon, "_find_pid_by_port", return_value=999):
                            with patch.object(
                                daemon, "_looks_like_manager", return_value=False
                            ):
                                with patch.object(daemon, "_force_kill") as mock_kill:
                                    result = daemon.stop_daemon(force=True)

                                    assert result is False
                                    mock_kill.assert_not_called()

    def test_stop_daemon_oserror(self):
        """Test stop_daemon with OSError."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch("os.kill", side_effect=OSError("Permission denied")):
                    with patch.object(daemon, "_remove_pid"):
                        with patch.object(daemon, "_remove_daemon_config"):
                            result = daemon.stop_daemon()
                            assert result is True

    def test_stop_daemon_generic_exception(self):
        """Test stop_daemon with generic exception."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch.object(sys, "platform", "linux"):
                    with patch.object(daemon, "_is_process_running", return_value=True):
                        with patch("os.kill", side_effect=Exception("Unexpected")):
                            result = daemon.stop_daemon()
                            assert result is False


class TestShowLogsAdvanced:
    """Advanced tests for show_logs function."""

    def test_show_logs_follow_mode(self):
        """Test show_logs with follow mode."""
        with patch.object(Path, "exists", return_value=True):
            # Assertions below expect the `tail -f` branch, so pin the platform.
            # On win32 show_logs uses a polling read loop that never returns
            # here (it would spin forever), and never calls subprocess.run.
            with patch.object(sys, "platform", "linux"):
                with patch("subprocess.run") as mock_run:
                    result = daemon.show_logs(follow=True, lines=50)
                    assert result is True
                    mock_run.assert_called_once()
                    # Check that tail -f was called
                    call_args = mock_run.call_args[0][0]
                    assert "tail" in call_args
                    assert "-f" in call_args

    def test_show_logs_follow_keyboard_interrupt(self):
        """Test show_logs follow mode with keyboard interrupt."""
        with patch.object(Path, "exists", return_value=True):
            # Pin to the `tail -f` branch; the win32 polling loop never returns.
            with patch.object(sys, "platform", "linux"):
                with patch("subprocess.run", side_effect=KeyboardInterrupt):
                    result = daemon.show_logs(follow=True)
                    assert result is True  # Should return True after interrupt


class TestRemoveDaemonConfig:
    """Tests for _remove_daemon_config function."""

    def test_remove_existing_config(self):
        """Test removing existing config file."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "unlink") as mock_unlink:
                daemon._remove_daemon_config()
                mock_unlink.assert_called_once()

    def test_remove_missing_config(self):
        """Test removing non-existent config file."""
        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "unlink") as mock_unlink:
                daemon._remove_daemon_config()
                mock_unlink.assert_not_called()

    def test_remove_config_oserror(self):
        """Test handling OSError when removing config."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "unlink", side_effect=OSError):
                # Should not raise
                daemon._remove_daemon_config()


class TestSaveDaemonConfigEdgeCases:
    """Edge case tests for _save_daemon_config."""

    def test_save_config_oserror(self):
        """Test handling OSError when saving config."""
        with patch.object(daemon, "ensure_dirs"):
            with patch.object(Path, "write_text", side_effect=OSError):
                # Should not raise
                daemon._save_daemon_config("127.0.0.1", 8080)


class TestLoadDaemonConfigEdgeCases:
    """Edge case tests for _load_daemon_config."""

    def test_load_config_oserror(self):
        """Test handling OSError when loading config."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", side_effect=OSError):
                result = daemon._load_daemon_config()
                assert result is None


class TestRemovePidEdgeCases:
    """Edge case tests for _remove_pid."""

    def test_remove_pid_oserror(self):
        """Test handling OSError when removing PID."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "unlink", side_effect=OSError):
                # Should not raise
                daemon._remove_pid()


class TestEnsureDirsEdgeCases:
    """Edge case tests for ensure_dirs."""

    def testensure_dirs_existing(self):
        """Test ensuring dirs when they already exist."""
        with patch.object(Path, "mkdir") as mock_mkdir:
            with patch.object(Path, "exists", return_value=True):
                daemon.ensure_dirs()
                # ensure_dirs creates three directories: config, runtime, and
                # manager-logs.
                assert mock_mkdir.call_count == 3
                mock_mkdir.assert_called_with(parents=True, exist_ok=True)


class TestIsProcessRunning:
    """Regression tests for process liveness detection."""

    def test_terminated_process_with_open_handle_is_not_running(self):
        """A terminated process must not be reported as running.

        Windows keeps a terminated process's object — and therefore a valid
        PID and a valid ``OpenProcess`` handle — alive until every handle to it
        is closed. Testing only ``OpenProcess`` made a force-killed manager look
        alive indefinitely: its stale pidfile was never cleaned up, so
        ``ccconvert start`` refused to start a replacement and the user was
        stuck until they deleted the pidfile by hand.

        ``Popen`` keeps its handle open, which reproduces exactly that
        condition: the child has exited but its process object lingers.
        """
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        try:
            proc.wait()
            assert daemon._is_process_running(proc.pid) is False
        finally:
            proc.kill()

    def test_current_process_is_running(self):
        """Our own process must be reported as running."""
        assert daemon._is_process_running(os.getpid()) is True


class TestFindFreePortFrom:
    """Regression tests for the port-scan probe used when starting a manager."""

    def test_skips_port_held_on_wildcard_address(self):
        """A port held on 0.0.0.0 must not be reported free.

        Windows lets a bind to 127.0.0.1 succeed even while another socket
        holds 0.0.0.0 on the same port, and vice versa — where Linux returns
        EADDRINUSE, Windows allows both. Probing only loopback therefore
        reported the manager's own port as free while a manager was already
        listening, and the second manager started on the same port (then killed
        the first, since stop_daemon locates a stray process by port).
        """
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("0.0.0.0", 0))
            blocker.listen(1)
            occupied = blocker.getsockname()[1]

            # The coexistence that made the old probe wrong.
            assert daemon._can_bind(occupied, "127.0.0.1") is True

            assert daemon._find_free_port_from(occupied) != occupied
        finally:
            blocker.close()

    def test_skips_port_held_on_loopback(self):
        """A port held on 127.0.0.1 must not be reported free either."""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            occupied = blocker.getsockname()[1]

            assert daemon._find_free_port_from(occupied) != occupied
        finally:
            blocker.close()

    def test_returns_none_when_range_is_exhausted(self):
        """An exclude set covering the whole scan range yields None, not a crash."""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("0.0.0.0", 0))
            blocker.listen(1)
            occupied = blocker.getsockname()[1]

            assert daemon._find_free_port_from(occupied, exclude=set(range(occupied, occupied + 2000))) is None
        finally:
            blocker.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

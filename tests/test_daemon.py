"""
Tests for the daemon module.
"""

import json
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from cc_convert import daemon


class TestEnsureDirs:
    """Tests for _ensure_dirs function."""

    def test_creates_directories(self):
        """Test that directories are created."""
        with patch.object(Path, "mkdir") as mock_mkdir:
            with patch.object(Path, "exists", return_value=False):
                daemon.ensure_dirs()
                # ensure_dirs creates the config and runtime directories
                assert mock_mkdir.called
                mock_mkdir.assert_called_with(parents=True, exist_ok=True)


class TestReadPid:
    """Tests for _read_pid function."""

    def test_reads_valid_pid(self):
        """Test reading a valid PID file."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="12345"):
                pid = daemon._read_pid()
                assert pid == 12345

    def test_missing_file(self):
        """Test when PID file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            pid = daemon._read_pid()
            assert pid is None

    def test_invalid_content(self):
        """Test when PID file has invalid content."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="not_a_number"):
                pid = daemon._read_pid()
                assert pid is None

    def test_oserror(self):
        """Test when reading PID file raises OSError."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", side_effect=OSError):
                pid = daemon._read_pid()
                assert pid is None


class TestRemovePid:
    """Tests for _remove_pid function."""

    def test_removes_file(self):
        """Test removing PID file."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "unlink") as mock_unlink:
                daemon._remove_pid()
                mock_unlink.assert_called_once()

    def test_missing_file(self):
        """Test when PID file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "unlink") as mock_unlink:
                daemon._remove_pid()
                mock_unlink.assert_not_called()


class TestSaveDaemonConfig:
    """Tests for _save_daemon_config function."""

    def test_saves_config(self):
        """Test saving daemon configuration."""
        with patch.object(daemon, "ensure_dirs"):
            with patch.object(Path, "write_text") as mock_write:
                daemon._save_daemon_config(9000)
                mock_write.assert_called_once()
                # Check that JSON was written
                written = mock_write.call_args[0][0]
                config = json.loads(written)
                assert config["port"] == 9000
                assert "started_at" in config

    def test_oserror(self):
        """Test handling of OSError."""
        with patch.object(daemon, "ensure_dirs"):
            with patch.object(Path, "write_text", side_effect=OSError):
                # Should not raise
                daemon._save_daemon_config(9000)


class TestLoadDaemonConfig:
    """Tests for _load_daemon_config function."""

    def test_loads_valid_config(self):
        """Test loading a valid config file."""
        config_data = {"port": 9000}
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value=json.dumps(config_data)):
                config = daemon._load_daemon_config()
                assert config == config_data

    def test_missing_file(self):
        """Test when config file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            config = daemon._load_daemon_config()
            assert config is None

    def test_invalid_json(self):
        """Test when config file has invalid JSON."""
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value="invalid json"):
                config = daemon._load_daemon_config()
                assert config is None


class TestIsProcessRunning:
    """Tests for _is_process_running function.

    On Windows the check uses ``ctypes.OpenProcess`` (since ``os.kill(pid, 0)``
    is unreliable there); on POSIX it uses ``os.kill``. Tests patch the
    platform-appropriate call.
    """

    def test_running_process(self):
        """Test checking a running process."""
        if daemon.os.name == "nt":

            def _report_still_active(handle, exit_code_ptr):
                exit_code_ptr._obj.value = 259  # STILL_ACTIVE
                return 1

            with patch("ctypes.windll.kernel32.OpenProcess", return_value=0xABC):
                with patch(
                    "ctypes.windll.kernel32.GetExitCodeProcess",
                    side_effect=_report_still_active,
                ):
                    assert daemon._is_process_running(12345) is True
        else:
            with patch("os.kill", return_value=None):
                assert daemon._is_process_running(12345) is True

    @pytest.mark.skipif(daemon.os.name != "nt", reason="Windows-only semantics")
    def test_terminated_process_not_reported_as_running(self):
        """A terminated process must not be reported as running.

        ``OpenProcess`` also succeeds for a terminated process whose process
        object has not been reaped yet, so the exit code has to be consulted
        too. Relying on the handle alone made a force-killed manager look alive
        indefinitely, which left a stale pidfile behind and blocked
        ``ccconvert start`` from starting a replacement.
        """

        def _report_terminated(handle, exit_code_ptr):
            exit_code_ptr._obj.value = 1  # exited; anything but STILL_ACTIVE
            return 1

        with patch("ctypes.windll.kernel32.OpenProcess", return_value=0xABC):
            with patch(
                "ctypes.windll.kernel32.GetExitCodeProcess",
                side_effect=_report_terminated,
            ):
                assert daemon._is_process_running(12345) is False

    def test_not_running_process(self):
        """Test checking a non-running process."""
        if daemon.os.name == "nt":
            with patch("ctypes.windll.kernel32.OpenProcess", return_value=0):
                assert daemon._is_process_running(12345) is False
        else:
            with patch("os.kill", side_effect=OSError):
                assert daemon._is_process_running(12345) is False

    def test_process_lookup_error(self):
        """Test checking a process that raises ProcessLookupError (POSIX)."""
        if daemon.os.name == "nt":
            # Windows has no ProcessLookupError path — a NULL handle is the
            # equivalent of "process is gone".
            with patch("ctypes.windll.kernel32.OpenProcess", return_value=0):
                assert daemon._is_process_running(12345) is False
        else:
            with patch("os.kill", side_effect=ProcessLookupError):
                assert daemon._is_process_running(12345) is False


class TestIsPortInUse:
    """Tests for _is_port_in_use function."""

    def test_port_in_use(self):
        """Test when port is in use."""
        mock_socket = MagicMock()
        mock_socket.__enter__ = MagicMock(return_value=mock_socket)
        mock_socket.__exit__ = MagicMock(return_value=None)
        mock_socket.connect_ex.return_value = 0

        with patch("socket.socket", return_value=mock_socket):
            assert daemon._is_port_in_use(8080) is True

    def test_port_not_in_use(self):
        """Test when port is not in use."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 1

        with patch("socket.socket", return_value=mock_socket):
            assert daemon._is_port_in_use(8080) is False

    def test_exception(self):
        """Test when socket operation raises exception."""
        with patch("socket.socket", side_effect=Exception):
            assert daemon._is_port_in_use(8080) is False


class TestCleanupStalePidfile:
    """Tests for _cleanup_stale_pidfile function."""

    def test_removes_stale_pid(self):
        """Test removing stale PID file."""
        with patch.object(daemon, "_read_pid", return_value=12345):
            with patch.object(daemon, "_is_process_running", return_value=False):
                with patch.object(daemon, "_remove_pid") as mock_remove_pid:
                    with patch.object(daemon, "_remove_daemon_config") as mock_remove_config:
                        daemon._cleanup_stale_pidfile()
                        mock_remove_pid.assert_called_once()
                        mock_remove_config.assert_called_once()

    def test_keeps_valid_pid(self):
        """Test keeping valid PID file."""
        with patch.object(daemon, "_read_pid", return_value=12345):
            with patch.object(daemon, "_is_process_running", return_value=True):
                with patch.object(daemon, "_remove_pid") as mock_remove_pid:
                    daemon._cleanup_stale_pidfile()
                    mock_remove_pid.assert_not_called()

    def test_no_pid_file(self):
        """Test when there's no PID file."""
        with patch.object(daemon, "_read_pid", return_value=None):
            with patch.object(daemon, "_remove_pid") as mock_remove_pid:
                daemon._cleanup_stale_pidfile()
                mock_remove_pid.assert_not_called()


class TestGetStatus:
    """Tests for get_status function."""

    def test_running(self):
        """Test getting status when running."""
        config = {"host": "127.0.0.1", "port": 9000}
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch.object(daemon, "_is_process_running", return_value=True):
                    with patch.object(daemon, "_load_daemon_config", return_value=config):
                        running, pid, loaded_config = daemon.get_status()
                        assert running is True
                        assert pid == 12345
                        assert loaded_config == config

    def test_not_running(self):
        """Test getting status when not running."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                running, pid, config = daemon.get_status()
                assert running is False
                assert pid is None
                assert config is None


class TestStopDaemon:
    """Tests for stop_daemon function."""

    def test_stop_not_running(self):
        """Test stopping when not running."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                result = daemon.stop_daemon()
                assert result is True

    def test_stop_gracefully(self):
        """Test graceful stop."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                # First check: running, then loop checks (49 times False to break), final check not running
                with patch.object(daemon, "_is_process_running", side_effect=[True] + [False] * 50):
                    with patch("os.kill", return_value=None) as mock_kill:
                        with patch.object(daemon, "_remove_pid"):
                            with patch.object(daemon, "_remove_daemon_config"):
                                result = daemon.stop_daemon()
                                assert result is True
                                mock_kill.assert_called_with(12345, signal.SIGTERM)

    def test_stop_force(self):
        """Test force stop (taskkill on Windows, SIGKILL on POSIX)."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch.object(daemon, "_load_daemon_config", return_value=None):
                    with patch.object(daemon, "_is_process_running", side_effect=[True] + [False] * 50):
                        with patch("subprocess.run") as mock_run:
                            with patch("os.kill") as mock_kill:
                                with patch.object(daemon, "_remove_pid"):
                                    with patch.object(daemon, "_remove_daemon_config"):
                                        result = daemon.stop_daemon(force=True)
                                        assert result is True
                                        if sys.platform == "win32":
                                            args = mock_run.call_args[0][0]
                                            assert args[:2] == ["taskkill", "/F"]
                                            assert str(12345) in args
                                        else:
                                            mock_kill.assert_called_once_with(12345, signal.SIGKILL)

    def test_stop_process_lookup_error(self):
        """Test stop when process lookup fails."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch("os.kill", side_effect=ProcessLookupError):
                    with patch.object(daemon, "_remove_pid"):
                        with patch.object(daemon, "_remove_daemon_config"):
                            result = daemon.stop_daemon()
                            assert result is True


class TestRestartDaemon:
    """Tests for restart_daemon function."""

    def test_restart(self):
        """Test restart daemon."""
        with patch.object(daemon, "stop_daemon", return_value=True):
            with patch("time.sleep"):
                with patch.object(daemon, "start_daemon", return_value=True) as mock_start:
                    result = daemon.restart_daemon()
                    assert result is True
                    mock_start.assert_called_once_with(None)

    def test_restart_forwards_the_port_override(self):
        """`ccconvert restart --port N` must reach start_daemon."""
        with patch.object(daemon, "stop_daemon", return_value=True):
            with patch("time.sleep"):
                with patch.object(daemon, "start_daemon", return_value=True) as mock_start:
                    daemon.restart_daemon(port=18888)
                    mock_start.assert_called_once_with(18888)


class TestShowLogs:
    """Tests for show_logs function."""

    def test_no_log_file(self):
        """Test when log file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            result = daemon.show_logs()
            assert result is False

    def test_show_last_lines(self):
        """Test showing last N lines."""
        log_content = "line1\nline2\nline3\nline4\nline5\n"
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=log_content)):
                with patch("builtins.print"):
                    result = daemon.show_logs(lines=2)
                    assert result is True

    def test_show_logs_exception(self):
        """Test handling exception when reading logs."""
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("Cannot read")):
                result = daemon.show_logs()
                assert result is False


class TestStartDaemon:
    """Tests for start_daemon function."""

    def test_already_running(self):
        """Test starting when already running."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=12345):
                with patch.object(daemon, "_load_daemon_config", return_value={"port": 8080}):
                    result = daemon.start_daemon()
                    assert result is False

    def test_port_in_use_no_alternative(self):
        """Test when port is in use and no free alternative is found."""
        with patch.object(daemon, "_cleanup_stale_pidfile"):
            with patch.object(daemon, "_read_pid", return_value=None):
                with patch.object(daemon, "_is_port_in_use", return_value=True):
                    with patch.object(daemon, "_find_free_port_from", return_value=None):
                        with patch("cc_convert.profile_store.load_profile_store"):
                            with patch("cc_convert.profile_store.save_profile_store"):
                                result = daemon.start_daemon(port=8080)
                                assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Tests for the daemon_runner module (CC Convert manager bootstrap).
"""

import os
import sys
from unittest.mock import MagicMock, patch

from cc_convert import daemon_runner


class TestLogMessage:
    """Tests for log_message function."""

    def test_log_message(self):
        """Test logging a message."""
        with patch("builtins.print") as mock_print:
            with patch.object(sys.stderr, "flush"):
                daemon_runner.log_message("Test message")
                mock_print.assert_called_once()
                # Check that the message contains timestamp and text
                call_args = mock_print.call_args
                assert "Test message" in call_args[0][0]


class TestManagerPid:
    """Tests for _write_manager_pid / _remove_manager_pid."""

    def test_write_manager_pid(self, tmp_path, monkeypatch):
        """Test writing the manager PID file."""
        monkeypatch.setattr(
            "cc_convert.daemon_runner.get_runtime_dir", lambda: tmp_path
        )
        daemon_runner._write_manager_pid()
        pid_file = tmp_path / "manager.pid"
        assert pid_file.exists()
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_remove_manager_pid(self, tmp_path, monkeypatch):
        """Test removing the manager PID file."""
        monkeypatch.setattr(
            "cc_convert.daemon_runner.get_runtime_dir", lambda: tmp_path
        )
        pid_file = tmp_path / "manager.pid"
        pid_file.write_text("12345")
        daemon_runner._remove_manager_pid()
        assert not pid_file.exists()

    def test_remove_manager_pid_missing(self, tmp_path, monkeypatch):
        """Test removing a non-existent PID file is a no-op."""
        monkeypatch.setattr(
            "cc_convert.daemon_runner.get_runtime_dir", lambda: tmp_path
        )
        daemon_runner._remove_manager_pid()  # should not raise


class TestRunManager:
    """Tests for run_manager bootstrap wiring (mocked, no real uvicorn)."""

    def test_run_manager_wires_supervisor(self):
        """Verify manager app is built with store + supervisor and uvicorn runs on store port."""
        store = MagicMock()
        store.manager.port = 18400
        store.manager.host = "127.0.0.1"
        store.revision = 3
        store.instances = []
        mock_app = MagicMock()

        with patch("cc_convert.daemon_runner.load_profile_store", return_value=store):
            with patch("cc_convert.daemon_runner.InstanceSupervisor") as mock_sup:
                with patch("cc_convert.daemon_runner.create_manager_app", return_value=mock_app) as mock_create_app:
                    with patch("cc_convert.daemon_runner.uvicorn.run") as mock_uvicorn:
                        with patch.object(daemon_runner, "_write_manager_pid"):
                            with patch.object(daemon_runner, "_remove_manager_pid"):
                                daemon_runner.run_manager()

                                mock_create_app.assert_called_once_with(store, mock_sup.return_value)
                                mock_uvicorn.assert_called_once()
                                kwargs = mock_uvicorn.call_args.kwargs
                                assert kwargs["host"] == "127.0.0.1"
                                assert kwargs["port"] == 18400

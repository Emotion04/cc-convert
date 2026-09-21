# SPDX-License-Identifier: Apache-2.0
"""
Tests for logging functionality.
"""

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_convert.main import get_default_log_dir, setup_logging
from cc_convert.runtime_paths import get_runtime_dir
from tests.logging_support import temp_log_dir


class TestGetDefaultLogDir:
    """Tests for get_default_log_dir.

    The log directory is derived from the runtime directory, so these assert
    that contract rather than re-implementing the per-platform rules (which
    live in ``runtime_paths`` and key off ``os.name``, not ``sys.platform``).
    """

    def test_log_dir_is_under_runtime_dir(self):
        """Logs live under the runtime directory."""
        assert get_default_log_dir() == str(get_runtime_dir() / "logs")

    def test_log_dir_follows_runtime_dir_override(self, monkeypatch, tmp_path):
        """CCCONVERT_RUNTIME_DIR must relocate logs too.

        Regression: the log directory used to be computed independently from
        LOCALAPPDATA under the hyphenated name "cc-convert", honouring no
        override at all. A relocated runtime therefore still wrote its logs to
        the real user directory, and the test suite leaked log output outside
        its sandbox.
        """
        monkeypatch.setenv("CCCONVERT_RUNTIME_DIR", str(tmp_path))

        assert get_default_log_dir() == str(tmp_path / "logs")

    def test_log_dir_component_is_named_logs(self):
        """The last path component is not platform dependent."""
        assert Path(get_default_log_dir()).name == "logs"


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_creates_directory(self):
        """Test that setup_logging creates log directory."""
        with temp_log_dir() as tmpdir:
            log_dir = os.path.join(tmpdir, "logs", "subdir")
            setup_logging("INFO", log_dir)

            assert os.path.exists(log_dir)
            assert os.path.isdir(log_dir)

    def test_setup_logging_creates_log_file(self):
        """Test that setup_logging creates log file."""
        with temp_log_dir() as tmpdir:
            setup_logging("INFO", tmpdir)

            log_file = os.path.join(tmpdir, "server.log")
            assert os.path.exists(log_file)

    def test_setup_logging_handlers(self):
        """Test that setup_logging configures handlers correctly."""
        with temp_log_dir() as tmpdir:
            setup_logging("DEBUG", tmpdir)

            root_logger = logging.getLogger()

            # Should have 2 handlers: console and file
            assert len(root_logger.handlers) == 2

            # Check handler types
            handler_types = [type(h).__name__ for h in root_logger.handlers]
            assert "StreamHandler" in handler_types
            assert "TimedRotatingFileHandler" in handler_types

    def test_setup_logging_log_level(self):
        """Test that setup_logging sets correct log level."""
        with temp_log_dir() as tmpdir:
            setup_logging("WARNING", tmpdir)

            root_logger = logging.getLogger()
            assert root_logger.level == logging.WARNING

    def test_setup_logging_file_rotation_settings(self):
        """Test that file handler has correct rotation settings."""
        with temp_log_dir() as tmpdir:
            setup_logging("INFO", tmpdir)

            root_logger = logging.getLogger()
            file_handler = None
            for handler in root_logger.handlers:
                if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
                    file_handler = handler
                    break

            assert file_handler is not None
            assert file_handler.when.upper() == "MIDNIGHT"
            assert file_handler.backupCount == 1  # Keep 2 days of logs

    def test_setup_logging_log_format(self):
        """Test that log format is correct."""
        with temp_log_dir() as tmpdir:
            setup_logging("INFO", tmpdir)

            # Get file handler
            root_logger = logging.getLogger()
            file_handler = None
            for handler in root_logger.handlers:
                if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
                    file_handler = handler
                    break

            assert file_handler is not None
            formatter = file_handler.formatter
            assert "%(asctime)s" in formatter._fmt
            assert "%(name)s" in formatter._fmt
            assert "%(levelname)s" in formatter._fmt
            assert "%(message)s" in formatter._fmt

    def test_setup_logging_clears_existing_handlers(self):
        """Test that setup_logging clears existing handlers."""
        # Add a dummy handler first
        root_logger = logging.getLogger()
        dummy_handler = logging.NullHandler()
        root_logger.addHandler(dummy_handler)
        initial_count = len(root_logger.handlers)

        with temp_log_dir() as tmpdir:
            setup_logging("INFO", tmpdir)

            # Should have exactly 2 handlers after setup
            assert len(root_logger.handlers) == 2

    def test_setup_logging_with_none_log_dir(self):
        """Test setup_logging with None log_dir uses default."""
        with patch("cc_convert.main.get_default_log_dir") as mock_get_dir:
            with temp_log_dir() as tmpdir:
                mock_get_dir.return_value = tmpdir
                setup_logging("INFO", None)

                mock_get_dir.assert_called_once()
                assert os.path.exists(os.path.join(tmpdir, "server.log"))

    def test_setup_logging_expands_user_directory(self):
        """Test that ~ in log_dir is expanded."""
        with temp_log_dir() as tmpdir:
            # Create a path with ~
            home = os.path.expanduser("~")
            relative_path = tmpdir.replace(home, "~")

            setup_logging("INFO", relative_path)

            log_file = os.path.join(tmpdir, "server.log")
            assert os.path.exists(log_file)


class TestLoggingIntegration:
    """Integration tests for logging functionality."""

    def test_log_messages_written_to_file(self):
        """Test that log messages are written to file."""
        with temp_log_dir() as tmpdir:
            setup_logging("INFO", tmpdir)

            # Get a logger and write messages
            logger = logging.getLogger("test_logger")
            logger.info("Test info message")
            logger.warning("Test warning message")
            logger.error("Test error message")

            # Read log file
            log_file = os.path.join(tmpdir, "server.log")
            with open(log_file, "r") as f:
                content = f.read()

            assert "Test info message" in content
            assert "Test warning message" in content
            assert "Test error message" in content

    def test_log_level_filtering(self):
        """Test that log level filters messages correctly."""
        with temp_log_dir() as tmpdir:
            setup_logging("WARNING", tmpdir)

            logger = logging.getLogger("test_filter")
            logger.debug("Debug message")
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")

            log_file = os.path.join(tmpdir, "server.log")
            with open(log_file, "r") as f:
                content = f.read()

            assert "Debug message" not in content
            assert "Info message" not in content
            assert "Warning message" in content
            assert "Error message" in content

    def test_log_message_format(self):
        """Test that log messages have correct format."""
        with temp_log_dir() as tmpdir:
            setup_logging("INFO", tmpdir)

            logger = logging.getLogger("test_format")
            logger.info("Format test")

            log_file = os.path.join(tmpdir, "server.log")
            with open(log_file, "r") as f:
                lines = f.readlines()

            # Find the line with our message
            test_line = None
            for line in lines:
                if "Format test" in line:
                    test_line = line
                    break

            assert test_line is not None
            # Check format: timestamp - name - level - message
            parts = test_line.split(" - ")
            assert len(parts) >= 4
            assert "test_format" in parts[1]
            assert "INFO" in parts[2]
            assert "Format test" in parts[3]


class TestLoggingConfiguration:
    """Tests for logging configuration."""

    def test_config_default_log_level(self, monkeypatch):
        """Test default log level in config."""
        from cc_convert.config import Settings

        monkeypatch.setattr(
            "cc_convert.config.load_config_from_file",
            lambda: {},
        )
        monkeypatch.delenv("CC Convert_LOG_LEVEL", raising=False)
        settings = Settings()
        assert settings.log_level.upper() == "INFO"

    def test_config_log_level_override(self):
        """Test log level can be overridden."""
        from cc_convert.config import Settings

        settings = Settings(log_level="WARNING")
        assert settings.log_level.upper() == "WARNING"

    def test_config_default_log_dir(self):
        """Test default log dir in config."""
        from cc_convert.config import Settings

        settings = Settings()
        assert settings.log_dir == ""  # Empty means use platform default

    def test_config_custom_log_dir(self):
        """Test custom log dir in config."""
        from cc_convert.config import Settings

        settings = Settings(log_dir="/custom/log/path")
        assert settings.log_dir == "/custom/log/path"

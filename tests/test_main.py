"""
Tests for the main module.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import HTTPException

from cc_convert.main import create_app, run_foreground, main
from cc_convert.config import Settings


class TestCreateApp:
    """Tests for create_app function."""

    def test_create_app_basic(self):
        """Test creating app with default settings."""
        settings = Settings(
            openai_api_key="test-key",
        )

        app = create_app(settings)

        assert app.title == "CC Convert Proxy Worker"
        assert app.version == "0.8.0"
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"

    def test_create_app_stores_settings(self):
        """Test that settings are stored in app state."""
        settings = Settings(
            openai_api_key="test-key",
            custom_field="value",  # type: ignore[call-arg]
        )

        app = create_app(settings)

        assert app.state.settings is settings

    def test_create_app_with_api_key_auth(self):
        """Test creating app with API key authentication (workers are localhost-only, no inbound auth)."""
        settings = Settings(
            openai_api_key="test-key",
            api_key="server-api-key",
        )

        app = create_app(settings)
        client = TestClient(app)

        # Proxy workers do not enforce inbound auth (localhost-only).
        # Requests without auth should pass through to validation.
        response = client.post("/v1/messages", json={})
        assert response.status_code == 400  # validation error, not auth error

        # Auth header is still accepted but ignored at worker level
        response = client.post(
            "/v1/messages",
            json={},
            headers={"Authorization": "Bearer server-api-key"},
        )
        assert response.status_code == 400  # validation error

    def test_create_app_auth_accepts_x_api_key(self):
        """Test that x-api-key header is accepted but not enforced on workers."""
        settings = Settings(
            openai_api_key="test-key",
            api_key="server-api-key",
        )

        app = create_app(settings)
        client = TestClient(app)

        # Workers don't enforce inbound auth — requests pass to validation layer.
        response = client.post(
            "/v1/messages",
            json={},
            headers={"x-api-key": "server-api-key"},
        )
        assert response.status_code == 400

        response = client.post(
            "/v1/messages",
            json={},
            headers={"x-api-key": "wrong-key"},
        )
        assert response.status_code == 400  # also passes through to validation

    def test_create_app_auth_skips_docs(self):
        """Test that auth middleware skips docs endpoints."""
        settings = Settings(
            openai_api_key="test-key",
            api_key="server-api-key",
        )

        app = create_app(settings)
        client = TestClient(app)

        # Docs should be accessible without auth
        response = client.get("/docs")
        assert response.status_code == 200

        response = client.get("/redoc")
        assert response.status_code == 200

        response = client.get("/health")
        assert response.status_code == 200

    def test_create_app_auth_skips_openapi(self):
        """Test that auth middleware skips openapi.json."""
        settings = Settings(
            openai_api_key="test-key",
            api_key="server-api-key",
        )

        app = create_app(settings)
        client = TestClient(app)

        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_http_exception_handler(self):
        """Test HTTP exception handler."""
        settings = Settings(openai_api_key="test-key")
        app = create_app(settings)

        @app.get("/test-http-error")
        async def test_http_error():
            raise HTTPException(status_code=400, detail="Test HTTP error")

        client = TestClient(app)
        response = client.get("/test-http-error")

        assert response.status_code == 400
        data = response.json()
        assert data["type"] == "error"
        assert "error" in data

    def test_http_exception_handler_with_anthropic_error(self):
        """Test HTTP exception handler with Anthropic error format."""
        from cc_convert.protocol import AnthropicError, AnthropicErrorResponse

        settings = Settings(openai_api_key="test-key")
        app = create_app(settings)

        @app.get("/test-error")
        async def test_error():
            error_response = AnthropicErrorResponse(
                error=AnthropicError(type="test_error", message="Test error")
            )
            raise HTTPException(status_code=400, detail=error_response.model_dump())

        client = TestClient(app)
        response = client.get("/test-error")

        assert response.status_code == 400
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "test_error"

    def test_general_exception_handler(self):
        """Test general exception handler."""
        import logging
        import asyncio

        settings = Settings(openai_api_key="test-key")
        app = create_app(settings)

        # Test the handler directly
        mock_request = MagicMock()
        test_exc = ValueError("Test exception")

        # Find the general exception handler
        general_handler = app.exception_handlers.get(Exception)
        assert general_handler is not None

        # Call the handler
        async def run_test():
            return await general_handler(mock_request, test_exc)

        logging.disable(logging.ERROR)
        try:
            response = asyncio.run(run_test())

            assert response.status_code == 500
            import json
            data = json.loads(response.body.decode())
            assert data["type"] == "error"
            assert data["error"]["type"] == "internal_error"
        finally:
            logging.disable(logging.NOTSET)


class TestRunForeground:
    """Tests for run_foreground function."""

    def test_missing_api_key(self):
        """Test that run_foreground exits when API key is missing."""
        settings = Settings(openai_api_key=None)

        with pytest.raises(SystemExit) as exc_info:
            run_foreground(settings)

        assert exc_info.value.code == 1

    @patch("cc_convert.main.uvicorn.run")
    @patch("cc_convert.main.create_app")
    def test_successful_run(self, mock_create_app, mock_uvicorn_run):
        """Test successful foreground run."""
        settings = Settings(
            openai_api_key="test-key",
            host="127.0.0.1",
            port=9000,
            log_level="INFO",
        )

        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        run_foreground(settings)

        mock_create_app.assert_called_once_with(settings)
        mock_uvicorn_run.assert_called_once()
        call_kwargs = mock_uvicorn_run.call_args.kwargs
        assert call_kwargs["host"] == "127.0.0.1"
        assert call_kwargs["port"] == 9000
        assert call_kwargs["log_level"] == "info"


class TestMain:
    """Tests for main function."""

    @patch("cc_convert.main.run_foreground")
    @patch("cc_convert.main.get_settings")
    def test_no_command_shows_help(self, mock_get_settings, mock_run_foreground):
        """Test that no command shows help instead of running foreground."""
        settings = Settings(openai_api_key="test-key")
        mock_get_settings.return_value = settings

        with patch("sys.argv", ["ccconvert"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_run_foreground.assert_not_called()  # No longer runs foreground by default

    @patch("cc_convert.daemon.start_daemon")
    @patch("cc_convert.profile_store.load_profile_store")
    def test_start_command(self, mock_load_store, mock_start_daemon):
        """Test start command."""
        from cc_convert.profile_store import InstanceProfile, ManagerConfig, ProfileStore
        store = ProfileStore(
            manager=ManagerConfig(port=8080, host="127.0.0.1"),
            instances=[InstanceProfile(name="test", port=18001, upstream_base_url="https://api.openai.com/v1", target_model="gpt-4", upstream_api_key="sk-test")],
        )
        mock_load_store.return_value = store
        mock_start_daemon.return_value = True

        with patch("sys.argv", ["ccconvert", "start"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_start_daemon.assert_called_once()

    @patch("cc_convert.daemon.stop_daemon")
    def test_stop_command(self, mock_stop_daemon):
        """Test stop command."""
        mock_stop_daemon.return_value = True

        with patch("sys.argv", ["ccconvert", "stop"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_stop_daemon.assert_called_once_with(force=False)

    @patch("cc_convert.daemon.stop_daemon")
    def test_stop_command_force(self, mock_stop_daemon):
        """Test stop command with force flag."""
        mock_stop_daemon.return_value = True

        with patch("sys.argv", ["ccconvert", "stop", "-f"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_stop_daemon.assert_called_once_with(force=True)

    @patch("cc_convert.daemon.restart_daemon")
    @patch("cc_convert.profile_store.load_profile_store")
    def test_restart_command(self, mock_load_store, mock_restart_daemon):
        """Test restart command."""
        from cc_convert.profile_store import ProfileStore, ManagerConfig
        store = ProfileStore(manager=ManagerConfig(port=8080))
        mock_load_store.return_value = store
        mock_restart_daemon.return_value = True

        with patch("sys.argv", ["ccconvert", "restart"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_restart_daemon.assert_called_once()

    @patch("cc_convert.daemon.get_status")
    def test_status_command_running(self, mock_get_status):
        """Test status command when running."""
        mock_get_status.return_value = (True, 12345, {"host": "0.0.0.0", "port": 8080})

        with patch("sys.argv", ["ccconvert", "status"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    @patch("cc_convert.daemon.get_status")
    def test_status_command_not_running(self, mock_get_status):
        """Test status command when not running."""
        mock_get_status.return_value = (False, None, None)

        with patch("sys.argv", ["ccconvert", "status"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    @patch("cc_convert.daemon.show_logs")
    def test_logs_command(self, mock_show_logs):
        """Test logs command."""
        mock_show_logs.return_value = True

        with patch("sys.argv", ["ccconvert", "logs"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_show_logs.assert_called_once_with(follow=False, lines=50)

    @patch("cc_convert.daemon.show_logs")
    def test_logs_command_follow(self, mock_show_logs):
        """Test logs command with follow flag."""
        mock_show_logs.return_value = True

        with patch("sys.argv", ["ccconvert", "logs", "-f"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_show_logs.assert_called_once_with(follow=True, lines=50)

    @patch("cc_convert.daemon.show_logs")
    def test_logs_command_lines(self, mock_show_logs):
        """Test logs command with custom lines."""
        mock_show_logs.return_value = True

        with patch("sys.argv", ["ccconvert", "logs", "-n", "100"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_show_logs.assert_called_once_with(follow=False, lines=100)

    @patch("cc_convert.daemon.start_daemon")
    @patch("cc_convert.profile_store.load_profile_store")
    def test_start_command_failure(self, mock_load_store, mock_start_daemon):
        """Test start command failure."""
        from cc_convert.profile_store import ProfileStore, ManagerConfig
        store = ProfileStore(manager=ManagerConfig(port=8080))
        mock_load_store.return_value = store
        mock_start_daemon.return_value = False

        with patch("sys.argv", ["ccconvert", "start"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_version_flag(self):
        """Test --version flag."""
        with patch("sys.argv", ["ccconvert", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

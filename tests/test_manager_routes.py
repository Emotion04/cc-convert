"""
Tests for the manager dashboard routes using FastAPI TestClient
with mocked ProfileStore and InstanceSupervisor.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from cc_convert.main import create_manager_app
from cc_convert.profile_store import (
    InstanceProfile,
    ManagerConfig,
    ProfileStore,
)


@pytest.fixture
def mock_store():
    """Create a minimal ProfileStore with no instances for testing."""
    return ProfileStore(
        manager=ManagerConfig(port=9999, host="127.0.0.1"),
        instances=[],
    )


@pytest.fixture
def mock_supervisor():
    """Create a mock InstanceSupervisor with AsyncMock for async methods."""
    sup = MagicMock()
    sup.start_instance = AsyncMock(return_value=True)
    sup.stop_instance = AsyncMock(return_value=True)
    sup.restart_instance = AsyncMock(return_value=True)
    sup.get_instance_status = AsyncMock(return_value={"status": "stopped"})
    sup.get_instance_logs = AsyncMock(return_value=[])
    sup.update_instance = AsyncMock(return_value={"success": True})
    sup.get_running_instances = MagicMock(return_value=[])
    return sup


@pytest.fixture
def app(mock_store, mock_supervisor):
    """Create the manager app with mocked store and supervisor."""
    return create_manager_app(mock_store, mock_supervisor)


@pytest.fixture
def client(app):
    """Create a TestClient for the manager app.

    Sets Host to localhost to pass the host-validation middleware.
    """
    return TestClient(app, headers={"Host": "localhost"})


@pytest.fixture
def csrf_headers(app):
    """Headers with CSRF token for mutating requests.

    Reads the token from app.state so tests always use the correct value.
    """
    return {"X-CCCONVERT-CSRF": app.state.csrf_token}


# ---------------------------------------------------------------------------
# List instances
# ---------------------------------------------------------------------------


class TestListInstances:
    def test_list_instances_empty(self, client, mock_store):
        """GET /api/instances returns [] when the store has no instances."""
        mock_store.instances.clear()
        response = client.get("/api/instances")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_instances_with_data(self, client, mock_store, mock_supervisor):
        """GET /api/instances returns serialised instances with status."""
        inst = InstanceProfile(
            name="alpha",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-alpha-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={"status": "running", "pid": 111, "started_at": 100.0, "uptime": 10.0}
        )

        response = client.get("/api/instances")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == inst.id
        assert data[0]["name"] == "alpha"
        assert data[0]["status"] == "running"
        assert data[0]["instance_pid"] == 111

        # Key is never exposed; only metadata
        assert "upstream_api_key" not in data[0]
        assert data[0]["key_configured"] is True
        assert data[0]["key_last4"] == "-key"


# ---------------------------------------------------------------------------
# Create instance
# ---------------------------------------------------------------------------


class TestCreateInstance:
    def test_create_instance(self, client, mock_store, mock_supervisor, csrf_headers):
        """POST /api/instances with valid body creates an instance."""
        with (
            patch("cc_convert.web.routes.suggest_port", return_value=18500),
            patch("cc_convert.web.routes.save_profile_store"),
        ):
            response = client.post(
                "/api/instances",
                json={
                    "name": "test-instance",
                    "upstream_base_url": "https://api.openai.com/v1",
                    "upstream_api_key": "sk-test-key-12345",
                    "target_model": "gpt-4",
                },
                headers=csrf_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["name"] == "test-instance"
        assert data["port"] == 18500
        assert data["key_configured"] is True
        assert data["key_last4"] == "2345"
        assert "upstream_api_key" not in data

    def test_create_instance_validation_fails(self, client, csrf_headers):
        """POST /api/instances with invalid body returns 422."""
        # Empty object triggers "upstream_api_key is required"
        response = client.post(
            "/api/instances",
            json={},
            headers=csrf_headers,
        )
        assert response.status_code == 422

    def test_create_instance_missing_name_fails(self, client, csrf_headers):
        """POST /api/instances with empty name returns 422."""
        response = client.post(
            "/api/instances",
            json={
                "name": "",
                "upstream_base_url": "https://api.openai.com/v1",
                "upstream_api_key": "sk-test",
                "target_model": "gpt-4",
            },
            headers=csrf_headers,
        )
        assert response.status_code == 422

    def test_create_instance_with_explicit_port(self, client, mock_supervisor, csrf_headers):
        """POST /api/instances with a port uses it instead of suggest_port."""
        with patch("cc_convert.web.routes.save_profile_store"):
            response = client.post(
                "/api/instances",
                json={
                    "name": "fixed-port-instance",
                    "port": 19000,
                    "upstream_base_url": "https://api.openai.com/v1",
                    "upstream_api_key": "sk-test-key",
                    "target_model": "gpt-4",
                    "auto_start": False,
                },
                headers=csrf_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["port"] == 19000


# ---------------------------------------------------------------------------
# Get instance
# ---------------------------------------------------------------------------


class TestGetInstance:
    def test_get_instance(self, client, mock_store, mock_supervisor):
        """GET /api/instances/{id} returns instance with masked key and status."""
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-real-key-99999",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={
                "status": "running",
                "pid": 12345,
                "started_at": 1000.0,
                "uptime": 500.0,
            }
        )

        response = client.get(f"/api/instances/{inst.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == inst.id
        assert data["name"] == "test-instance"
        assert data["port"] == 18001
        assert data["key_configured"] is True
        assert data["key_last4"] == "9999"
        assert "upstream_api_key" not in data
        assert data["status"] == "running"
        assert data["instance_pid"] == 12345
        assert data["instance_started_at"] == 1000.0
        assert data["instance_uptime"] == 500.0

    def test_get_instance_not_found(self, client, mock_supervisor):
        """GET /api/instances/{id} with unknown id returns 404."""
        response = client.get("/api/instances/nonexistent-id")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update instance
# ---------------------------------------------------------------------------


class TestUpdateInstance:
    def test_update_instance_name(self, client, mock_store, mock_supervisor, csrf_headers):
        """PATCH /api/instances/{id} name updates the instance."""
        inst = InstanceProfile(
            name="old-name",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)

        async def _update_side_effect(instance_id, data):
            for inst in mock_store.instances:
                if inst.id == instance_id:
                    for k, v in data.items():
                        setattr(inst, k, v)
                    return {"success": True}
            return {"success": False, "error": "not found"}

        mock_supervisor.update_instance = AsyncMock(side_effect=_update_side_effect)
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={"status": "stopped"}
        )

        response = client.patch(
            f"/api/instances/{inst.id}",
            json={"name": "new-name"},
            headers=csrf_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-name"

        # Verify the correct data was forwarded to the supervisor
        mock_supervisor.update_instance.assert_called_once_with(
            inst.id, {"name": "new-name"}
        )

    def test_update_instance_not_found(self, client, csrf_headers):
        """PATCH /api/instances/{id} with unknown id returns 404."""
        response = client.patch(
            "/api/instances/nonexistent",
            json={"name": "new"},
            headers=csrf_headers,
        )
        assert response.status_code == 404

    def test_update_instance_validation_fails(self, client, mock_store, csrf_headers):
        """PATCH /api/instances/{id} with invalid data returns 422."""
        inst = InstanceProfile(
            name="test",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)

        # Empty name should fail validation
        response = client.patch(
            f"/api/instances/{inst.id}",
            json={"name": ""},
            headers=csrf_headers,
        )
        assert response.status_code == 422

    def test_reject_masked_key_patch(self, client, mock_store, mock_supervisor, csrf_headers):
        """PATCH with masked upstream_api_key (*** prefix) is rejected.

        The route handler detects the masked sentinel and pops it from the
        patch data so the real key is never overwritten.
        """
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-real-secret-99999",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)

        mock_supervisor.update_instance = AsyncMock(return_value={"success": True})
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={"status": "stopped"}
        )

        response = client.patch(
            f"/api/instances/{inst.id}",
            json={"upstream_api_key": "***9999"},
            headers=csrf_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Original key must be preserved
        assert data["key_last4"] == "9999"

        # Because data was emptied by the masked-key rejection, the route
        # returns early (get_instance_status) and does NOT call update_instance.
        mock_supervisor.update_instance.assert_not_called()

    def test_accept_valid_key_patch(self, client, mock_store, mock_supervisor, csrf_headers):
        """PATCH with a real (non-masked) upstream_api_key updates the key."""
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-old-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)

        async def _update_side_effect(instance_id, data):
            for inst in mock_store.instances:
                if inst.id == instance_id:
                    for k, v in data.items():
                        setattr(inst, k, v)
                    return {"success": True}
            return {"success": False, "error": "not found"}

        mock_supervisor.update_instance = AsyncMock(side_effect=_update_side_effect)
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={"status": "stopped"}
        )
        with patch("cc_convert.web.routes.save_profile_store"):
            response = client.patch(
                f"/api/instances/{inst.id}",
                json={"upstream_api_key": "sk-new-key-77777"},
                headers=csrf_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["key_last4"] == "7777"


# ---------------------------------------------------------------------------
# Delete instance
# ---------------------------------------------------------------------------


class TestDeleteInstance:
    def test_delete_stopped_instance(self, client, mock_store, mock_supervisor, csrf_headers):
        """DELETE /api/instances/{id} for a stopped instance succeeds."""
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={"status": "stopped"}
        )

        with patch("cc_convert.web.routes.save_profile_store"):
            response = client.delete(f"/api/instances/{inst.id}", headers=csrf_headers)

        assert response.status_code == 200
        assert response.json() == {"success": True}
        # Verify instance was removed from the store
        assert len(mock_store.instances) == 0

    def test_delete_running_instance_rejected(self, client, mock_store, mock_supervisor, csrf_headers):
        """DELETE /api/instances/{id} for a running instance returns 409."""
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.get_instance_status = AsyncMock(
            return_value={"status": "running"}
        )

        response = client.delete(f"/api/instances/{inst.id}", headers=csrf_headers)

        assert response.status_code == 409
        assert "running" in response.json()["detail"].lower()
        # Instance must still be in the store
        assert len(mock_store.instances) == 1

    def test_delete_instance_not_found(self, client, csrf_headers):
        """DELETE /api/instances/{id} with unknown id returns 404."""
        response = client.delete(
            "/api/instances/nonexistent", headers=csrf_headers
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Port suggestion
# ---------------------------------------------------------------------------


class TestSuggestPort:
    def test_suggest_port_endpoint(self, client, csrf_headers):
        """POST /api/ports/suggest returns a suggested port."""
        with patch("cc_convert.web.routes.suggest_port", return_value=19999):
            response = client.post("/api/ports/suggest", headers=csrf_headers)

        assert response.status_code == 200
        assert response.json() == {"port": 19999}


# ---------------------------------------------------------------------------
# Manager status
# ---------------------------------------------------------------------------


class TestManagerStatus:
    def test_manager_status(self, client, mock_store, mock_supervisor):
        """GET /api/manager returns version, pid, port, and instance counts."""
        mock_store.manager.port = 9999
        mock_supervisor.get_running_instances = MagicMock(
            return_value=["inst-1", "inst-2"]
        )

        response = client.get("/api/manager")

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "0.8.0"
        assert isinstance(data["pid"], int)
        assert data["port"] == 9999
        assert data["instance_count"] == len(mock_store.instances)
        assert data["running_count"] == 2

    def test_manager_status_with_instances(self, client, mock_store, mock_supervisor):
        """GET /api/manager reflects non-zero instance counts."""
        mock_store.instances.append(
            InstanceProfile(
                name="a", port=18001,
                upstream_base_url="https://api.openai.com/v1",
                upstream_api_key="sk-a", target_model="gpt-4",
            )
        )
        mock_store.instances.append(
            InstanceProfile(
                name="b", port=18002,
                upstream_base_url="https://api.openai.com/v1",
                upstream_api_key="sk-b", target_model="gpt-4",
            )
        )
        mock_supervisor.get_running_instances = MagicMock(return_value=["inst-b-id"])

        response = client.get("/api/manager")

        assert response.status_code == 200
        data = response.json()
        assert data["instance_count"] == 2
        assert data["running_count"] == 1


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------


class TestCSRFProtection:
    def test_csrf_token_required(self, client, mock_store):
        """POST /api/instances without X-CCCONVERT-CSRF header returns 403."""
        response = client.post(
            "/api/instances",
            json={"name": "test", "upstream_api_key": "sk-test",
                  "upstream_base_url": "https://api.openai.com/v1",
                  "target_model": "gpt-4"},
        )
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_csrf_token_wrong_value(self, client, mock_store):
        """POST /api/instances with wrong CSRF token returns 403."""
        response = client.post(
            "/api/instances",
            json={"name": "test"},
            headers={"X-CCCONVERT-CSRF": "wrong-token"},
        )
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_get_does_not_require_csrf(self, client):
        """GET endpoints are not protected by CSRF middleware."""
        response = client.get("/api/instances")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Instance lifecycle (start / stop / restart)
# ---------------------------------------------------------------------------


class TestInstanceLifecycle:
    def test_start_stop_restart(self, client, mock_store, mock_supervisor, csrf_headers):
        """POST start/stop/restart lifecycle endpoints return success."""
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)

        # Start
        response = client.post(
            f"/api/instances/{inst.id}/start", headers=csrf_headers
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_supervisor.start_instance.assert_called_once()

        # Stop
        response = client.post(
            f"/api/instances/{inst.id}/stop", headers=csrf_headers
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_supervisor.stop_instance.assert_called_once_with(inst.id)

        # Restart
        response = client.post(
            f"/api/instances/{inst.id}/restart", headers=csrf_headers
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_supervisor.restart_instance.assert_called_once_with(inst.id)

    def test_start_not_found(self, client, mock_supervisor, csrf_headers):
        """POST /api/instances/{id}/start with unknown id returns 404."""
        response = client.post("/api/instances/nonexistent/start", headers=csrf_headers)
        assert response.status_code == 404

    def test_stop_failure(self, client, mock_store, mock_supervisor, csrf_headers):
        """POST /api/instances/{id}/stop when supervisor fails returns 500."""
        inst = InstanceProfile(
            name="test", port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test", target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.stop_instance = AsyncMock(return_value=False)

        response = client.post(
            f"/api/instances/{inst.id}/stop", headers=csrf_headers
        )
        assert response.status_code == 500

    def test_restart_failure(self, client, mock_store, mock_supervisor, csrf_headers):
        """POST /api/instances/{id}/restart when supervisor fails returns 500."""
        inst = InstanceProfile(
            name="test", port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test", target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.restart_instance = AsyncMock(return_value=False)

        response = client.post(
            f"/api/instances/{inst.id}/restart", headers=csrf_headers
        )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# Instance logs
# ---------------------------------------------------------------------------


class TestInstanceLogs:
    def test_instance_logs(self, client, mock_store, mock_supervisor, csrf_headers):
        """GET /api/instances/{id}/logs returns log entries from supervisor."""
        inst = InstanceProfile(
            name="test-instance",
            port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test-key",
            target_model="gpt-4",
        )
        mock_store.instances.append(inst)

        mock_logs = [
            {"id": 0, "timestamp": "2025-01-01 00:00:00", "level": "INFO", "message": "Started"},
            {"id": 1, "timestamp": "2025-01-01 00:00:01", "level": "INFO", "message": "Ready"},
        ]
        mock_supervisor.get_instance_logs = AsyncMock(return_value=mock_logs)

        response = client.get(
            f"/api/instances/{inst.id}/logs",
            params={"type": "server", "lines": 50},
        )

        assert response.status_code == 200
        assert response.json() == mock_logs
        mock_supervisor.get_instance_logs.assert_called_once_with(
            inst.id, lines=50, log_type="server"
        )

    def test_instance_logs_empty(self, client, mock_store, mock_supervisor):
        """GET /api/instances/{id}/logs when no logs exist returns []."""
        inst = InstanceProfile(
            name="test", port=18001,
            upstream_base_url="https://api.openai.com/v1",
            upstream_api_key="sk-test", target_model="gpt-4",
        )
        mock_store.instances.append(inst)
        mock_supervisor.get_instance_logs = AsyncMock(return_value=[])

        response = client.get(
            f"/api/instances/{inst.id}/logs",
            params={"type": "access", "lines": 100},
        )

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# Global settings
# ---------------------------------------------------------------------------


class TestGlobalSettings:
    def test_get_global_settings(self, client, mock_store):
        """GET /api/global returns default global settings fields."""
        response = client.get("/api/global")
        assert response.status_code == 200
        data = response.json()
        assert data["log_level"] == "INFO"
        assert data["timeout"] == 300.0
        assert data["cors_origins"] == ["*"]
        assert data["cors_credentials"] is True
        assert data["cors_methods"] == ["*"]
        assert data["cors_headers"] == ["*"]

        # Sensitive fields should be absent when empty
        assert data.get("tavily_api_key", "") == ""
        assert data.get("tongxiao_api_key", "") == ""

    def test_patch_global_settings(self, client, mock_store, csrf_headers):
        """PATCH /api/global updates a setting and returns the new value."""
        with patch("cc_convert.web.routes.save_profile_store"):
            response = client.patch(
                "/api/global",
                json={"log_level": "DEBUG"},
                headers=csrf_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["log_level"] == "DEBUG"

    def test_patch_global_settings_multiple(self, client, mock_store, csrf_headers):
        """PATCH /api/global can update multiple fields at once."""
        with patch("cc_convert.web.routes.save_profile_store"):
            response = client.patch(
                "/api/global",
                json={
                    "log_level": "WARNING",
                    "timeout": 600.0,
                    "websearch_provider": "both",
                },
                headers=csrf_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["log_level"] == "WARNING"
        assert data["timeout"] == 600.0
        assert data["websearch_provider"] == "both"

    def test_patch_global_settings_rejects_masked_key(self, client, mock_store, csrf_headers):
        """PATCH /api/global with masked API key sentinel does not overwrite."""
        # First set a real key
        mock_store.global_tavily_api_key = "tvly-real-key-55555"

        with patch("cc_convert.web.routes.save_profile_store"):
            response = client.patch(
                "/api/global",
                json={"tavily_api_key": "***5555"},
                headers=csrf_headers,
            )

        assert response.status_code == 200
        data = response.json()
        # The existing key should be preserved (masked in response)
        assert data["tavily_api_key"] == "***5555"

    def test_get_global_settings_masked_keys(self, client, mock_store):
        """GET /api/global masks API keys in the response."""
        mock_store.global_tavily_api_key = "tvly-real-key-77777"
        mock_store.global_tongxiao_api_key = "tx-real-key-88888"

        response = client.get("/api/global")
        assert response.status_code == 200
        data = response.json()
        assert data["tavily_api_key"] == "***7777"
        assert data["tongxiao_api_key"] == "***8888"


# ---------------------------------------------------------------------------
# Dashboard page routes (basic reachability)
# ---------------------------------------------------------------------------


class TestPageRoutes:
    def test_dashboard_page(self, client):
        """GET / returns the dashboard HTML page."""
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_config_page(self, client):
        """GET /config returns the config form HTML page."""
        response = client.get("/config")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_logs_page(self, client):
        """GET /logs returns the log viewer HTML page."""
        response = client.get("/logs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_api_test_page(self, client):
        """GET /api-test returns the API tester HTML page."""
        response = client.get("/api-test")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_docs_page(self, client):
        """GET /docs returns the custom API documentation page (not Swagger)."""
        response = client.get("/docs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "CC Convert" in response.text
        assert "POST /v1/messages" in response.text

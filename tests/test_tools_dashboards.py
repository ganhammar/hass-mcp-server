"""Tests for dashboard tools."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestToolsDashboards:
    """Tests for dashboard tools."""

    @pytest.fixture
    def mock_server(self):
        """Create a mock MCP server."""
        return Mock()

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.services = Mock()
        return hass

    @pytest.fixture
    def view(self, mock_hass, mock_server):
        """Create an MCPEndpointView instance."""
        return MCPEndpointView(mock_hass, mock_server)

    async def test_post_tools_call_list_dashboards(self, view, mock_hass):
        """Test POST with tools/call for list_dashboards."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_dashboards", "arguments": {}},
                "id": 60,
            }
        )

        mock_dashboards = [
            {"url_path": "default", "mode": "storage", "title": "Home"},
            {"url_path": "energy", "mode": "storage", "title": "Energy"},
        ]

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.list_dashboards",
                new_callable=AsyncMock,
                return_value=mock_dashboards,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = json.loads(body["result"]["content"][0]["text"])
        assert len(result) == 2

    async def test_post_tools_call_list_dashboards_error(self, view, mock_hass):
        """Test POST with tools/call for list_dashboards when it fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_dashboards", "arguments": {}},
                "id": 61,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.list_dashboards",
                new_callable=AsyncMock,
                side_effect=Exception("lovelace not loaded"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error listing dashboards" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_get_dashboard_config(self, view, mock_hass):
        """Test POST with tools/call for get_dashboard_config."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {"url_path": "default"},
                },
                "id": 62,
            }
        )

        mock_config = {"views": [{"title": "Home", "cards": []}]}

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = json.loads(body["result"]["content"][0]["text"])
        assert "views" in result

    async def test_post_tools_call_get_dashboard_config_not_found(self, view, mock_hass):
        """Test POST with tools/call for get_dashboard_config when dashboard not found."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {"url_path": "nonexistent"},
                },
                "id": 63,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
                new_callable=AsyncMock,
                side_effect=ValueError("Dashboard 'nonexistent' not found"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error getting dashboard config" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_save_dashboard_config(self, view, mock_hass):
        """Test POST with tools/call for save_dashboard_config."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "save_dashboard_config",
                    "arguments": {
                        "url_path": "energy",
                        "config": {"views": [{"title": "Energy"}]},
                    },
                },
                "id": 64,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.save_dashboard_config",
                new_callable=AsyncMock,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully saved config" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_save_dashboard_config_error(self, view, mock_hass):
        """Test POST with tools/call for save_dashboard_config when it fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "save_dashboard_config",
                    "arguments": {
                        "url_path": "nonexistent",
                        "config": {"views": []},
                    },
                },
                "id": 65,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.save_dashboard_config",
                new_callable=AsyncMock,
                side_effect=ValueError("Dashboard 'nonexistent' not found"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error saving dashboard config" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_dashboard_config(self, view, mock_hass):
        """Test POST with tools/call for delete_dashboard_config."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_dashboard_config",
                    "arguments": {"url_path": "energy"},
                },
                "id": 66,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.delete_dashboard_config",
                new_callable=AsyncMock,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully deleted config" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_dashboard_config_error(self, view, mock_hass):
        """Test POST with tools/call for delete_dashboard_config when it fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_dashboard_config",
                    "arguments": {"url_path": "nonexistent"},
                },
                "id": 67,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.delete_dashboard_config",
                new_callable=AsyncMock,
                side_effect=ValueError("Dashboard 'nonexistent' not found"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error deleting dashboard config" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_dashboard(self, view, mock_hass):
        """Test POST with tools/call for create_dashboard."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_dashboard",
                    "arguments": {
                        "url_path": "my-dash",
                        "title": "My Dashboard",
                        "icon": "mdi:view-dashboard",
                    },
                },
                "id": 68,
            }
        )

        created_item = {"id": "abc", "url_path": "my-dash", "title": "My Dashboard"}

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.create_dashboard",
                new_callable=AsyncMock,
                return_value=created_item,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully created dashboard" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_dashboard_error(self, view, mock_hass):
        """Test POST with tools/call for create_dashboard when it fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_dashboard",
                    "arguments": {
                        "url_path": "default",
                        "title": "Default",
                    },
                },
                "id": 69,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.create_dashboard",
                new_callable=AsyncMock,
                side_effect=ValueError("Cannot create the default dashboard"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error creating dashboard" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_dashboard(self, view, mock_hass):
        """Test POST with tools/call for update_dashboard."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_dashboard",
                    "arguments": {
                        "url_path": "my-dash",
                        "title": "Updated Dashboard",
                    },
                },
                "id": 70,
            }
        )

        updated_item = {"url_path": "my-dash", "title": "Updated Dashboard"}

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.update_dashboard",
                new_callable=AsyncMock,
                return_value=updated_item,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully updated dashboard" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_dashboard_error(self, view, mock_hass):
        """Test POST with tools/call for update_dashboard when it fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_dashboard",
                    "arguments": {
                        "url_path": "default",
                        "title": "X",
                    },
                },
                "id": 71,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.update_dashboard",
                new_callable=AsyncMock,
                side_effect=ValueError("Cannot update the default dashboard"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error updating dashboard" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_dashboard(self, view, mock_hass):
        """Test POST with tools/call for delete_dashboard."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_dashboard",
                    "arguments": {"url_path": "my-dash"},
                },
                "id": 72,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.delete_dashboard",
                new_callable=AsyncMock,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully deleted dashboard" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_dashboard_error(self, view, mock_hass):
        """Test POST with tools/call for delete_dashboard when it fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_dashboard",
                    "arguments": {"url_path": "default"},
                },
                "id": 73,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.delete_dashboard",
                new_callable=AsyncMock,
                side_effect=ValueError("Cannot delete the default dashboard"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error deleting dashboard" in body["result"]["content"][0]["text"]

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

    async def test_post_tools_call_get_dashboard_config_with_path(self, view, mock_hass):
        """Test get_dashboard_config returning only the part a JSON Pointer addresses."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {"url_path": "default", "path": "/views/0/cards/1"},
                },
                "id": 74,
            }
        )

        mock_config = {
            "views": [
                {
                    "title": "Home",
                    "cards": [
                        {"type": "tile", "entity": "light.sofa"},
                        {"type": "tile", "entity": "fan.air_purifier"},
                    ],
                }
            ]
        }

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
        assert result == {"type": "tile", "entity": "fan.air_purifier"}

    async def test_post_tools_call_get_dashboard_config_with_unknown_path(self, view, mock_hass):
        """Test get_dashboard_config when the JSON Pointer does not resolve."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {"url_path": "default", "path": "/views/9"},
                },
                "id": 75,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
                new_callable=AsyncMock,
                return_value={"views": [{"title": "Home"}]},
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error getting dashboard config" in text
        assert "out of range" in text

    async def test_post_tools_call_get_dashboard_config_summary(self, view, mock_hass):
        """Test get_dashboard_config summary mode outlining the whole dashboard."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {"url_path": "default", "summary": True},
                },
                "id": 76,
            }
        )

        mock_config = {
            "views": [
                {
                    "title": "Home",
                    "cards": [
                        {"type": "tile", "entity": "light.sofa", "features": [{"type": "x"}]}
                    ],
                }
            ]
        }

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
        assert result["view_count"] == 1
        card = result["views"][0]["cards"][0]
        assert card["pointer"] == "/views/0/cards/0"
        assert "features" not in card

    async def test_post_tools_call_get_dashboard_config_summary_for_one_view(self, view, mock_hass):
        """Test get_dashboard_config summary mode scoped to a single view."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {"url_path": "default", "summary": True, "path": "/views/1"},
                },
                "id": 77,
            }
        )

        mock_config = {
            "views": [
                {"title": "Home", "cards": []},
                {"title": "Bedroom", "cards": [{"type": "tile", "entity": "light.bed"}]},
            ]
        }

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
        assert result["pointer"] == "/views/1"
        assert result["title"] == "Bedroom"
        assert result["cards"][0]["pointer"] == "/views/1/cards/0"

    async def test_post_tools_call_get_dashboard_config_summary_unsupported_path(
        self, view, mock_hass
    ):
        """Test get_dashboard_config summary mode with a path it cannot outline."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_config",
                    "arguments": {
                        "url_path": "default",
                        "summary": True,
                        "path": "/views/0/cards/1",
                    },
                },
                "id": 78,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
                new_callable=AsyncMock,
                return_value={"views": [{"title": "Home", "cards": []}]},
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "summary=true supports" in text
        assert "/views/0/cards/1" in text

    async def test_post_tools_call_patch_dashboard_config(self, view, mock_hass):
        """Test POST with tools/call for patch_dashboard_config."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "patch_dashboard_config",
                    "arguments": {
                        "url_path": "default",
                        "operations": [
                            {
                                "op": "test",
                                "path": "/views/0/cards/0/entity",
                                "value": "fan.air_purifier",
                            },
                            {
                                "op": "move",
                                "from": "/views/0/cards/0",
                                "path": "/views/1/cards/-",
                            },
                        ],
                    },
                },
                "id": 79,
            }
        )

        mock_config = {
            "views": [
                {"title": "Living Room", "cards": [{"type": "tile", "entity": "fan.air_purifier"}]},
                {"title": "Bedroom", "cards": []},
            ]
        }
        mock_save = AsyncMock()

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.save_dashboard_config",
                mock_save,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Applied 2 operation(s)" in text
        assert "[0] Living Room: 0 card(s)" in text
        assert "[1] Bedroom: 1 card(s)" in text

        saved = mock_save.await_args.args[2]
        assert saved["views"][0]["cards"] == []
        assert saved["views"][1]["cards"][0]["entity"] == "fan.air_purifier"

    async def test_post_tools_call_patch_dashboard_config_error(self, view, mock_hass):
        """Test patch_dashboard_config leaving the dashboard alone when an operation fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "patch_dashboard_config",
                    "arguments": {
                        "url_path": "default",
                        "operations": [
                            {"op": "test", "path": "/views/0/title", "value": "Away"},
                        ],
                    },
                },
                "id": 80,
            }
        )

        mock_save = AsyncMock()

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
                new_callable=AsyncMock,
                return_value={"views": [{"title": "Home", "cards": []}]},
            ),
            patch(
                "custom_components.mcp_server_http_transport.dashboard_manager.save_dashboard_config",
                mock_save,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error patching dashboard config" in text
        assert "test failed" in text
        mock_save.assert_not_awaited()

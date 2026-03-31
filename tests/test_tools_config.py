"""Tests for automation/scene/script config tools."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestToolsConfig:
    """Test automation/scene/script config tool calls."""

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

    async def test_post_tools_call_create_automation(self, view, mock_hass):
        """Test POST with tools/call for create_automation."""
        mock_hass.config.path.return_value = "/config/automations.yaml"
        mock_hass.async_add_executor_job = AsyncMock(return_value=None)
        mock_hass.services.async_call = AsyncMock()

        config = {"alias": "Test Automation", "trigger": [], "action": []}

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "create_automation", "arguments": {"config": config}},
                "id": 40,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.uuid.uuid4",
                return_value="test-uuid-1234",
            ),
        ):
            # Make async_add_executor_job execute the function immediately
            async def run_fn(fn):
                return fn()

            mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully created automation" in body["result"]["content"][0]["text"]
        assert "test-uuid-1234" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_automation_error(self, view, mock_hass):
        """Test POST with tools/call for create_automation when write fails."""
        mock_hass.config.path.return_value = "/config/automations.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_automation",
                    "arguments": {"config": {"alias": "Test"}},
                },
                "id": 41,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                side_effect=Exception("Permission denied"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error creating automation" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_automation(self, view, mock_hass):
        """Test POST with tools/call for update_automation."""
        mock_hass.config.path.return_value = "/config/automations.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_automation",
                    "arguments": {
                        "automation_id": "existing-id",
                        "config": {"alias": "Updated"},
                    },
                },
                "id": 42,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[{"id": "existing-id", "alias": "Old"}],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully updated automation" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_automation_not_found(self, view, mock_hass):
        """Test POST with tools/call for update_automation with invalid id."""
        mock_hass.config.path.return_value = "/config/automations.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_automation",
                    "arguments": {
                        "automation_id": "nonexistent",
                        "config": {"alias": "Updated"},
                    },
                },
                "id": 43,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error updating automation" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_automation(self, view, mock_hass):
        """Test POST with tools/call for delete_automation."""
        mock_hass.config.path.return_value = "/config/automations.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_automation",
                    "arguments": {"automation_id": "to-delete"},
                },
                "id": 44,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[{"id": "to-delete", "alias": "Delete Me"}],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully deleted automation" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_automation_not_found(self, view, mock_hass):
        """Test POST with tools/call for delete_automation with invalid id."""
        mock_hass.config.path.return_value = "/config/automations.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_automation",
                    "arguments": {"automation_id": "nonexistent"},
                },
                "id": 45,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error deleting automation" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_scene(self, view, mock_hass):
        """Test POST with tools/call for create_scene."""
        mock_hass.config.path.return_value = "/config/scenes.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_scene",
                    "arguments": {
                        "config": {"name": "Movie Night", "entities": {}},
                    },
                },
                "id": 46,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.uuid.uuid4",
                return_value="scene-uuid",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully created scene" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_scene(self, view, mock_hass):
        """Test POST with tools/call for update_scene."""
        mock_hass.config.path.return_value = "/config/scenes.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_scene",
                    "arguments": {
                        "scene_id": "scene-1",
                        "config": {"name": "Updated Scene"},
                    },
                },
                "id": 47,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[{"id": "scene-1", "name": "Old Scene"}],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully updated scene" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_scene(self, view, mock_hass):
        """Test POST with tools/call for delete_scene."""
        mock_hass.config.path.return_value = "/config/scenes.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_scene",
                    "arguments": {"scene_id": "scene-1"},
                },
                "id": 48,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[{"id": "scene-1", "name": "Delete Me"}],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully deleted scene" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_scene_error(self, view, mock_hass):
        """Test POST with tools/call for create_scene when write fails."""
        mock_hass.config.path.return_value = "/config/scenes.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_scene",
                    "arguments": {"config": {"name": "Test"}},
                },
                "id": 55,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                side_effect=Exception("Permission denied"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error creating scene" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_scene_not_found(self, view, mock_hass):
        """Test POST with tools/call for update_scene with invalid id."""
        mock_hass.config.path.return_value = "/config/scenes.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_scene",
                    "arguments": {
                        "scene_id": "nonexistent",
                        "config": {"name": "Updated"},
                    },
                },
                "id": 56,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error updating scene" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_scene_not_found(self, view, mock_hass):
        """Test POST with tools/call for delete_scene with invalid id."""
        mock_hass.config.path.return_value = "/config/scenes.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_scene",
                    "arguments": {"scene_id": "nonexistent"},
                },
                "id": 57,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error deleting scene" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_script(self, view, mock_hass):
        """Test POST with tools/call for create_script."""
        mock_hass.config.path.return_value = "/config/scripts.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_script",
                    "arguments": {
                        "key": "morning_routine",
                        "config": {"alias": "Morning Routine", "sequence": []},
                    },
                },
                "id": 49,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={},
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully created script" in body["result"]["content"][0]["text"]
        assert "morning_routine" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_create_script_duplicate(self, view, mock_hass):
        """Test POST with tools/call for create_script with duplicate key."""
        mock_hass.config.path.return_value = "/config/scripts.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_script",
                    "arguments": {
                        "key": "existing_script",
                        "config": {"alias": "Dup", "sequence": []},
                    },
                },
                "id": 50,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={"existing_script": {"alias": "Existing"}},
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error creating script" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_script(self, view, mock_hass):
        """Test POST with tools/call for update_script."""
        mock_hass.config.path.return_value = "/config/scripts.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_script",
                    "arguments": {
                        "key": "morning_routine",
                        "config": {"alias": "Updated Routine", "sequence": []},
                    },
                },
                "id": 51,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={"morning_routine": {"alias": "Old"}},
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully updated script" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_update_script_not_found(self, view, mock_hass):
        """Test POST with tools/call for update_script with missing key."""
        mock_hass.config.path.return_value = "/config/scripts.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "update_script",
                    "arguments": {
                        "key": "nonexistent",
                        "config": {"alias": "Nope"},
                    },
                },
                "id": 52,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={},
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error updating script" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_script(self, view, mock_hass):
        """Test POST with tools/call for delete_script."""
        mock_hass.config.path.return_value = "/config/scripts.yaml"
        mock_hass.services.async_call = AsyncMock()

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_script",
                    "arguments": {"key": "morning_routine"},
                },
                "id": 53,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={"morning_routine": {"alias": "Delete Me"}},
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully deleted script" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_delete_script_not_found(self, view, mock_hass):
        """Test POST with tools/call for delete_script with missing key."""
        mock_hass.config.path.return_value = "/config/scripts.yaml"

        async def run_fn(fn):
            return fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "delete_script",
                    "arguments": {"key": "nonexistent"},
                },
                "id": 54,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={},
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error deleting script" in body["result"]["content"][0]["text"]

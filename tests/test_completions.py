"""Tests for completion endpoints."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestCompletions:
    """Test the MCP completion endpoints."""

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

    async def test_post_completion_entity_id(self, view, mock_hass):
        """Test POST with completion/complete for entity_id."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state2 = Mock()
        mock_state2.entity_id = "light.bedroom"
        mock_state3 = Mock()
        mock_state3.entity_id = "switch.kitchen"
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "get_state"},
                    "argument": {"name": "entity_id", "value": "light."},
                },
                "id": 33,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert len(completion["values"]) == 2
        assert "light.living_room" in completion["values"]
        assert "light.bedroom" in completion["values"]

    async def test_post_completion_domain(self, view, mock_hass):
        """Test POST with completion/complete for domain."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "list_entities"},
                    "argument": {"name": "domain", "value": "li"},
                },
                "id": 34,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "light" in completion["values"]
        assert "switch" not in completion["values"]

    async def test_post_completion_service(self, view, mock_hass):
        """Test POST with completion/complete for service."""
        mock_hass.services.async_services.return_value = {
            "light": {"turn_on": Mock(), "turn_off": Mock(), "toggle": Mock()},
            "switch": {"turn_on": Mock(), "turn_off": Mock()},
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "call_service"},
                    "argument": {"name": "service", "value": "turn"},
                },
                "id": 35,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "turn_on" in completion["values"]
        assert "turn_off" in completion["values"]
        assert "toggle" not in completion["values"]

    async def test_post_completion_area_id(self, view, mock_hass):
        """Test POST with completion/complete for area_id."""
        mock_area1 = Mock()
        mock_area1.id = "living_room"
        mock_area2 = Mock()
        mock_area2.id = "kitchen"

        mock_registry = Mock()
        mock_registry.async_list_areas.return_value = [mock_area1, mock_area2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "list_devices"},
                    "argument": {"name": "area_id", "value": "liv"},
                },
                "id": 36,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.completions.ar.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "living_room" in completion["values"]
        assert "kitchen" not in completion["values"]

    async def test_post_completion_unknown_argument(self, view, mock_hass):
        """Test POST with completion/complete for unknown argument."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "get_state"},
                    "argument": {"name": "unknown_arg", "value": "test"},
                },
                "id": 37,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert completion["values"] == []
        assert completion["hasMore"] is False

    async def test_post_completion_entity_ids(self, view, mock_hass):
        """Test POST with completion/complete for entity_ids argument."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state2 = Mock()
        mock_state2.entity_id = "light.bedroom"
        mock_state3 = Mock()
        mock_state3.entity_id = "switch.kitchen"
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "batch_get_state"},
                    "argument": {"name": "entity_ids", "value": "light."},
                },
                "id": 237,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "light.living_room" in completion["values"]
        assert "light.bedroom" in completion["values"]
        assert "switch.kitchen" not in completion["values"]

    async def test_post_completion_trigger_type(self, view, mock_hass):
        """Test POST with completion/complete for trigger_type argument."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/prompt", "name": "automation_builder"},
                    "argument": {"name": "trigger_type", "value": "ti"},
                },
                "id": 238,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "time" in completion["values"]
        assert "time_pattern" in completion["values"]

    async def test_post_completion_period(self, view, mock_hass):
        """Test POST with completion/complete for period argument."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "get_statistics"},
                    "argument": {"name": "period", "value": ""},
                },
                "id": 239,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "hour" in completion["values"]
        assert "day" in completion["values"]
        assert "5minute" in completion["values"]

    async def test_post_completion_config_type(self, view, mock_hass):
        """Test POST with completion/complete for config_type argument."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/prompt", "name": "change_validator"},
                    "argument": {"name": "config_type", "value": ""},
                },
                "id": 240,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "automation" in completion["values"]
        assert "scene" in completion["values"]
        assert "script" in completion["values"]

    async def test_post_completion_automation_id_debugger(self, view, mock_hass):
        """Test POST with completion/complete for automation_id with automation_debugger."""
        mock_automations = [
            {"id": "abc-123", "alias": "Morning"},
            {"id": "def-456", "alias": "Night"},
        ]

        mock_hass.config.path.return_value = "/config/automations.yaml"

        async def run_fn(fn, *args):
            return fn(*args)

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/prompt", "name": "automation_debugger"},
                    "argument": {"name": "automation_id", "value": "abc"},
                },
                "id": 241,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=mock_automations,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "abc-123" in completion["values"]
        assert "def-456" not in completion["values"]

    async def test_post_completion_domain_create_helper(self, view, mock_hass):
        """Test domain completion for create_helper returns helper domains, not entity domains."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/tool", "name": "create_helper"},
                    "argument": {"name": "domain", "value": ""},
                },
                "id": 242,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        completion = body["result"]["completion"]
        assert "input_boolean" in completion["values"]
        assert "counter" in completion["values"]
        assert "timer" in completion["values"]
        assert "light" not in completion["values"]
        assert "switch" not in completion["values"]

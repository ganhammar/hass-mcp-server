"""Tests for system admin tool endpoints."""

import json
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestToolsSystemAdmin:
    """Test the system admin tool endpoints."""

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

    async def test_post_tools_call_get_error_log(self, view, mock_hass):
        """Test POST with tools/call for get_error_log."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(
            return_value="2024-01-01 ERROR Something went wrong\n2024-01-01 WARNING Low battery"
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 200,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "ERROR" in text
        assert "WARNING" in text

    async def test_post_tools_call_get_error_log_with_lines(self, view, mock_hass):
        """Test POST with tools/call for get_error_log with lines parameter."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(return_value="Last line only\n")

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {"lines": 1}},
                "id": 201,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Last line only" in text

    async def test_post_tools_call_get_error_log_file_not_found(self, view, mock_hass):
        """Test POST with tools/call for get_error_log when file is missing."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(return_value="Log file not found")

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 202,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Log file not found" in text

    async def test_post_tools_call_restart_ha_confirmed(self, view, mock_hass):
        """Test POST with tools/call for restart_ha with confirm=true."""
        mock_hass.services.async_call = AsyncMock()

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "restart_ha",
                    "arguments": {"confirm": True},
                },
                "id": 203,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "restart has been initiated" in text

    async def test_post_tools_call_restart_ha_not_confirmed(self, view, mock_hass):
        """Test POST with tools/call for restart_ha with confirm=false."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "restart_ha",
                    "arguments": {"confirm": False},
                },
                "id": 204,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "confirm=true" in text

    async def test_post_tools_call_restart_ha_missing_confirm(self, view, mock_hass):
        """Test POST with tools/call for restart_ha without confirm argument."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "restart_ha",
                    "arguments": {},
                },
                "id": 205,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "confirm=true" in text

    async def test_post_tools_call_get_system_status(self, view, mock_hass):
        """Test POST with tools/call for get_system_status."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temp"
        mock_state2.state = "unavailable"
        mock_state3 = Mock()
        mock_state3.entity_id = "switch.kitchen"
        mock_state3.state = "off"
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]
        mock_hass.config.version = "2024.12.0"

        mock_entry = Mock()
        mock_hass.config_entries.async_entries.return_value = [mock_entry, mock_entry]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_system_status", "arguments": {}},
                "id": 206,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["version"] == "2024.12.0"
        assert data["total_entities"] == 3
        assert data["domain_counts"]["light"] == 1
        assert data["domain_counts"]["sensor"] == 1
        assert data["domain_counts"]["switch"] == 1
        assert len(data["problem_entities"]) == 1
        assert data["problem_entities"][0]["entity_id"] == "sensor.temp"
        assert data["integration_count"] == 2

    async def test_post_tools_call_get_domain_stats(self, view, mock_hass):
        """Test POST with tools/call for get_domain_stats."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Light"}
        mock_state2 = Mock()
        mock_state2.entity_id = "light.bedroom"
        mock_state2.state = "off"
        mock_state2.attributes = {"friendly_name": "Bedroom Light"}
        mock_state3 = Mock()
        mock_state3.entity_id = "sensor.temp"
        mock_state3.state = "22"
        mock_state3.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_domain_stats",
                    "arguments": {"domain": "light"},
                },
                "id": 207,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["domain"] == "light"
        assert data["total"] == 2
        assert data["state_counts"]["on"] == 1
        assert data["state_counts"]["off"] == 1
        assert len(data["examples"]) == 2

    async def test_post_tools_call_get_domain_stats_empty(self, view, mock_hass):
        """Test POST with tools/call for get_domain_stats with no matching entities."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.temp"
        mock_state.state = "22"
        mock_state.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_domain_stats",
                    "arguments": {"domain": "light"},
                },
                "id": 208,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["total"] == 0

    async def test_post_tools_call_check_config_valid(self, view, mock_hass):
        """Test POST with tools/call for check_config with valid config."""
        mock_result = Mock()
        mock_result.errors = []

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "check_config", "arguments": {}},
                "id": 209,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["valid"] is True
        assert data["errors"] == []

    async def test_post_tools_call_check_config_errors(self, view, mock_hass):
        """Test POST with tools/call for check_config with errors."""
        mock_result = Mock()
        mock_result.errors = ["Invalid automation config", "Missing entity"]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "check_config", "arguments": {}},
                "id": 210,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["valid"] is False
        assert len(data["errors"]) == 2

    async def test_post_tools_call_list_integrations(self, view, mock_hass):
        """Test POST with tools/call for list_integrations."""
        mock_entry1 = Mock()
        mock_entry1.domain = "hue"
        mock_entry1.title = "Philips Hue"
        mock_entry1.state = "loaded"
        mock_entry1.entry_id = "entry1"

        mock_entry2 = Mock()
        mock_entry2.domain = "zwave"
        mock_entry2.title = "Z-Wave"
        mock_entry2.state = "loaded"
        mock_entry2.entry_id = "entry2"

        mock_hass.config_entries.async_entries.return_value = [mock_entry1, mock_entry2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_integrations", "arguments": {}},
                "id": 211,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["domain"] == "hue"
        assert data[0]["title"] == "Philips Hue"
        assert data[1]["domain"] == "zwave"

    async def test_post_tools_call_get_error_log_read_error(self, view, mock_hass):
        """Test get_error_log when async_add_executor_job raises."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(side_effect=Exception("IO error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 250,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error reading error log" in text

    async def test_post_tools_call_get_error_log_actual_file_read(self, view, mock_hass):
        """Test get_error_log reading actual file content via executor."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            log_path = f.name

        mock_hass.config.path.return_value = log_path

        async def run_fn(fn, *args):
            return fn(*args) if args else fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {"lines": 2}},
                "id": 251,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "line2" in text
        assert "line3" in text

    async def test_post_tools_call_get_error_log_file_missing(self, view, mock_hass):
        """Test get_error_log when log file does not exist (FileNotFoundError)."""
        mock_hass.config.path.return_value = "/nonexistent/path/home-assistant.log"

        async def run_fn(fn, *args):
            return fn(*args) if args else fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 255,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Log file not found" in text

    async def test_post_tools_call_restart_ha_error(self, view, mock_hass):
        """Test restart_ha when service call raises."""
        mock_hass.services.async_call = AsyncMock(side_effect=Exception("Service error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "restart_ha", "arguments": {"confirm": True}},
                "id": 252,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error restarting Home Assistant" in text

    async def test_post_tools_call_check_config_error(self, view, mock_hass):
        """Test check_config when async_check_ha_config_file raises."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "check_config", "arguments": {}},
                "id": 253,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                side_effect=Exception("Config check failed"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error checking config" in text

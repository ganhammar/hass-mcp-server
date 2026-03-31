"""Tests for prompt-related MCP endpoints."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestPrompts:
    """Test prompt-related MCP endpoint functionality."""

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

    async def test_post_prompts_list(self, view):
        """Test POST with prompts/list request."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={"jsonrpc": "2.0", "method": "prompts/list", "id": 28}
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        prompts = body["result"]["prompts"]
        assert len(prompts) == 13
        prompt_names = [p["name"] for p in prompts]
        assert "troubleshoot_device" in prompt_names
        assert "daily_summary" in prompt_names
        assert "automation_review" in prompt_names
        assert "energy_report" in prompt_names
        assert "setup_guide" in prompt_names
        assert "automation_builder" in prompt_names
        assert "automation_debugger" in prompt_names
        assert "automation_audit" in prompt_names
        assert "schedule_optimizer" in prompt_names
        assert "naming_conventions" in prompt_names
        assert "dashboard_builder" in prompt_names
        assert "change_validator" in prompt_names
        assert "security_review" in prompt_names

    async def test_post_prompts_get_troubleshoot_device(self, view, mock_hass):
        """Test POST with prompts/get for troubleshoot_device."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "unavailable"
        mock_state.attributes = {"friendly_name": "Living Room Light"}
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.get.return_value = mock_state

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "troubleshoot_device",
                    "arguments": {"entity_id": "light.living_room"},
                },
                "id": 29,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "Troubleshoot" in result["description"]
        assert len(result["messages"]) == 1
        assert "unavailable" in result["messages"][0]["content"]["text"]

    async def test_post_prompts_get_troubleshoot_device_not_found(self, view, mock_hass):
        """Test POST with prompts/get for troubleshoot_device with unknown entity."""
        mock_hass.states.get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "troubleshoot_device",
                    "arguments": {"entity_id": "light.nonexistent"},
                },
                "id": 30,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "not found" in body["result"]["messages"][0]["content"]["text"]

    async def test_post_prompts_get_daily_summary(self, view, mock_hass):
        """Test POST with prompts/get for daily_summary."""
        mock_state1 = Mock()
        mock_state1.state = "on"
        mock_state2 = Mock()
        mock_state2.state = "off"

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(
            return_value={"light.living_room": [mock_state1, mock_state2]}
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "daily_summary", "arguments": {}},
                "id": 31,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "Daily summary" in result["description"]
        assert "light.living_room" in result["messages"][0]["content"]["text"]

    async def test_post_prompts_get_daily_summary_recorder_error(self, view, mock_hass):
        """Test POST with prompts/get for daily_summary when recorder fails."""
        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(
            side_effect=Exception("Recorder not available")
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "daily_summary", "arguments": {}},
                "id": 32,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "Unable to retrieve history data" in text

    async def test_post_prompts_get_unknown(self, view, mock_hass):
        """Test POST with prompts/get for unknown prompt."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "unknown_prompt", "arguments": {}},
                "id": 32,
            }
        )
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert "Unknown prompt" in body["error"]["message"]

    async def test_post_prompts_get_automation_review(self, view, mock_hass):
        """Test prompts/get for automation_review."""
        mock_config = {"id": "abc-123", "alias": "Test Auto", "trigger": []}

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "automation_review",
                    "arguments": {"automation_id": "abc-123"},
                },
                "id": 89,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        prompt = body["result"]
        text = prompt["messages"][0]["content"]["text"]
        assert "Test Auto" in text
        assert "trigger" in text.lower()

    async def test_post_prompts_get_automation_review_not_found(self, view, mock_hass):
        """Test automation_review with nonexistent automation."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "automation_review",
                    "arguments": {"automation_id": "nonexistent"},
                },
                "id": 90,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
                new_callable=AsyncMock,
                side_effect=ValueError("Not found"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "not found" in text

    async def test_post_prompts_get_setup_guide(self, view, mock_hass):
        """Test prompts/get for setup_guide."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.temp"
        mock_state.state = "unavailable"
        mock_state.attributes = {
            "friendly_name": "Temperature",
            "device_class": "temperature",
        }
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.get.return_value = mock_state

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "setup_guide",
                    "arguments": {"entity_id": "sensor.temp"},
                },
                "id": 91,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "sensor" in text
        assert "temperature" in text

    async def test_post_prompts_get_energy_report(self, view, mock_hass):
        """Test prompts/get for energy_report."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.energy"
        mock_state.state = "100"
        mock_state.attributes = {
            "friendly_name": "Energy",
            "device_class": "energy",
            "unit_of_measurement": "kWh",
        }
        mock_hass.states.async_all.return_value = [mock_state]

        mock_history_state = Mock()
        mock_history_state.state = "100"
        mock_history_state.attributes = {
            "friendly_name": "Energy",
            "unit_of_measurement": "kWh",
        }

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(
            return_value={"sensor.energy": [mock_history_state]}
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "energy_report",
                    "arguments": {"start_time": "2024-01-01T00:00:00"},
                },
                "id": 92,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "energy" in text.lower()

    async def test_post_prompts_get_energy_report_no_entities(self, view, mock_hass):
        """Test energy_report when no energy entities exist."""
        mock_state = Mock()
        mock_state.entity_id = "light.test"
        mock_state.state = "on"
        mock_state.attributes = {"friendly_name": "Test Light"}
        mock_hass.states.async_all.return_value = [mock_state]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "energy_report",
                    "arguments": {"start_time": "2024-01-01T00:00:00"},
                },
                "id": 93,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "No energy-related entities" in text

    async def test_post_prompts_get_energy_report_recorder_error(self, view, mock_hass):
        """Test energy_report when recorder fails."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.energy"
        mock_state.state = "100"
        mock_state.attributes = {
            "friendly_name": "Energy",
            "device_class": "energy",
            "unit_of_measurement": "kWh",
        }
        mock_hass.states.async_all.return_value = [mock_state]

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(
            side_effect=Exception("Recorder unavailable")
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "energy_report",
                    "arguments": {"start_time": "2024-01-01T00:00:00"},
                },
                "id": 94,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "Unable to retrieve" in text

    async def test_post_prompts_get_setup_guide_entity_not_found(self, view, mock_hass):
        """Test setup_guide when entity does not exist."""
        mock_hass.states.get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "setup_guide",
                    "arguments": {"entity_id": "sensor.nonexistent"},
                },
                "id": 95,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "not found" in text
        assert "sensor" in text

    async def test_post_prompts_get_automation_builder(self, view, mock_hass):
        """Test POST with prompts/get for automation_builder."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_hass.states.async_all.return_value = [mock_state]
        mock_hass.services.async_services.return_value = {
            "light": {"turn_on": Mock()},
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "automation_builder", "arguments": {}},
                "id": 226,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "Guided" in result["description"]
        assert len(result["messages"]) == 1
        text = result["messages"][0]["content"]["text"]
        assert "step by step" in text
        assert "Trigger" in text

    async def test_post_prompts_get_automation_builder_with_trigger(self, view, mock_hass):
        """Test POST with prompts/get for automation_builder with trigger_type."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_hass.states.async_all.return_value = [mock_state]
        mock_hass.services.async_services.return_value = {
            "light": {"turn_on": Mock()},
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "automation_builder",
                    "arguments": {"trigger_type": "time"},
                },
                "id": 227,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "time" in text

    async def test_post_prompts_get_automation_debugger(self, view, mock_hass):
        """Test POST with prompts/get for automation_debugger."""
        mock_config = {"id": "test-auto", "alias": "Test Auto", "trigger": []}

        mock_auto_state = Mock()
        mock_auto_state.entity_id = "automation.test_auto"
        mock_auto_state.state = "on"
        mock_auto_state.attributes = {
            "last_triggered": "2024-01-01T12:00:00",
            "current": 0,
            "mode": "single",
            "id": "test-auto",
        }
        mock_hass.states.get.return_value = None
        mock_hass.states.async_all.return_value = [mock_auto_state]

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value=[])

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "automation_debugger",
                    "arguments": {"automation_id": "test-auto"},
                },
                "id": 228,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "homeassistant.components.logbook.processor.EventProcessor",
                return_value=Mock(get_events=Mock(return_value=[])),
            ),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "Debug" in result["description"]
        text = result["messages"][0]["content"]["text"]
        assert "Test Auto" in text
        assert "automation" in text.lower()

    async def test_post_prompts_get_automation_debugger_not_found(self, view, mock_hass):
        """Test POST with prompts/get for automation_debugger when not found."""
        mock_hass.states.get.return_value = None
        mock_hass.states.async_all.return_value = []

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "automation_debugger",
                    "arguments": {"automation_id": "nonexistent"},
                },
                "id": 229,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
                new_callable=AsyncMock,
                side_effect=ValueError("Not found"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "not found" in text

    async def test_post_prompts_get_automation_audit(self, view, mock_hass):
        """Test POST with prompts/get for automation_audit."""
        mock_automations = [
            {"id": "auto1", "alias": "Morning Lights"},
            {"id": "auto2", "alias": "Night Lock"},
        ]

        mock_auto_state = Mock()
        mock_auto_state.entity_id = "automation.morning_lights"
        mock_auto_state.state = "on"
        mock_auto_state.attributes = {"last_triggered": "2024-01-01T07:00:00"}
        mock_hass.states.async_all.return_value = [mock_auto_state]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "automation_audit", "arguments": {}},
                "id": 230,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entries",
                new_callable=AsyncMock,
                return_value=mock_automations,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "Conflicts" in text
        assert "Redundancies" in text
        assert "Morning Lights" in text

    async def test_post_prompts_get_schedule_optimizer(self, view, mock_hass):
        """Test POST with prompts/get for schedule_optimizer."""
        mock_automations = [{"id": "auto1", "alias": "Morning Routine"}]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "schedule_optimizer", "arguments": {}},
                "id": 231,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entries",
                new_callable=AsyncMock,
                return_value=mock_automations,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "optimization" in result["description"]
        assert len(result["messages"]) == 1

    async def test_post_prompts_get_schedule_optimizer_with_entity(self, view, mock_hass):
        """Test POST with prompts/get for schedule_optimizer with entity_id."""
        mock_automations = [{"id": "auto1", "alias": "Morning Routine"}]

        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {"brightness": 255}
        mock_hass.states.get.return_value = mock_state

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "schedule_optimizer",
                    "arguments": {"entity_id": "light.living_room"},
                },
                "id": 232,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entries",
                new_callable=AsyncMock,
                return_value=mock_automations,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "light.living_room" in text

    async def test_post_prompts_get_naming_conventions(self, view, mock_hass):
        """Test POST with prompts/get for naming_conventions."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}
        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temp"
        mock_state2.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "naming_conventions", "arguments": {}},
                "id": 233,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "Consistency" in text
        assert "light.living_room" in text or "Living Room Light" in text

    async def test_post_prompts_get_dashboard_builder_with_area(self, view, mock_hass):
        """Test POST with prompts/get for dashboard_builder with area_id."""
        mock_area = Mock()
        mock_area.id = "living_room"
        mock_area.name = "Living Room"

        mock_area_registry = Mock()
        mock_area_registry.async_get_area.return_value = mock_area

        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {
            "friendly_name": "Living Room Light",
            "device_class": None,
        }
        mock_hass.states.async_all.return_value = [mock_state]

        mock_entry = Mock()
        mock_entry.area_id = "living_room"
        mock_entity_registry = Mock()
        mock_entity_registry.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {
                    "name": "dashboard_builder",
                    "arguments": {"area_id": "living_room"},
                },
                "id": 234,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.prompts.workflows.ar.async_get",
                return_value=mock_area_registry,
            ),
            patch(
                "custom_components.mcp_server_http_transport.prompts.workflows.er.async_get",
                return_value=mock_entity_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "Dashboard" in result["description"] or "layout" in result["description"]
        text = result["messages"][0]["content"]["text"]
        assert "Living Room" in text
        assert "light.living_room" in text

    async def test_post_prompts_get_change_validator(self, view, mock_hass):
        """Test POST with prompts/get for change_validator."""
        mock_automations = [{"id": "auto1", "alias": "Test"}]
        mock_scenes = [{"id": "scene1", "name": "Movie Night"}]
        mock_scripts = {"morning": {"alias": "Morning"}}

        mock_config_result = Mock()
        mock_config_result.errors = []

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "change_validator", "arguments": {}},
                "id": 235,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_list_entries",
                new_callable=AsyncMock,
                side_effect=[mock_automations, mock_scenes],
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.read_dict_entries",
                new_callable=AsyncMock,
                return_value=mock_scripts,
            ),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                return_value=mock_config_result,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["messages"][0]["content"]["text"]
        assert "Automations" in text or "Scripts" in text
        assert "validation" in text.lower() or "Passed" in text

    async def test_post_prompts_get_security_review(self, view, mock_hass):
        """Test POST with prompts/get for security_review."""
        mock_hass.config.external_url = "https://home.example.com"
        mock_hass.config.internal_url = "http://192.168.1.100:8123"

        mock_entry = Mock()
        mock_entry.domain = "hue"
        mock_entry.title = "Philips Hue"
        mock_entry.state = "loaded"
        mock_hass.config_entries.async_entries.return_value = [mock_entry]

        mock_state1 = Mock()
        mock_state1.entity_id = "camera.front_door"
        mock_state1.state = "idle"
        mock_state1.attributes = {"friendly_name": "Front Door Camera"}
        mock_state2 = Mock()
        mock_state2.entity_id = "lock.front_door"
        mock_state2.state = "locked"
        mock_state2.attributes = {"friendly_name": "Front Door Lock"}
        mock_state3 = Mock()
        mock_state3.entity_id = "light.living_room"
        mock_state3.state = "on"
        mock_state3.attributes = {"friendly_name": "Living Room Light"}
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "prompts/get",
                "params": {"name": "security_review", "arguments": {}},
                "id": 236,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert "Security" in result["description"] or "security" in result["description"]
        text = result["messages"][0]["content"]["text"]
        assert "camera.front_door" in text
        assert "lock.front_door" in text

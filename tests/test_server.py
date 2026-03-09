"""Tests for MCP Server implementation."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.mcp_server_http_transport.server import HomeAssistantMCPServer
from custom_components.mcp_server_http_transport.tools import MCPTools


class TestHomeAssistantMCPServer:
    """Test the HomeAssistantMCPServer class."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.services = Mock()
        return hass

    @pytest.fixture
    def server(self, mock_hass):
        """Create a HomeAssistantMCPServer instance."""
        return HomeAssistantMCPServer(mock_hass)

    def test_init_creates_server(self, mock_hass):
        """Test __init__ creates MCP server."""
        server = HomeAssistantMCPServer(mock_hass)

        assert server.hass == mock_hass
        assert server.server is not None
        assert server.server.name == "home-assistant-mcp-server"
        assert isinstance(server.tools, MCPTools)

    async def test_get_state_returns_entity_info(self, server, mock_hass):
        """Test _get_state returns entity information."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {"brightness": 255}
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.get.return_value = mock_state

        result = await server.tools._get_state({"entity_id": "light.living_room"})

        assert len(result) == 1
        assert result[0].type == "text"
        assert "light.living_room" in result[0].text
        assert "on" in result[0].text

    async def test_get_state_entity_not_found(self, server, mock_hass):
        """Test _get_state when entity doesn't exist."""
        mock_hass.states.get.return_value = None

        result = await server.tools._get_state({"entity_id": "light.nonexistent"})

        assert len(result) == 1
        assert "not found" in result[0].text

    async def test_call_service_success(self, server, mock_hass):
        """Test _call_service calls Home Assistant service."""
        mock_hass.services.async_call = AsyncMock()

        result = await server.tools._call_service(
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.living_room",
                "data": {"brightness": 255},
            }
        )

        assert len(result) == 1
        assert "Successfully called" in result[0].text
        mock_hass.services.async_call.assert_called_once_with(
            "light", "turn_on", {"brightness": 255, "entity_id": "light.living_room"}, blocking=True
        )

    async def test_call_service_without_entity_id(self, server, mock_hass):
        """Test _call_service without entity_id."""
        mock_hass.services.async_call = AsyncMock()

        result = await server.tools._call_service({"domain": "homeassistant", "service": "restart"})

        assert len(result) == 1
        assert "Successfully called" in result[0].text
        mock_hass.services.async_call.assert_called_once_with(
            "homeassistant", "restart", {}, blocking=True
        )

    async def test_call_service_error(self, server, mock_hass):
        """Test _call_service handles errors."""
        mock_hass.services.async_call = AsyncMock(side_effect=Exception("Service error"))

        result = await server.tools._call_service({"domain": "light", "service": "turn_on"})

        assert len(result) == 1
        assert "Error calling service" in result[0].text

    async def test_list_entities_all(self, server, mock_hass):
        """Test _list_entities returns all entities."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_state2.state = "off"
        mock_state2.attributes = {"friendly_name": "Kitchen Switch"}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        result = await server.tools._list_entities({})

        assert len(result) == 1
        assert "light.living_room" in result[0].text
        assert "switch.kitchen" in result[0].text

    async def test_list_entities_with_domain_filter(self, server, mock_hass):
        """Test _list_entities with domain filter."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_state2.state = "off"
        mock_state2.attributes = {}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        result = await server.tools._list_entities({"domain": "light"})

        assert len(result) == 1
        assert "light.living_room" in result[0].text
        assert "switch.kitchen" not in result[0].text

    async def test_list_entities_entity_without_friendly_name(self, server, mock_hass):
        """Test _list_entities handles entities without friendly_name."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.temperature"
        mock_state.state = "22.5"
        mock_state.attributes = {}

        mock_hass.states.async_all.return_value = [mock_state]

        result = await server.tools._list_entities({})

        assert len(result) == 1
        assert "sensor.temperature" in result[0].text

    async def test_list_automations_returns_automations(self, server, mock_hass):
        """Test _list_automations returns all automations."""
        mock_automation1 = Mock()
        mock_automation1.entity_id = "automation.morning_routine"
        mock_automation1.state = "on"
        mock_automation1.attributes = {"friendly_name": "Morning Routine"}

        mock_automation2 = Mock()
        mock_automation2.entity_id = "automation.evening_routine"
        mock_automation2.state = "off"
        mock_automation2.attributes = {"friendly_name": "Evening Routine"}

        mock_light = Mock()
        mock_light.entity_id = "light.living_room"
        mock_light.state = "on"
        mock_light.attributes = {"friendly_name": "Living Room Light"}

        mock_hass.states.async_all.return_value = [
            mock_automation1,
            mock_automation2,
            mock_light,
        ]

        result = await server.tools._list_automations({})

        assert len(result) == 1
        assert "automation.morning_routine" in result[0].text
        assert "automation.evening_routine" in result[0].text
        assert "light.living_room" not in result[0].text

    async def test_list_automations_empty(self, server, mock_hass):
        """Test _list_automations with no automations."""
        mock_light = Mock()
        mock_light.entity_id = "light.living_room"
        mock_light.state = "on"
        mock_light.attributes = {"friendly_name": "Living Room Light"}

        mock_hass.states.async_all.return_value = [mock_light]

        result = await server.tools._list_automations({})

        assert len(result) == 1
        assert "[]" in result[0].text

    async def test_list_automations_without_friendly_name(self, server, mock_hass):
        """Test _list_automations handles automations without friendly_name."""
        mock_automation = Mock()
        mock_automation.entity_id = "automation.test"
        mock_automation.state = "on"
        mock_automation.attributes = {}

        mock_hass.states.async_all.return_value = [mock_automation]

        result = await server.tools._list_automations({})

        assert len(result) == 1
        assert "automation.test" in result[0].text

    async def test_get_automation_config_returns_yaml(self, server, mock_hass):
        """Test _get_automation_config returns YAML format."""
        mock_entity = Mock()
        mock_entity.raw_config = {
            "alias": "Morning Routine",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [{"service": "light.turn_on"}],
        }

        mock_component = Mock()
        mock_component.get_entity.return_value = mock_entity

        from homeassistant.components.automation import DATA_COMPONENT

        mock_hass.data = {DATA_COMPONENT: mock_component}

        result = await server.tools._get_automation_config(
            {"automation_id": "automation.morning_routine"}
        )

        assert len(result) == 1
        assert "Morning Routine" in result[0].text
        assert "alias:" in result[0].text
        assert "{" not in result[0].text
        mock_component.get_entity.assert_called_once_with("automation.morning_routine")

    async def test_get_automation_config_not_found(self, server, mock_hass):
        """Test _get_automation_config when automation doesn't exist."""
        mock_component = Mock()
        mock_component.get_entity.return_value = None

        from homeassistant.components.automation import DATA_COMPONENT

        mock_hass.data = {DATA_COMPONENT: mock_component}

        result = await server.tools._get_automation_config(
            {"automation_id": "automation.nonexistent"}
        )

        assert len(result) == 1
        assert "not found" in result[0].text

    async def test_get_automation_config_component_not_available(self, server, mock_hass):
        """Test _get_automation_config when automation component is not loaded."""
        mock_hass.data = {}

        result = await server.tools._get_automation_config({"automation_id": "automation.test"})

        assert len(result) == 1
        assert "not available" in result[0].text

    async def test_update_automation_config_writes_yaml_and_reloads(
        self, server, mock_hass, tmp_path
    ):
        """Test _update_automation_config parses YAML, writes file, and reloads."""
        automations_file = tmp_path / "automations.yaml"
        automations_file.write_text(
            "- id: morning_lights\n  alias: Old Name\n  trigger: []\n  action: []\n"
        )

        mock_hass.config.path = Mock(return_value=str(automations_file))
        mock_hass.services.async_call = AsyncMock()

        new_yaml = "id: morning_lights\nalias: New Name\ntrigger: []\naction: []\n"

        result = await server.tools._update_automation_config(
            {"automation_id": "automation.morning_lights", "config": new_yaml}
        )

        assert len(result) == 1
        assert "Successfully updated" in result[0].text
        written = automations_file.read_text()
        assert "New Name" in written
        mock_hass.services.async_call.assert_called_once_with(
            "automation", "reload", {}, blocking=True
        )

    async def test_update_automation_config_invalid_yaml(self, server, mock_hass):
        """Test _update_automation_config returns error on invalid YAML."""
        result = await server.tools._update_automation_config(
            {"automation_id": "automation.test", "config": ": invalid: yaml: ["}
        )

        assert len(result) == 1
        assert "Invalid YAML" in result[0].text

    async def test_update_automation_config_not_found(self, server, mock_hass, tmp_path):
        """Test _update_automation_config when automation ID doesn't exist in file."""
        automations_file = tmp_path / "automations.yaml"
        automations_file.write_text("- id: other_automation\n  alias: Other\n")

        mock_hass.config.path = Mock(return_value=str(automations_file))

        result = await server.tools._update_automation_config(
            {"automation_id": "automation.nonexistent", "config": "alias: Test\n"}
        )

        assert len(result) == 1
        assert "not found" in result[0].text

    async def test_create_automation_appends_and_reloads(self, server, mock_hass, tmp_path):
        """Test _create_automation appends to file and reloads."""
        automations_file = tmp_path / "automations.yaml"
        automations_file.write_text("- id: existing\n  alias: Existing\n")

        mock_hass.config.path = Mock(return_value=str(automations_file))
        mock_hass.services.async_call = AsyncMock()

        new_yaml = "alias: Night Mode\ntrigger:\n- platform: time\n  at: '23:00:00'\naction:\n- service: light.turn_off\n"

        result = await server.tools._create_automation({"config": new_yaml})

        assert len(result) == 1
        assert "Successfully created" in result[0].text
        assert "automation.night_mode" in result[0].text
        written = automations_file.read_text()
        assert "Night Mode" in written
        assert "Existing" in written
        mock_hass.services.async_call.assert_called_once_with(
            "automation", "reload", {}, blocking=True
        )

    async def test_create_automation_generates_id_from_alias(self, server, mock_hass, tmp_path):
        """Test _create_automation generates id from alias when not provided."""
        automations_file = tmp_path / "automations.yaml"
        automations_file.write_text("")

        mock_hass.config.path = Mock(return_value=str(automations_file))
        mock_hass.services.async_call = AsyncMock()

        result = await server.tools._create_automation(
            {"config": "alias: Morning Coffee\ntrigger: []\naction: []\n"}
        )

        assert "automation.morning_coffee" in result[0].text

    async def test_create_automation_rejects_duplicate_id(self, server, mock_hass, tmp_path):
        """Test _create_automation rejects automation with duplicate id."""
        automations_file = tmp_path / "automations.yaml"
        automations_file.write_text("- id: my_auto\n  alias: Existing\n")

        mock_hass.config.path = Mock(return_value=str(automations_file))

        result = await server.tools._create_automation(
            {"config": "id: my_auto\nalias: Duplicate\ntrigger: []\naction: []\n"}
        )

        assert "already exists" in result[0].text

    async def test_create_automation_requires_alias(self, server, mock_hass):
        """Test _create_automation returns error when alias is missing."""
        result = await server.tools._create_automation({"config": "trigger: []\naction: []\n"})

        assert "alias" in result[0].text

    async def test_create_automation_invalid_yaml(self, server, mock_hass):
        """Test _create_automation returns error on invalid YAML."""
        result = await server.tools._create_automation({"config": ": bad: [yaml"})

        assert "Invalid YAML" in result[0].text

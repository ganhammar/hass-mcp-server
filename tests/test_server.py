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

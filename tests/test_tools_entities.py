"""Tests for entity-related tools."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView
from custom_components.mcp_server_http_transport.tools import entities as entities_mod


class TestGetAliasesCompat:
    """Test _get_aliases backward compatibility helper."""

    def test_uses_async_get_entity_aliases_when_available(self):
        """Test _get_aliases uses er.async_get_entity_aliases on HA 2026.4+."""
        mock_hass = Mock()
        mock_entry = Mock()
        mock_entry.aliases = ["Test Alias"]
        with (
            patch.object(entities_mod, "_HAS_GET_ENTITY_ALIASES", True),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get_entity_aliases",
                return_value=["Resolved Alias"],
                create=True,
            ) as mock_fn,
        ):
            result = entities_mod._get_aliases(mock_hass, mock_entry)
        assert result == ["Resolved Alias"]
        mock_fn.assert_called_once_with(mock_hass, mock_entry)

    def test_falls_back_to_sorted_str_on_older_ha(self):
        """Test _get_aliases falls back to sorted(str(a) ...) on older HA."""
        mock_hass = Mock()
        mock_entry = Mock()
        mock_entry.aliases = ["Bravo", "Alpha"]
        with patch.object(entities_mod, "_HAS_GET_ENTITY_ALIASES", False):
            result = entities_mod._get_aliases(mock_hass, mock_entry)
        assert result == ["Alpha", "Bravo"]


class TestToolsEntities:
    """Test entity-related tools."""

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

    async def test_post_tools_call_get_state(self, view, mock_hass):
        """Test POST with tools/call for get_state."""
        # Setup mock state
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {"brightness": 255}
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.get.return_value = mock_state

        mock_entry = Mock()
        mock_entry.aliases = ["Lounge Light"]
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_state", "arguments": {"entity_id": "light.living_room"}},
                "id": 3,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert "result" in body
        assert "content" in body["result"]
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["aliases"] == ["Lounge Light"]

    async def test_post_tools_call_get_state_not_found(self, view, mock_hass):
        """Test POST with tools/call for non-existent entity."""
        mock_hass.states.get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_state",
                    "arguments": {"entity_id": "light.nonexistent"},
                },
                "id": 4,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        content_text = body["result"]["content"][0]["text"]
        assert "not found" in content_text

    async def test_post_tools_call_service(self, view, mock_hass):
        """Test POST with tools/call for call_service."""
        mock_hass.services.async_call = AsyncMock()

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {
                        "domain": "light",
                        "service": "turn_on",
                        "entity_id": "light.living_room",
                        "data": {"brightness": 255},
                    },
                },
                "id": 5,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully called" in body["result"]["content"][0]["text"]
        mock_hass.services.async_call.assert_called_once_with(
            "light",
            "turn_on",
            {"brightness": 255, "entity_id": "light.living_room"},
            blocking=True,
        )

    async def test_post_tools_call_service_error(self, view, mock_hass):
        """Test POST with tools/call for call_service that fails."""
        mock_hass.services.async_call = AsyncMock(side_effect=Exception("Service error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {"domain": "light", "service": "turn_on"},
                },
                "id": 6,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error calling service" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_list_entities(self, view, mock_hass):
        """Test POST with tools/call for list_entities."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_state2.state = "off"
        mock_state2.attributes = {"friendly_name": "Kitchen Switch"}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        mock_er = Mock()
        mock_er.async_get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_entities", "arguments": {}},
                "id": 7,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        entities = json.loads(body["result"]["content"][0]["text"])
        assert len(entities) == 2
        assert entities[0]["entity_id"] == "light.living_room"
        assert entities[0]["aliases"] == []
        assert entities[1]["entity_id"] == "switch.kitchen"

    async def test_post_tools_call_list_entities_with_domain_filter(self, view, mock_hass):
        """Test POST with tools/call for list_entities with domain filter."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_state2.state = "off"
        mock_state2.attributes = {}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        mock_er = Mock()
        mock_er.async_get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_entities", "arguments": {"domain": "light"}},
                "id": 8,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        entities = json.loads(body["result"]["content"][0]["text"])
        assert len(entities) == 1
        assert entities[0]["entity_id"] == "light.living_room"

    async def test_post_tools_call_list_areas(self, view, mock_hass):
        """Test POST with tools/call for list_areas."""
        mock_area1 = Mock()
        mock_area1.id = "living_room"
        mock_area1.name = "Living Room"
        mock_area1.floor_id = "ground_floor"

        mock_area2 = Mock()
        mock_area2.id = "kitchen"
        mock_area2.name = "Kitchen"
        mock_area2.floor_id = "ground_floor"

        mock_registry = Mock()
        mock_registry.async_list_areas.return_value = [mock_area1, mock_area2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_areas", "arguments": {}},
                "id": 12,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.ar.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        areas = json.loads(body["result"]["content"][0]["text"])
        assert len(areas) == 2
        assert areas[0]["id"] == "living_room"
        assert areas[0]["name"] == "Living Room"
        assert areas[1]["id"] == "kitchen"

    async def test_post_tools_call_list_devices(self, view, mock_hass):
        """Test POST with tools/call for list_devices."""
        mock_device1 = Mock()
        mock_device1.id = "device1"
        mock_device1.name = "Living Room Lamp"
        mock_device1.manufacturer = "IKEA"
        mock_device1.model = "TRADFRI"
        mock_device1.area_id = "living_room"
        mock_device1.name_by_user = None

        mock_device2 = Mock()
        mock_device2.id = "device2"
        mock_device2.name = "Kitchen Sensor"
        mock_device2.manufacturer = "Aqara"
        mock_device2.model = "WSDCGQ11LM"
        mock_device2.area_id = "kitchen"
        mock_device2.name_by_user = None

        mock_registry = Mock()
        mock_registry.devices = {"device1": mock_device1, "device2": mock_device2}

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_devices", "arguments": {}},
                "id": 13,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        devices = json.loads(body["result"]["content"][0]["text"])
        assert len(devices) == 2
        assert devices[0]["name"] == "Living Room Lamp"
        assert devices[1]["manufacturer"] == "Aqara"

    async def test_post_tools_call_list_devices_with_area_filter(self, view, mock_hass):
        """Test POST with tools/call for list_devices with area filter."""
        mock_device1 = Mock()
        mock_device1.id = "device1"
        mock_device1.name = "Living Room Lamp"
        mock_device1.manufacturer = "IKEA"
        mock_device1.model = "TRADFRI"
        mock_device1.area_id = "living_room"
        mock_device1.name_by_user = None

        mock_device2 = Mock()
        mock_device2.id = "device2"
        mock_device2.name = "Kitchen Sensor"
        mock_device2.manufacturer = "Aqara"
        mock_device2.model = "WSDCGQ11LM"
        mock_device2.area_id = "kitchen"
        mock_device2.name_by_user = None

        mock_registry = Mock()
        mock_registry.devices = {"device1": mock_device1, "device2": mock_device2}

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "list_devices",
                    "arguments": {"area_id": "living_room"},
                },
                "id": 14,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        devices = json.loads(body["result"]["content"][0]["text"])
        assert len(devices) == 1
        assert devices[0]["name"] == "Living Room Lamp"

    async def test_post_tools_call_get_device_details_multi_domain(self, view, mock_hass):
        """Test get_device_details returns entities across domains with states."""
        mock_device = Mock()
        mock_device.id = "device1"
        mock_device.name = "Xiaomi Robot Vacuum X10"
        mock_device.manufacturer = "Xiaomi"
        mock_device.model = "X10"
        mock_device.area_id = "living_room"
        mock_device.name_by_user = None
        mock_dr = Mock()
        mock_dr.async_get.return_value = mock_device

        vacuum_entry = Mock()
        vacuum_entry.entity_id = "vacuum.xiaomi_robot_vacuum_x10"
        vacuum_entry.name = None
        vacuum_entry.original_name = "Xiaomi Robot Vacuum X10"
        vacuum_entry.disabled_by = None

        sensor_entry = Mock()
        sensor_entry.entity_id = "sensor.xiaomi_robot_vacuum_x10_current_room"
        sensor_entry.name = None
        sensor_entry.original_name = "Current Room"
        sensor_entry.disabled_by = None

        mock_er = Mock()

        vacuum_state = Mock()
        vacuum_state.state = "docked"
        vacuum_state.attributes = {"friendly_name": "Xiaomi Robot Vacuum X10"}
        sensor_state = Mock()
        sensor_state.state = "Dining Room"
        sensor_state.attributes = {"friendly_name": "Current Room"}
        mock_hass.states.get.side_effect = lambda eid: {
            "vacuum.xiaomi_robot_vacuum_x10": vacuum_state,
            "sensor.xiaomi_robot_vacuum_x10_current_room": sensor_state,
        }.get(eid)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_device_details", "arguments": {"device_id": "device1"}},
                "id": 101,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_dr,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "er.async_entries_for_device",
                return_value=[vacuum_entry, sensor_entry],
            ) as mock_entries,
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["device"]["name"] == "Xiaomi Robot Vacuum X10"
        assert data["device"]["manufacturer"] == "Xiaomi"
        domains = sorted(e["domain"] for e in data["entities"])
        assert domains == ["sensor", "vacuum"]
        current_room = next(e for e in data["entities"] if e["entity_id"].endswith("current_room"))
        assert current_room["state"] == "Dining Room"
        assert current_room["name"] == "Current Room"
        # Disabled entities excluded by default
        _, kwargs = mock_entries.call_args
        assert kwargs["include_disabled_entities"] is False

    async def test_post_tools_call_get_device_details_not_found(self, view, mock_hass):
        """Test get_device_details returns an error for an unknown device."""
        mock_dr = Mock()
        mock_dr.async_get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_device_details", "arguments": {"device_id": "nope"}},
                "id": 102,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "not found" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_get_device_details_no_states(self, view, mock_hass):
        """Test get_device_details omits states when include_states is false."""
        mock_device = Mock()
        mock_device.id = "device1"
        mock_device.name = "Pet Feeder"
        mock_device.manufacturer = "Acme"
        mock_device.model = "PF1"
        mock_device.area_id = None
        mock_device.name_by_user = None
        mock_dr = Mock()
        mock_dr.async_get.return_value = mock_device

        entry = Mock()
        entry.entity_id = "switch.pet_feeder_dispense"
        entry.name = "Dispense"
        entry.original_name = "Dispense"
        entry.disabled_by = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_device_details",
                    "arguments": {"device_id": "device1", "include_states": False},
                },
                "id": 103,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_dr,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=Mock(),
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "er.async_entries_for_device",
                return_value=[entry],
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["entities"][0]["entity_id"] == "switch.pet_feeder_dispense"
        assert "state" not in data["entities"][0]
        assert data["entities"][0]["name"] == "Dispense"

    async def test_post_tools_call_get_device_details_entity_without_state(self, view, mock_hass):
        """Test get_device_details reports state=None for an entity with no current state."""
        mock_device = Mock()
        mock_device.id = "device1"
        mock_device.name = "Pet Feeder"
        mock_device.manufacturer = "Acme"
        mock_device.model = "PF1"
        mock_device.area_id = None
        mock_device.name_by_user = None
        mock_dr = Mock()
        mock_dr.async_get.return_value = mock_device

        entry = Mock()
        entry.entity_id = "switch.pet_feeder_dispense"
        entry.name = None
        entry.original_name = "Dispense"
        entry.disabled_by = None

        # The registered entity has no state in the machine (e.g. unavailable or not loaded).
        mock_hass.states.get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_device_details", "arguments": {"device_id": "device1"}},
                "id": 104,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_dr,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=Mock(),
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "er.async_entries_for_device",
                return_value=[entry],
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["entities"][0]["entity_id"] == "switch.pet_feeder_dispense"
        assert data["entities"][0]["state"] is None
        # With no state to override it, the name falls back to the registry original_name.
        assert data["entities"][0]["name"] == "Dispense"

    async def test_post_tools_call_list_services(self, view, mock_hass):
        """Test POST with tools/call for list_services."""
        mock_hass.services.async_services.return_value = {
            "light": {"turn_on": Mock(), "turn_off": Mock(), "toggle": Mock()},
            "switch": {"turn_on": Mock(), "turn_off": Mock()},
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_services", "arguments": {}},
                "id": 15,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        services = json.loads(body["result"]["content"][0]["text"])
        assert "light" in services
        assert "turn_on" in services["light"]
        assert "turn_off" in services["light"]
        assert "switch" in services

    async def test_post_tools_call_list_services_with_domain_filter(self, view, mock_hass):
        """Test POST with tools/call for list_services with domain filter."""
        mock_hass.services.async_services.return_value = {
            "light": {"turn_on": Mock(), "turn_off": Mock()},
            "switch": {"turn_on": Mock(), "turn_off": Mock()},
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_services", "arguments": {"domain": "light"}},
                "id": 16,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        services = json.loads(body["result"]["content"][0]["text"])
        assert "light" in services
        assert "switch" not in services

    async def test_post_tools_call_describe_service_single(self, view, mock_hass):
        """Test describe_service returns one service's field schema."""
        descriptions = {
            "vacuum": {
                "clean_area": {
                    "name": "Clean area",
                    "description": "Clean selected mapped areas.",
                    "fields": {
                        "cleaning_area_id": {
                            "required": True,
                            "selector": {"text": {"multiple": True}},
                        }
                    },
                },
                "start": {"name": "Start", "fields": {}},
            }
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "describe_service",
                    "arguments": {"domain": "vacuum", "service": "clean_area"},
                },
                "id": 110,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value=descriptions),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert list(data.keys()) == ["clean_area"]
        assert "cleaning_area_id" in data["clean_area"]["fields"]
        assert data["clean_area"]["fields"]["cleaning_area_id"]["required"] is True

    async def test_post_tools_call_describe_service_whole_domain(self, view, mock_hass):
        """Test describe_service returns every service in a domain when service is omitted."""
        descriptions = {
            "vacuum": {
                "clean_area": {"name": "Clean area", "fields": {}},
                "start": {"name": "Start", "fields": {}},
            }
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "describe_service", "arguments": {"domain": "vacuum"}},
                "id": 111,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value=descriptions),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert sorted(data.keys()) == ["clean_area", "start"]

    async def test_post_tools_call_describe_service_unknown_domain(self, view, mock_hass):
        """Test describe_service reports when a domain has no services."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "describe_service", "arguments": {"domain": "nope"}},
                "id": 112,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value={"vacuum": {"start": {}}}),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "No services found" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_describe_service_unknown_service(self, view, mock_hass):
        """Test describe_service reports when a service does not exist in the domain."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "describe_service",
                    "arguments": {"domain": "vacuum", "service": "nope"},
                },
                "id": 113,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value={"vacuum": {"start": {}}}),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "not found" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_search_entities_by_query(self, view, mock_hass):
        """Test search_entities matches by friendly name."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temperature"
        mock_state2.state = "22.5"
        mock_state2.attributes = {"friendly_name": "Temperature Sensor"}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_entry.area_id = None
        mock_entry.device_id = None
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_entities",
                    "arguments": {"query": "living"},
                },
                "id": 82,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "light.living_room"

    async def test_post_tools_call_search_entities_no_params(self, view, mock_hass):
        """Test search_entities requires at least one parameter."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "search_entities", "arguments": {}},
                "id": 83,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_search_entities_by_device_class(self, view, mock_hass):
        """Test search_entities filters by device_class."""
        mock_state1 = Mock()
        mock_state1.entity_id = "sensor.temp"
        mock_state1.state = "22"
        mock_state1.attributes = {
            "friendly_name": "Temp",
            "device_class": "temperature",
        }

        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.humidity"
        mock_state2.state = "45"
        mock_state2.attributes = {
            "friendly_name": "Humidity",
            "device_class": "humidity",
        }

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_entry.area_id = None
        mock_entry.device_id = None
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_entities",
                    "arguments": {"device_class": "temperature"},
                },
                "id": 84,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "sensor.temp"

    async def test_post_tools_call_search_entities_by_area(self, view, mock_hass):
        """Test search_entities filters by area_id."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "light.bedroom"
        mock_state2.state = "off"
        mock_state2.attributes = {"friendly_name": "Bedroom Light"}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        entry1 = Mock()
        entry1.aliases = []
        entry1.area_id = "living_room"
        entry1.device_id = None

        entry2 = Mock()
        entry2.aliases = []
        entry2.area_id = "bedroom"
        entry2.device_id = None

        entries = {"light.living": entry1, "light.bedroom": entry2}
        mock_er = Mock()
        mock_er.async_get = Mock(side_effect=lambda eid: entries.get(eid))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_entities",
                    "arguments": {"area_id": "living_room"},
                },
                "id": 98,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "light.living"

    async def test_post_tools_call_search_entities_area_via_device(self, view, mock_hass):
        """Test search_entities resolves area via device registry."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.temp"
        mock_state.state = "22"
        mock_state.attributes = {"friendly_name": "Temp"}

        mock_hass.states.async_all.return_value = [mock_state]

        entry = Mock()
        entry.aliases = []
        entry.area_id = None
        entry.device_id = "device_1"
        mock_er = Mock()
        mock_er.async_get.return_value = entry

        mock_device = Mock()
        mock_device.area_id = "kitchen"
        mock_dr = Mock()
        mock_dr.async_get.return_value = mock_device

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_entities",
                    "arguments": {"area_id": "kitchen"},
                },
                "id": 99,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "sensor.temp"
        assert data[0]["area_id"] == "kitchen"

    async def test_post_tools_call_search_entities_limit(self, view, mock_hass):
        """Test search_entities respects the limit parameter."""
        states = []
        for i in range(10):
            s = Mock()
            s.entity_id = f"light.light_{i}"
            s.state = "on"
            s.attributes = {"friendly_name": f"Light {i}"}
            states.append(s)
        mock_hass.states.async_all.return_value = states

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_entry.area_id = None
        mock_entry.device_id = None
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_entities",
                    "arguments": {"domain": "light", "limit": 3},
                },
                "id": 101,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 3

    async def test_post_tools_call_search_entities_domain_filter(self, view, mock_hass):
        """Test search_entities with domain filter skips non-matching entities."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Light"}

        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temp"
        mock_state2.state = "22"
        mock_state2.attributes = {"friendly_name": "Temp"}

        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_entry.area_id = None
        mock_entry.device_id = None
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_entities",
                    "arguments": {"domain": "sensor"},
                },
                "id": 100,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "sensor.temp"

    async def test_post_tools_call_list_labels(self, view, mock_hass):
        """Test POST with tools/call for list_labels."""
        mock_label1 = Mock()
        mock_label1.label_id = "important"
        mock_label1.name = "Important"
        mock_label1.color = "red"
        mock_label1.icon = "mdi:star"
        mock_label1.description = "Important items"

        mock_label2 = Mock()
        mock_label2.label_id = "outdoor"
        mock_label2.name = "Outdoor"
        mock_label2.color = "green"
        mock_label2.icon = "mdi:tree"
        mock_label2.description = "Outdoor devices"

        mock_registry = Mock()
        mock_registry.async_list_labels.return_value = [mock_label1, mock_label2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_labels", "arguments": {}},
                "id": 214,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.lr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["label_id"] == "important"
        assert data[0]["name"] == "Important"
        assert data[1]["label_id"] == "outdoor"

    async def test_post_tools_call_list_labels_empty(self, view, mock_hass):
        """Test POST with tools/call for list_labels with no labels."""
        mock_registry = Mock()
        mock_registry.async_list_labels.return_value = []

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_labels", "arguments": {}},
                "id": 215,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.lr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data == []

    async def test_post_tools_call_batch_get_state(self, view, mock_hass):
        """Test POST with tools/call for batch_get_state."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"brightness": 255}
        mock_state1.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state1.last_updated = datetime(2024, 1, 1, 12, 0, 0)

        mock_state2 = Mock()
        mock_state2.entity_id = "switch.kitchen"
        mock_state2.state = "off"
        mock_state2.attributes = {"friendly_name": "Kitchen Switch"}
        mock_state2.last_changed = datetime(2024, 1, 1, 10, 0, 0)
        mock_state2.last_updated = datetime(2024, 1, 1, 10, 0, 0)

        def mock_get(entity_id):
            return {
                "light.living_room": mock_state1,
                "switch.kitchen": mock_state2,
            }.get(entity_id)

        mock_hass.states.get = mock_get

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "batch_get_state",
                    "arguments": {
                        "entity_ids": ["light.living_room", "switch.kitchen"],
                    },
                },
                "id": 216,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["entity_id"] == "light.living_room"
        assert data[0]["state"] == "on"
        assert data[1]["entity_id"] == "switch.kitchen"
        assert data[1]["state"] == "off"

    async def test_post_tools_call_batch_get_state_mixed(self, view, mock_hass):
        """Test POST with tools/call for batch_get_state with mixed found/not-found."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {"brightness": 255}
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)

        def mock_get(entity_id):
            if entity_id == "light.living_room":
                return mock_state
            return None

        mock_hass.states.get = mock_get

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "batch_get_state",
                    "arguments": {
                        "entity_ids": ["light.living_room", "light.nonexistent"],
                    },
                },
                "id": 217,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["entity_id"] == "light.living_room"
        assert "state" in data[0]
        assert data[1]["entity_id"] == "light.nonexistent"
        assert data[1]["error"] == "not found"

    async def test_post_tools_call_batch_get_state_with_set_attribute(self, view, mock_hass):
        """Regression for PR #38: batch_get_state must serialize set attributes.

        Hue Bridge Pro groups expose `hue_scenes` as a Python set. Before the
        encoder handled sets, this path raised TypeError and returned HTTP 500.
        """
        mock_state = Mock()
        mock_state.entity_id = "light.hue_group"
        mock_state.state = "on"
        mock_state.attributes = {
            "is_hue_group": True,
            "hue_scenes": {"Entspannen", "Energie tanken", "Frühlingsblüten"},
        }
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)

        mock_hass.states.get = lambda entity_id: (
            mock_state if entity_id == "light.hue_group" else None
        )

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "batch_get_state",
                    "arguments": {"entity_ids": ["light.hue_group"]},
                },
                "id": 219,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "light.hue_group"
        assert data[0]["attributes"]["hue_scenes"] == [
            "Energie tanken",
            "Entspannen",
            "Frühlingsblüten",
        ]

    async def test_post_tools_call_batch_get_state_with_fields(self, view, mock_hass):
        """Test batch_get_state filters attributes per entity when fields is provided (#26)."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {
            "brightness": 255,
            "color_temp": 300,
            "friendly_name": "Living Room",
        }
        mock_state1.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state1.last_updated = datetime(2024, 1, 1, 12, 0, 0)

        mock_state2 = Mock()
        mock_state2.entity_id = "light.bedroom"
        mock_state2.state = "off"
        mock_state2.attributes = {
            "brightness": 0,
            "color_temp": 0,
            "friendly_name": "Bedroom",
        }
        mock_state2.last_changed = datetime(2024, 1, 1, 10, 0, 0)
        mock_state2.last_updated = datetime(2024, 1, 1, 10, 0, 0)

        mock_hass.states.get = lambda entity_id: {
            "light.living_room": mock_state1,
            "light.bedroom": mock_state2,
        }.get(entity_id)

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "batch_get_state",
                    "arguments": {
                        "entity_ids": ["light.living_room", "light.bedroom"],
                        "fields": ["brightness"],
                    },
                },
                "id": 220,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["attributes"] == {"brightness": 255}
        assert data[1]["attributes"] == {"brightness": 0}

    async def test_post_tools_call_batch_get_state_exceeds_limit(self, view, mock_hass):
        """Test POST with tools/call for batch_get_state exceeding 50 limit."""
        entity_ids = [f"light.light_{i}" for i in range(51)]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "batch_get_state",
                    "arguments": {"entity_ids": entity_ids},
                },
                "id": 218,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "maximum 50" in text

    async def test_post_tools_call_get_state_with_fields(self, view, mock_hass):
        """Test POST with tools/call for get_state with fields filter."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {
            "brightness": 255,
            "color_temp": 400,
            "friendly_name": "Living Room Light",
        }
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.get.return_value = mock_state

        mock_entry = Mock()
        mock_entry.aliases = []
        mock_er = Mock()
        mock_er.async_get.return_value = mock_entry

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_state",
                    "arguments": {
                        "entity_id": "light.living_room",
                        "fields": ["brightness"],
                    },
                },
                "id": 219,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert "brightness" in data["attributes"]
        assert "color_temp" not in data["attributes"]
        assert "friendly_name" not in data["attributes"]

    async def test_post_tools_call_list_entities_detailed(self, view, mock_hass):
        """Test POST with tools/call for list_entities with detailed=true."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {
            "brightness": 255,
            "friendly_name": "Living Room Light",
        }
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.async_all.return_value = [mock_state]

        mock_er = Mock()
        mock_er.async_get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "list_entities",
                    "arguments": {"detailed": True},
                },
                "id": 220,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert "attributes" in data[0]
        assert "last_changed" in data[0]
        assert "last_updated" in data[0]

    async def test_post_tools_call_list_entities_with_fields(self, view, mock_hass):
        """Test POST with tools/call for list_entities with fields filter."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {
            "brightness": 255,
            "friendly_name": "Living Room Light",
        }
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.async_all.return_value = [mock_state]

        mock_er = Mock()
        mock_er.async_get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "list_entities",
                    "arguments": {"fields": ["entity_id", "state"]},
                },
                "id": 221,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert "entity_id" in data[0]
        assert "state" in data[0]
        assert "attributes" not in data[0]
        assert "last_changed" not in data[0]

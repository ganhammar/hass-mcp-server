"""Tests for entity-related tools."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import SupportsResponse
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mcp_server_http_transport.http import Caller, MCPEndpointView
from custom_components.mcp_server_http_transport.tools import entities as entities_mod

# The view resolves every request to an authorized caller before dispatch; these
# tests start from one so they exercise the tool rather than the auth path.
CALLER = Caller(user_id="user123", auth_method="native", user=Mock(is_admin=True))


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
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.services = Mock()
        # Most services return nothing; the ones that do are set per test. Left
        # as a bare Mock this would read as "returns a response" and every call
        # would ask for one.
        hass.services.has_service = Mock(return_value=True)
        hass.services.supports_response = Mock(return_value=SupportsResponse.NONE)
        return hass

    @pytest.fixture
    def view(self, mock_hass):
        """Create an MCPEndpointView instance."""
        return MCPEndpointView(mock_hass)

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
            patch.object(view, "_authenticate", return_value=CALLER),
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
        assert "isError" not in body["result"]
        assert "content" in body["result"]
        text = body["result"]["content"][0]["text"]
        data = json.loads(text)
        assert data["aliases"] == ["Lounge Light"]
        # Tool text is compact JSON: no indentation or separator whitespace to pay tokens for.
        assert text == json.dumps(data, separators=(",", ":"))

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

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["isError"] is True
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

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        assert "Successfully called" in body["result"]["content"][0]["text"]
        mock_hass.services.async_call.assert_called_once_with(
            "light",
            "turn_on",
            {"brightness": 255, "entity_id": "light.living_room"},
            blocking=True,
            context=ANY,
            return_response=False,
        )
        # Home Assistant records the change as made by the calling user.
        context = mock_hass.services.async_call.call_args.kwargs["context"]
        assert context.user_id == CALLER.user_id

    async def test_post_tools_call_service_returns_response_data(self, view, mock_hass):
        """A service that only exists to return data must return it.

        Home Assistant refuses to run a SupportsResponse.ONLY service unless the
        caller asks for the response, so without this these services could not be
        called at all — not merely called without their answer.
        """
        mock_hass.services.supports_response = Mock(return_value=SupportsResponse.ONLY)
        mock_hass.services.async_call = AsyncMock(
            return_value={"calendar.family": {"events": [{"summary": "Dentist"}]}}
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {
                        "domain": "calendar",
                        "service": "get_events",
                        "entity_id": "calendar.family",
                    },
                },
                "id": 7,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        assert "Dentist" in body["result"]["content"][0]["text"]
        mock_hass.services.async_call.assert_called_once_with(
            "calendar",
            "get_events",
            {"entity_id": "calendar.family"},
            blocking=True,
            context=ANY,
            return_response=True,
        )

    async def test_post_tools_call_service_optional_response_without_data(self, view, mock_hass):
        """An OPTIONAL service that answers with nothing still reports success."""
        mock_hass.services.supports_response = Mock(return_value=SupportsResponse.OPTIONAL)
        mock_hass.services.async_call = AsyncMock(return_value=None)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {"domain": "conversation", "service": "process"},
                },
                "id": 8,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        assert "Successfully called" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_service_empty_response_reports_success(self, view, mock_hass):
        """A script with no response variable answers {} and still reports success.

        Scripts are SupportsResponse.OPTIONAL and script/__init__.py returns
        `response or {}`, so every ordinary script call would otherwise render
        as an empty dict instead of a confirmation.
        """
        mock_hass.services.supports_response = Mock(return_value=SupportsResponse.OPTIONAL)
        mock_hass.services.async_call = AsyncMock(return_value={})

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {"domain": "script", "service": "bedtime"},
                },
                "id": 9,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        assert body["result"]["content"][0]["text"] == "Successfully called script.bedtime"

    async def test_post_tools_call_service_unknown_service(self, view, mock_hass):
        """An unknown service reports itself as not found.

        Core's supports_response() subscripts the service registry directly and
        raises KeyError for anything it does not hold, which would replace the
        ServiceNotFound message with a bare "'light'" before async_call is ever
        reached. This is the common failure mode for a model guessing a name,
        so the useful part of the message has to survive.
        """
        registry = {"light": {"turn_on": SupportsResponse.NONE}}
        mock_hass.services.has_service = Mock(
            side_effect=lambda domain, service: service in registry.get(domain, {})
        )
        mock_hass.services.supports_response = Mock(
            side_effect=lambda domain, service: registry[domain][service]
        )
        # Stands in for ServiceNotFound, whose __str__ resolves a translation
        # against a live hass and so cannot be raised from a mock.
        mock_hass.services.async_call = AsyncMock(
            side_effect=Exception("Action light.no_such_service not found.")
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {"domain": "light", "service": "no_such_service"},
                },
                "id": 10,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        result = json.loads(response.body)["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "light.no_such_service" in text
        mock_hass.services.async_call.assert_called_once_with(
            "light",
            "no_such_service",
            {},
            blocking=True,
            context=ANY,
            return_response=False,
        )

    async def test_post_tools_call_service_unserializable_response(self, view, mock_hass):
        """A value the encoder does not know is stringified, not the whole response lost."""
        mock_hass.services.supports_response = Mock(return_value=SupportsResponse.ONLY)
        mock_hass.services.async_call = AsyncMock(return_value={"handle": object()})

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {"domain": "custom", "service": "read"},
                },
                "id": 11,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["handle"].startswith("<object object at ")

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

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["isError"] is True
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        entities = json.loads(body["result"]["content"][0]["text"])
        assert len(entities) == 1
        assert entities[0]["entity_id"] == "light.living_room"

    async def test_post_tools_call_list_areas(self, view, mock_hass):
        """Test POST with tools/call for list_areas."""
        mock_area1 = Mock()
        mock_area1.id = "living_room"
        mock_area1.name = "Living Room"
        mock_area1.floor_id = "ground_floor"
        mock_area1.icon = None
        mock_area1.labels = set()
        mock_area1.aliases = set()

        mock_area2 = Mock()
        mock_area2.id = "kitchen"
        mock_area2.name = "Kitchen"
        mock_area2.floor_id = "ground_floor"
        mock_area2.icon = None
        mock_area2.labels = set()
        mock_area2.aliases = set()

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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.ar.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
        mock_device1.labels = set()
        mock_device1.disabled_by = None

        mock_device2 = Mock()
        mock_device2.id = "device2"
        mock_device2.name = "Kitchen Sensor"
        mock_device2.manufacturer = "Aqara"
        mock_device2.model = "WSDCGQ11LM"
        mock_device2.area_id = "kitchen"
        mock_device2.name_by_user = None
        mock_device2.labels = set()
        mock_device2.disabled_by = None

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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
        mock_device1.labels = set()
        mock_device1.disabled_by = None

        mock_device2 = Mock()
        mock_device2.id = "device2"
        mock_device2.name = "Kitchen Sensor"
        mock_device2.manufacturer = "Aqara"
        mock_device2.model = "WSDCGQ11LM"
        mock_device2.area_id = "kitchen"
        mock_device2.name_by_user = None
        mock_device2.labels = set()
        mock_device2.disabled_by = None

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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
        mock_device.labels = set()
        mock_device.disabled_by = None
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
            patch.object(view, "_authenticate", return_value=CALLER),
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
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["isError"] is True
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
        mock_device.labels = set()
        mock_device.disabled_by = None
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
            patch.object(view, "_authenticate", return_value=CALLER),
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
        assert "isError" not in body["result"]
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
        mock_device.labels = set()
        mock_device.disabled_by = None
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
            patch.object(view, "_authenticate", return_value=CALLER),
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
        assert "isError" not in body["result"]
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

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value=descriptions),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value=descriptions),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value={"vacuum": {"start": {}}}),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["isError"] is True
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities."
                "async_get_all_descriptions",
                AsyncMock(return_value={"vacuum": {"start": {}}}),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["isError"] is True
        assert "not found" in body["result"]["content"][0]["text"]

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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.lr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.lr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["isError"] is True
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
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
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "isError" not in body["result"]
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert "entity_id" in data[0]
        assert "state" in data[0]
        assert "attributes" not in data[0]
        assert "last_changed" not in data[0]


def _search_payload(result: dict) -> list[dict]:
    assert "isError" not in result
    return json.loads(result["content"][0]["text"])


def _ids(result: dict) -> list[str]:
    return [entity["entity_id"] for entity in _search_payload(result)]


def _entity_aliases(*aliases: str):
    """Aliases in the type this core's entity registry stores them as."""
    return list(aliases) if entities_mod._HAS_GET_ENTITY_ALIASES else set(aliases)


@pytest.fixture
def home(hass):
    """A small home: an office upstairs and a kitchen, with lights, sensors and a lamp.

    The desk lamp has no area of its own and is found in the office through its
    device. The office is also called 'Kontor', the lamp 'Lampa'.
    """
    floors = fr.async_get(hass)
    upstairs = floors.async_create("Upstairs", aliases={"Övervåning"}, level=1)
    ground = floors.async_create("Ground", level=0)
    areas = ar.async_get(hass)
    office = areas.async_create("Office", floor_id=upstairs.floor_id, aliases={"Kontor"})
    kitchen = areas.async_create("Kitchen", floor_id=ground.floor_id)

    config_entry = MockConfigEntry(domain="demo")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("demo", "desk")},
        name="Desk Lamp",
    )
    dr.async_get(hass).async_update_device(device.id, area_id=office.id)

    registry = er.async_get(hass)

    def add(entity_id, state, name, *, area=None, device_id=None, attributes=None, **entry):
        domain, object_id = entity_id.split(".")
        created = registry.async_get_or_create(
            domain,
            "demo",
            object_id,
            suggested_object_id=object_id,
            config_entry=config_entry,
            device_id=device_id,
            original_name=name,
            **entry,
        )
        assert created.entity_id == entity_id
        if area is not None:
            registry.async_update_entity(entity_id, area_id=area)
        hass.states.async_set(entity_id, state, {"friendly_name": name, **(attributes or {})})

    add("light.office_ceiling", "on", "Ceiling Light", area=office.id)
    add("light.desk", "off", "Desk Lamp", device_id=device.id)
    registry.async_update_entity("light.desk", aliases=_entity_aliases("Lampa"))
    add("switch.desk_lamp_plug", "on", "Desk Lamp Plug", device_id=device.id)
    add(
        "sensor.desk_lamp_signal",
        "-60",
        "Desk Lamp Signal",
        device_id=device.id,
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    add(
        "sensor.office_light_level",
        "320",
        "Office Light Level",
        area=office.id,
        attributes={"device_class": "illuminance", "unit_of_measurement": "lx"},
    )
    add(
        "sensor.office_temperature",
        "21.5",
        "Office Temperature",
        area=office.id,
        attributes={"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    add(
        "switch.office_fan",
        "off",
        "Office Fan",
        area=office.id,
        hidden_by=er.RegistryEntryHider.USER,
    )
    add("light.kitchen", "on", "Kitchen Light", area=kitchen.id)
    # An entity Home Assistant knows only from its state, with no registry entry.
    hass.states.async_set("sun.sun", "above_horizon", {"friendly_name": "Sun"})
    return SimpleNamespace(office=office.id, kitchen=kitchen.id)


class TestSearchEntities:
    """search_entities against real registries, as one plain-words call answers a question."""

    @pytest.mark.parametrize("query", ["office light", "lights office", "OFFICE LIGHTS"])
    async def test_words_match_in_any_order_across_area_and_domain(self, hass, home, query):
        ids = _ids(await entities_mod.search_entities(hass, {"query": query}))

        # The lights the query names come first; the lamp is in the office only
        # through its device.
        assert ids == ["light.desk", "light.office_ceiling", "sensor.office_light_level"]

    async def test_area_and_entity_aliases_match(self, hass, home):
        ids = _ids(await entities_mod.search_entities(hass, {"query": "kontor lampa"}))

        assert ids == ["light.desk"]

    async def test_floor_name_and_alias_match(self, hass, home):
        by_name = await entities_mod.search_entities(hass, {"query": "upstairs temperature"})
        by_alias = await entities_mod.search_entities(hass, {"query": "övervåning temperature"})

        assert _ids(by_name) == _ids(by_alias) == ["sensor.office_temperature"]

    async def test_device_name_matches(self, hass, home):
        ids = _ids(await entities_mod.search_entities(hass, {"query": "desk lamp signal"}))

        assert ids == ["sensor.desk_lamp_signal"]

    async def test_every_word_must_match(self, hass, home):
        result = await entities_mod.search_entities(hass, {"query": "kitchen temperature"})

        # Nothing matching is a listing that found nothing, not an error.
        assert _search_payload(result) == []

    async def test_an_exact_name_comes_first_and_diagnostics_last(self, hass, home):
        ids = _ids(await entities_mod.search_entities(hass, {"query": "desk lamp"}))

        assert ids == ["light.desk", "switch.desk_lamp_plug", "sensor.desk_lamp_signal"]

    async def test_hidden_and_diagnostic_entities_come_after_the_rest(self, hass, home):
        ids = _ids(await entities_mod.search_entities(hass, {"area_id": home.office}))

        assert ids[-2:] == ["sensor.desk_lamp_signal", "switch.office_fan"]
        assert set(ids[:-2]) == {
            "light.desk",
            "light.office_ceiling",
            "sensor.office_light_level",
            "sensor.office_temperature",
            "switch.desk_lamp_plug",
        }

    async def test_result_carries_state_area_name_and_unit(self, hass, home):
        result = await entities_mod.search_entities(hass, {"query": "office temperature"})

        assert _search_payload(result) == [
            {
                "entity_id": "sensor.office_temperature",
                "state": "21.5",
                "friendly_name": "Office Temperature",
                "device_class": "temperature",
                "area_id": home.office,
                "area": "Office",
                "aliases": [],
                "unit_of_measurement": "°C",
            }
        ]

    async def test_an_entity_without_a_registry_entry_is_found(self, hass, home):
        result = await entities_mod.search_entities(hass, {"query": "sun"})

        assert _search_payload(result) == [
            {
                "entity_id": "sun.sun",
                "state": "above_horizon",
                "friendly_name": "Sun",
                "device_class": None,
                "area_id": None,
                "area": None,
                "aliases": [],
            }
        ]

    @pytest.mark.parametrize("area", ["office", "Office", "kontor", "KONTOR"])
    async def test_area_filter_takes_a_name_or_alias(self, hass, home, area):
        by_id = _ids(await entities_mod.search_entities(hass, {"area_id": home.office}))
        by_name = _ids(await entities_mod.search_entities(hass, {"area_id": area}))

        assert by_name == by_id
        assert "light.desk" in by_id
        assert "light.kitchen" not in by_id

    async def test_area_filter_with_a_query(self, hass, home):
        result = await entities_mod.search_entities(hass, {"query": "light", "area_id": "Kitchen"})

        assert _ids(result) == ["light.kitchen"]

    async def test_unknown_area_is_an_error(self, hass, home):
        result = await entities_mod.search_entities(hass, {"area_id": "Attic"})

        assert result["isError"] is True
        assert "Attic" in result["content"][0]["text"]
        assert "list_areas" in result["content"][0]["text"]

    async def test_domain_and_device_class_filters(self, hass, home):
        lights = _ids(await entities_mod.search_entities(hass, {"domain": "light"}))
        temperature = await entities_mod.search_entities(hass, {"device_class": "temperature"})

        assert lights == ["light.desk", "light.kitchen", "light.office_ceiling"]
        assert _ids(temperature) == ["sensor.office_temperature"]

    async def test_a_cut_list_says_so(self, hass, home):
        result = await entities_mod.search_entities(hass, {"query": "office", "limit": 2})

        assert _ids(result) == ["light.desk", "light.office_ceiling"]
        assert result["content"][1]["text"] == (
            "Showing the best 2 of 7 matches. Add words or filters to narrow the search, "
            "or raise limit."
        )

    async def test_a_whole_list_says_nothing_more(self, hass, home):
        result = await entities_mod.search_entities(hass, {"query": "office", "limit": 7})

        assert len(_ids(result)) == 7
        assert len(result["content"]) == 1

    @pytest.mark.parametrize(
        "arguments",
        [{}, {"query": "   "}, {"query": None}],
    )
    async def test_something_to_search_by_is_required(self, hass, home, arguments):
        result = await entities_mod.search_entities(hass, arguments)

        assert result["isError"] is True
        assert "at least one search parameter" in result["content"][0]["text"]

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"query": ["office"]}, "query must be a string"),
            ({"query": "office", "limit": 0}, "limit must be a positive integer"),
            ({"query": "office", "limit": "5"}, "limit must be a positive integer"),
        ],
    )
    async def test_malformed_arguments_are_refused(self, hass, home, arguments, message):
        result = await entities_mod.search_entities(hass, arguments)

        assert result["isError"] is True
        assert message in result["content"][0]["text"]


class TestWordForms:
    @pytest.mark.parametrize(
        ("word", "forms"),
        [
            ("lights", ("lights", "light")),
            ("switches", ("switches", "switche", "switch")),
            ("gas", ("gas",)),
            ("lampa", ("lampa",)),
        ],
    )
    def test_a_plural_also_matches_without_its_ending(self, word, forms):
        assert entities_mod._word_forms(word) == forms

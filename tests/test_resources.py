"""Tests for MCP resource endpoints."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestResources:
    """Test the MCP resource endpoints."""

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

    async def test_post_resources_list(self, view):
        """Test POST with resources/list request."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={"jsonrpc": "2.0", "method": "resources/list", "id": 22}
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result = body["result"]
        assert len(result["resources"]) == 8
        resource_uris = [r["uri"] for r in result["resources"]]
        assert "hass://config" in resource_uris
        assert "hass://areas" in resource_uris
        assert "hass://devices" in resource_uris
        assert "hass://services" in resource_uris
        assert "hass://floors" in resource_uris
        assert "hass://entities" in resource_uris
        assert "hass://labels" in resource_uris
        assert "hass://integrations" in resource_uris
        assert len(result["resourceTemplates"]) == 3
        assert "entity_id" in result["resourceTemplates"][0]["uriTemplate"]

    async def test_post_resources_read_config(self, view, mock_hass):
        """Test POST with resources/read for hass://config."""
        mock_units = Mock()
        mock_units.as_dict.return_value = {"temperature": "°C"}
        mock_hass.config.location_name = "Home"
        mock_hass.config.latitude = 59.0
        mock_hass.config.longitude = 18.0
        mock_hass.config.elevation = 10
        mock_hass.config.units = mock_units
        mock_hass.config.time_zone = "Europe/Stockholm"
        mock_hass.config.currency = "SEK"
        mock_hass.config.country = "SE"
        mock_hass.config.language = "en"

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://config"},
                "id": 23,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        contents = body["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "hass://config"
        data = json.loads(contents[0]["text"])
        assert data["location_name"] == "Home"

    async def test_post_resources_read_areas(self, view, mock_hass):
        """Test POST with resources/read for hass://areas."""
        mock_area = Mock()
        mock_area.id = "living_room"
        mock_area.name = "Living Room"
        mock_area.floor_id = "ground_floor"

        mock_registry = Mock()
        mock_registry.async_list_areas.return_value = [mock_area]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://areas"},
                "id": 24,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.resources.ar.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        contents = body["result"]["contents"]
        areas = json.loads(contents[0]["text"])
        assert len(areas) == 1
        assert areas[0]["id"] == "living_room"

    async def test_post_resources_read_entity(self, view, mock_hass):
        """Test POST with resources/read for hass://entity/{entity_id}."""
        mock_state = Mock()
        mock_state.entity_id = "light.living_room"
        mock_state.state = "on"
        mock_state.attributes = {"brightness": 255}
        mock_state.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state.last_updated = datetime(2024, 1, 1, 12, 0, 0)
        mock_hass.states.get.return_value = mock_state

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://entity/light.living_room"},
                "id": 25,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        contents = body["result"]["contents"]
        data = json.loads(contents[0]["text"])
        assert data["entity_id"] == "light.living_room"
        assert data["state"] == "on"

    async def test_post_resources_read_entity_not_found(self, view, mock_hass):
        """Test POST with resources/read for nonexistent entity."""
        mock_hass.states.get.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://entity/light.nonexistent"},
                "id": 26,
            }
        )
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert "error" in body
        assert "not found" in body["error"]["message"]

    async def test_post_resources_read_unknown_uri(self, view, mock_hass):
        """Test POST with resources/read for unknown URI."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://unknown"},
                "id": 27,
            }
        )
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert "error" in body
        assert "Unknown resource" in body["error"]["message"]

    async def test_post_resources_read_devices(self, view, mock_hass):
        """Test resources/read for hass://devices."""
        mock_device = Mock()
        mock_device.id = "device1"
        mock_device.name = "Test Device"
        mock_device.manufacturer = "TestCorp"
        mock_device.model = "Model1"
        mock_device.area_id = "living_room"
        mock_device.name_by_user = None
        mock_registry = Mock()
        mock_registry.devices = {"device1": mock_device}

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://devices"},
                "id": 86,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.resources.dr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert len(data) == 1
        assert data[0]["id"] == "device1"
        assert data[0]["manufacturer"] == "TestCorp"

    async def test_post_resources_read_services(self, view, mock_hass):
        """Test resources/read for hass://services."""
        mock_hass.services.async_services.return_value = {
            "light": {"turn_on": {}, "turn_off": {}},
            "switch": {"toggle": {}},
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://services"},
                "id": 87,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert "light" in data
        assert "turn_on" in data["light"]
        assert "switch" in data

    async def test_post_resources_read_floors(self, view, mock_hass):
        """Test resources/read for hass://floors."""
        mock_floor = Mock()
        mock_floor.floor_id = "ground"
        mock_floor.name = "Ground Floor"
        mock_floor.icon = "mdi:home"
        mock_floor.level = 0
        mock_floor.aliases = {"First Floor", "Main"}
        mock_registry = Mock()
        mock_registry.async_list_floors.return_value = [mock_floor]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://floors"},
                "id": 88,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.resources.fr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert len(data) == 1
        assert data[0]["floor_id"] == "ground"
        assert data[0]["name"] == "Ground Floor"
        assert "First Floor" in data[0]["aliases"]

    async def test_post_resources_read_entities(self, view, mock_hass):
        """Test POST with resources/read for hass://entities."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}
        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temp"
        mock_state2.state = "22"
        mock_state2.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://entities"},
                "id": 222,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert "light" in data
        assert "sensor" in data
        assert data["light"][0]["entity_id"] == "light.living_room"
        assert data["light"][0]["state"] == "on"
        assert data["light"][0]["friendly_name"] == "Living Room Light"

    async def test_post_resources_read_entities_domain(self, view, mock_hass):
        """Test POST with resources/read for hass://entities/domain/light."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Room Light"}
        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temp"
        mock_state2.state = "22"
        mock_state2.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://entities/domain/light"},
                "id": 223,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "light.living_room"

    async def test_post_resources_read_labels(self, view, mock_hass):
        """Test POST with resources/read for hass://labels."""
        mock_label = Mock()
        mock_label.label_id = "important"
        mock_label.name = "Important"
        mock_label.color = "red"
        mock_label.icon = "mdi:star"
        mock_label.description = "Important items"

        mock_registry = Mock()
        mock_registry.async_list_labels.return_value = [mock_label]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://labels"},
                "id": 224,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.resources.lr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert len(data) == 1
        assert data[0]["label_id"] == "important"
        assert data[0]["name"] == "Important"
        assert data[0]["color"] == "red"

    async def test_post_resources_read_integrations(self, view, mock_hass):
        """Test POST with resources/read for hass://integrations."""
        mock_entry = Mock()
        mock_entry.domain = "hue"
        mock_entry.title = "Philips Hue"
        mock_entry.state = "loaded"
        mock_entry.entry_id = "entry1"
        mock_hass.config_entries.async_entries.return_value = [mock_entry]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "resources/read",
                "params": {"uri": "hass://integrations"},
                "id": 225,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["contents"][0]["text"])
        assert len(data) == 1
        assert data[0]["domain"] == "hue"
        assert data[0]["title"] == "Philips Hue"

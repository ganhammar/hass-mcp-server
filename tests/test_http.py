"""Tests for HTTP endpoints."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.oidc_provider.token_validator import get_issuer_from_request

from custom_components.mcp_server_http_transport.http import (
    MCPEndpointView,
    MCPProtectedResourceMetadataView,
    MCPSubpathProtectedResourceMetadataView,
    _get_protected_resource_metadata,
)


def test_get_base_url_with_forwarded_headers():
    """Test get_issuer_from_request with X-Forwarded headers (proxy setup)."""
    request = Mock()
    request.headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "example.com",
    }
    request.url.origin.return_value = "http://localhost:8123"

    result = get_issuer_from_request(request)

    assert result == "https://example.com"
    request.url.origin.assert_not_called()


def test_get_base_url_without_forwarded_headers():
    """Test get_issuer_from_request without X-Forwarded headers (direct connection)."""
    request = Mock()
    request.headers = {}
    request.url.origin.return_value = "http://192.168.1.100:8123"

    result = get_issuer_from_request(request)

    assert result == "http://192.168.1.100:8123"
    request.url.origin.assert_called_once()


def test_get_base_url_with_partial_forwarded_headers():
    """Test get_issuer_from_request with only one X-Forwarded header (should use fallback)."""
    request = Mock()
    request.headers = {
        "X-Forwarded-Proto": "https",
    }
    request.url.origin.return_value = "http://localhost:8123"

    result = get_issuer_from_request(request)

    assert result == "http://localhost:8123"
    request.url.origin.assert_called_once()


def test_get_protected_resource_metadata():
    """Test _get_protected_resource_metadata returns correct structure."""
    base_url = "https://homeassistant.local"

    metadata = _get_protected_resource_metadata(base_url)

    assert metadata["resource"] == f"{base_url}/api/mcp"
    assert metadata["authorization_servers"] == [f"{base_url}/oidc"]
    assert metadata["bearer_methods_supported"] == ["header"]
    assert metadata["resource_signing_alg_values_supported"] == ["RS256"]
    assert metadata["resource_documentation"] == f"{base_url}/api/mcp"


class TestMCPProtectedResourceMetadataView:
    """Test the MCP protected resource metadata view at root."""

    async def test_get_returns_metadata(self):
        """Test GET returns protected resource metadata."""
        request = Mock()
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPProtectedResourceMetadataView()
        response = await view.get(request)

        assert response.status == 200
        assert response.content_type == "application/json"

        body = json.loads(response.body)
        assert body["resource"] == "https://homeassistant.local/api/mcp"
        assert body["authorization_servers"] == ["https://homeassistant.local/oidc"]

    async def test_get_with_forwarded_headers(self):
        """Test GET with X-Forwarded headers."""
        request = Mock()
        request.headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "example.com",
        }

        view = MCPProtectedResourceMetadataView()
        response = await view.get(request)

        body = json.loads(response.body)
        assert body["resource"] == "https://example.com/api/mcp"


class TestMCPSubpathProtectedResourceMetadataView:
    """Test the MCP protected resource metadata view with /mcp suffix."""

    async def test_get_returns_metadata(self):
        """Test GET returns protected resource metadata."""
        request = Mock()
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPSubpathProtectedResourceMetadataView()
        response = await view.get(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["resource"] == "https://homeassistant.local/api/mcp"


class TestMCPEndpointView:
    """Test the MCP endpoint view."""

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

    async def test_post_without_token_returns_401(self, view):
        """Test POST without Authorization header returns 401."""
        request = Mock()
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        response = await view.post(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"
        assert "WWW-Authenticate" in response.headers

    async def test_post_with_invalid_token_returns_401(self, view):
        """Test POST with invalid token returns 401."""
        request = Mock()
        request.headers = {"Authorization": "Bearer invalid_token"}
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=None):
            response = await view.post(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"

    async def test_post_initialize_request(self, view):
        """Test POST with initialize request."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 1})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["protocolVersion"] == "2024-11-05"
        assert body["result"]["serverInfo"]["name"] == "home-assistant-mcp-server"
        assert body["id"] == 1

    async def test_post_tools_list_request(self, view):
        """Test POST with tools/list request."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/list", "id": 2})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert "tools" in body["result"]
        assert len(body["result"]["tools"]) == 25
        tool_names = [t["name"] for t in body["result"]["tools"]]
        assert "get_state" in tool_names
        assert "call_service" in tool_names
        assert "list_entities" in tool_names
        assert "get_config" in tool_names
        assert "list_areas" in tool_names
        assert "list_devices" in tool_names
        assert "list_services" in tool_names
        assert "render_template" in tool_names
        assert "get_history" in tool_names
        assert "create_automation" in tool_names
        assert "update_automation" in tool_names
        assert "delete_automation" in tool_names
        assert "create_scene" in tool_names
        assert "update_scene" in tool_names
        assert "delete_scene" in tool_names
        assert "create_script" in tool_names
        assert "update_script" in tool_names
        assert "delete_script" in tool_names
        assert "list_dashboards" in tool_names
        assert "get_dashboard_config" in tool_names
        assert "save_dashboard_config" in tool_names
        assert "delete_dashboard_config" in tool_names
        assert "create_dashboard" in tool_names
        assert "update_dashboard" in tool_names
        assert "delete_dashboard" in tool_names

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
        mock_entry.aliases = {"Lounge Light"}
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
                "custom_components.mcp_server_http_transport.tools.er.async_get",
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
                "custom_components.mcp_server_http_transport.tools.er.async_get",
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
                "custom_components.mcp_server_http_transport.tools.er.async_get",
                return_value=mock_er,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        entities = json.loads(body["result"]["content"][0]["text"])
        assert len(entities) == 1
        assert entities[0]["entity_id"] == "light.living_room"

    async def test_post_unknown_method_returns_error(self, view):
        """Test POST with unknown method returns error."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={"jsonrpc": "2.0", "method": "unknown_method", "id": 9}
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "error" in body
        assert body["error"]["code"] == -32601
        assert "Method not found" in body["error"]["message"]

    async def test_post_notification_returns_202(self, view):
        """Test POST with notification (no id) returns 202."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "some_notification"})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 202

    async def test_validate_token_without_bearer_prefix(self, view):
        """Test _validate_token without Bearer prefix returns None."""
        request = Mock()
        request.headers = {"Authorization": "invalid_format"}

        result = view._validate_token(request)

        assert result is None

    async def test_post_tools_call_unknown_tool(self, view):
        """Test POST with tools/call for unknown tool."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "unknown_tool", "arguments": {}},
                "id": 10,
            }
        )
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert "error" in body
        assert "Unknown tool" in body["error"]["message"]

    async def test_post_tools_call_get_config(self, view, mock_hass):
        """Test POST with tools/call for get_config."""
        mock_units = Mock()
        mock_units.as_dict.return_value = {"temperature": "°C", "length": "km"}
        mock_hass.config.location_name = "Home"
        mock_hass.config.latitude = 59.0
        mock_hass.config.longitude = 18.0
        mock_hass.config.elevation = 10
        mock_hass.config.units = mock_units
        mock_hass.config.time_zone = "Europe/Stockholm"
        mock_hass.config.version = "2024.12.0"
        mock_hass.config.currency = "SEK"
        mock_hass.config.country = "SE"
        mock_hass.config.language = "en"

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_config", "arguments": {}},
                "id": 11,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        config = json.loads(body["result"]["content"][0]["text"])
        assert config["location_name"] == "Home"
        assert config["latitude"] == 59.0
        assert config["version"] == "2024.12.0"
        assert config["time_zone"] == "Europe/Stockholm"
        assert config["unit_system"]["temperature"] == "°C"

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
                "custom_components.mcp_server_http_transport.tools.ar.async_get",
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
                "custom_components.mcp_server_http_transport.tools.dr.async_get",
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
                "custom_components.mcp_server_http_transport.tools.dr.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        devices = json.loads(body["result"]["content"][0]["text"])
        assert len(devices) == 1
        assert devices[0]["name"] == "Living Room Lamp"

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

    # --- Phase 1b: render_template + get_history ---

    async def test_post_tools_call_render_template(self, view, mock_hass):
        """Test POST with tools/call for render_template."""
        mock_tpl = Mock()
        mock_tpl.async_render.return_value = "Living Room Light is on"

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "render_template",
                    "arguments": {
                        "template": "{{ states('light.living_room') }}",
                    },
                },
                "id": 17,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.helpers.template.Template",
                return_value=mock_tpl,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["content"][0]["text"] == "Living Room Light is on"

    async def test_post_tools_call_render_template_error(self, view, mock_hass):
        """Test POST with tools/call for render_template with error."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "render_template",
                    "arguments": {"template": "{{ invalid"},
                },
                "id": 18,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.helpers.template.Template",
                side_effect=Exception("Template syntax error"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error rendering template" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_get_history(self, view, mock_hass):
        """Test POST with tools/call for get_history."""
        mock_state1 = Mock()
        mock_state1.state = "off"
        mock_state1.last_changed = datetime(2024, 1, 1, 8, 0, 0)
        mock_state1.attributes = {}

        mock_state2 = Mock()
        mock_state2.state = "on"
        mock_state2.last_changed = datetime(2024, 1, 1, 12, 0, 0)
        mock_state2.attributes = {"brightness": 255}

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(
            return_value={"light.living_room": [mock_state1, mock_state2]}
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_history",
                    "arguments": {
                        "entity_id": "light.living_room",
                        "start_time": "2024-01-01T00:00:00",
                        "end_time": "2024-01-01T23:59:59",
                    },
                },
                "id": 19,
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
        history = json.loads(body["result"]["content"][0]["text"])
        assert len(history) == 2
        assert history[0]["state"] == "off"
        assert history[1]["state"] == "on"

    async def test_post_tools_call_get_history_empty(self, view, mock_hass):
        """Test POST with tools/call for get_history with no history."""
        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value={})

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_history",
                    "arguments": {
                        "entity_id": "light.nonexistent",
                        "start_time": "2024-01-01T00:00:00",
                    },
                },
                "id": 20,
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
        history = json.loads(body["result"]["content"][0]["text"])
        assert len(history) == 0

    async def test_post_tools_call_get_history_recorder_error(self, view, mock_hass):
        """Test POST with tools/call for get_history when recorder fails."""
        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(
            side_effect=Exception("Recorder not available")
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_history",
                    "arguments": {
                        "entity_id": "light.living_room",
                        "start_time": "2024-01-01T00:00:00",
                    },
                },
                "id": 21,
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
        assert "Error getting history" in body["result"]["content"][0]["text"]

    # --- Phase 2: Resources ---

    async def test_post_initialize_advertises_capabilities(self, view):
        """Test POST initialize advertises resources and prompts capabilities."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 21})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        body = json.loads(response.body)
        capabilities = body["result"]["capabilities"]
        assert "tools" in capabilities
        assert "resources" in capabilities
        assert "prompts" in capabilities

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
        assert len(result["resources"]) == 2
        assert result["resources"][0]["uri"] == "hass://config"
        assert result["resources"][1]["uri"] == "hass://areas"
        assert len(result["resourceTemplates"]) == 2
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
        mock_hass.config.version = "2024.12.0"
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

    # --- Phase 3: Prompts ---

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
        assert len(prompts) == 2
        prompt_names = [p["name"] for p in prompts]
        assert "troubleshoot_device" in prompt_names
        assert "daily_summary" in prompt_names

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

    # --- Phase 4: Completions ---

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

    # --- Config CRUD Tools ---

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

    # --- Dashboard tools ---

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

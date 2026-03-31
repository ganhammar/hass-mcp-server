"""Integration tests that simulate full MCP client sessions.

These tests use a realistic mock Home Assistant instance pre-populated with
entities, areas, devices, and services. They exercise the full request pipeline:
HTTP POST → token validation → JSON-RPC dispatch → module handler → response.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


@pytest.fixture
def populated_hass():
    """Create a mock Home Assistant with realistic pre-populated data."""
    hass = Mock()

    # --- Entities ---
    light_living = Mock()
    light_living.entity_id = "light.living_room"
    light_living.state = "on"
    light_living.attributes = {
        "friendly_name": "Living Room Light",
        "brightness": 200,
        "color_mode": "brightness",
        "supported_color_modes": ["brightness"],
    }
    light_living.last_changed = datetime(2024, 6, 15, 8, 30, 0)
    light_living.last_updated = datetime(2024, 6, 15, 8, 30, 0)

    light_bedroom = Mock()
    light_bedroom.entity_id = "light.bedroom"
    light_bedroom.state = "off"
    light_bedroom.attributes = {
        "friendly_name": "Bedroom Light",
        "brightness": 0,
    }
    light_bedroom.last_changed = datetime(2024, 6, 15, 7, 0, 0)
    light_bedroom.last_updated = datetime(2024, 6, 15, 7, 0, 0)

    sensor_temp = Mock()
    sensor_temp.entity_id = "sensor.temperature"
    sensor_temp.state = "22.5"
    sensor_temp.attributes = {
        "friendly_name": "Temperature Sensor",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
    }
    sensor_temp.last_changed = datetime(2024, 6, 15, 9, 0, 0)
    sensor_temp.last_updated = datetime(2024, 6, 15, 9, 0, 0)

    switch_kitchen = Mock()
    switch_kitchen.entity_id = "switch.kitchen"
    switch_kitchen.state = "off"
    switch_kitchen.attributes = {"friendly_name": "Kitchen Switch"}
    switch_kitchen.last_changed = datetime(2024, 6, 15, 6, 0, 0)
    switch_kitchen.last_updated = datetime(2024, 6, 15, 6, 0, 0)

    automation_morning = Mock()
    automation_morning.entity_id = "automation.morning_routine"
    automation_morning.state = "on"
    automation_morning.attributes = {
        "friendly_name": "Morning Routine",
        "last_triggered": "2024-06-15T07:00:00",
    }
    automation_morning.last_changed = datetime(2024, 6, 15, 7, 0, 0)
    automation_morning.last_updated = datetime(2024, 6, 15, 7, 0, 0)

    all_entities = [light_living, light_bedroom, sensor_temp, switch_kitchen, automation_morning]

    hass.states = Mock()
    hass.states.async_all.return_value = all_entities
    hass.states.get = Mock(
        side_effect=lambda eid: next((e for e in all_entities if e.entity_id == eid), None)
    )

    # --- Services ---
    hass.services = Mock()
    hass.services.async_services.return_value = {
        "light": {"turn_on": Mock(), "turn_off": Mock(), "toggle": Mock()},
        "switch": {"turn_on": Mock(), "turn_off": Mock()},
        "automation": {"trigger": Mock(), "turn_on": Mock(), "turn_off": Mock()},
        "homeassistant": {"restart": Mock(), "reload_all": Mock()},
    }
    hass.services.async_call = AsyncMock()

    # --- Config ---
    mock_units = Mock()
    mock_units.as_dict.return_value = {
        "temperature": "°C",
        "length": "km",
        "mass": "kg",
        "volume": "L",
    }
    hass.config = Mock()
    hass.config.location_name = "Home"
    hass.config.latitude = 59.3293
    hass.config.longitude = 18.0686
    hass.config.elevation = 28
    hass.config.units = mock_units
    hass.config.time_zone = "Europe/Stockholm"
    hass.config.version = "2024.12.0"
    hass.config.currency = "SEK"
    hass.config.country = "SE"
    hass.config.language = "sv"

    return hass


@pytest.fixture
def mock_area_registry():
    """Create a mock area registry."""
    area_living = Mock()
    area_living.id = "living_room"
    area_living.name = "Living Room"
    area_living.floor_id = "ground_floor"

    area_bedroom = Mock()
    area_bedroom.id = "bedroom"
    area_bedroom.name = "Bedroom"
    area_bedroom.floor_id = "first_floor"

    area_kitchen = Mock()
    area_kitchen.id = "kitchen"
    area_kitchen.name = "Kitchen"
    area_kitchen.floor_id = "ground_floor"

    registry = Mock()
    registry.async_list_areas.return_value = [area_living, area_bedroom, area_kitchen]
    return registry


@pytest.fixture
def mock_entity_registry():
    """Create a mock entity registry."""
    entries = {}

    entry_living = Mock()
    entry_living.aliases = {"Living Room Lamp", "Lounge Light"}
    entry_living.area_id = "living_room"
    entry_living.device_id = "hue_bridge"
    entries["light.living_room"] = entry_living

    entry_bedroom = Mock()
    entry_bedroom.aliases = set()
    entry_bedroom.area_id = "bedroom"
    entry_bedroom.device_id = None
    entries["light.bedroom"] = entry_bedroom

    entry_temp = Mock()
    entry_temp.aliases = {"Temp Sensor"}
    entry_temp.area_id = "bedroom"
    entry_temp.device_id = "aqara_sensor"
    entries["sensor.temperature"] = entry_temp

    entry_switch = Mock()
    entry_switch.aliases = set()
    entry_switch.area_id = "kitchen"
    entry_switch.device_id = "smart_plug"
    entries["switch.kitchen"] = entry_switch

    entry_auto = Mock()
    entry_auto.aliases = {"Wake Up Routine"}
    entry_auto.area_id = None
    entry_auto.device_id = None
    entries["automation.morning_routine"] = entry_auto

    registry = Mock()
    registry.async_get = Mock(side_effect=lambda eid: entries.get(eid))
    return registry


@pytest.fixture
def mock_device_registry():
    """Create a mock device registry."""
    device_hue = Mock()
    device_hue.id = "hue_bridge"
    device_hue.name = "Hue Bridge"
    device_hue.manufacturer = "Philips"
    device_hue.model = "BSB002"
    device_hue.area_id = "living_room"
    device_hue.name_by_user = None

    device_sensor = Mock()
    device_sensor.id = "aqara_sensor"
    device_sensor.name = "Aqara Temperature"
    device_sensor.manufacturer = "Aqara"
    device_sensor.model = "WSDCGQ11LM"
    device_sensor.area_id = "bedroom"
    device_sensor.name_by_user = "Bedroom Sensor"

    device_plug = Mock()
    device_plug.id = "smart_plug"
    device_plug.name = "Smart Plug"
    device_plug.manufacturer = "TP-Link"
    device_plug.model = "HS110"
    device_plug.area_id = "kitchen"
    device_plug.name_by_user = None

    registry = Mock()
    registry.devices = {
        "hue_bridge": device_hue,
        "aqara_sensor": device_sensor,
        "smart_plug": device_plug,
    }
    return registry


class TestMCPClientSession:
    """Simulate a full MCP client session with a realistic HA instance."""

    @pytest.fixture
    def view(self, populated_hass):
        """Create view with populated hass."""
        return MCPEndpointView(populated_hass, Mock())

    async def _call(self, view, method, params=None, msg_id=1):
        """Helper: make a JSON-RPC request through the view."""
        body = {"jsonrpc": "2.0", "method": method, "id": msg_id}
        if params is not None:
            body["params"] = params

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value=body)

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        return json.loads(response.body)

    async def test_full_session_lifecycle(self, view):
        """Test a complete MCP session: initialize → discover → use."""
        # Step 1: Initialize
        result = await self._call(view, "initialize")
        assert result["result"]["protocolVersion"] == "2024-11-05"
        caps = result["result"]["capabilities"]
        assert "tools" in caps
        assert "resources" in caps
        assert "prompts" in caps

        # Step 2: Discover tools
        result = await self._call(view, "tools/list", msg_id=2)
        tool_names = [t["name"] for t in result["result"]["tools"]]
        assert len(tool_names) == 34
        # Verify all tools have required schema fields
        for tool in result["result"]["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

        # Step 3: Discover resources
        result = await self._call(view, "resources/list", msg_id=3)
        assert len(result["result"]["resources"]) == 5
        assert len(result["result"]["resourceTemplates"]) == 2

        # Step 4: Discover prompts
        result = await self._call(view, "prompts/list", msg_id=4)
        assert len(result["result"]["prompts"]) == 5
        for prompt in result["result"]["prompts"]:
            assert "name" in prompt
            assert "description" in prompt

    async def test_entity_consistency_across_tools_and_resources(self, view, mock_entity_registry):
        """Verify get_state tool and resources/read return consistent data."""
        with patch(
            "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
            return_value=mock_entity_registry,
        ):
            # Get state via tool
            tool_result = await self._call(
                view,
                "tools/call",
                {"name": "get_state", "arguments": {"entity_id": "light.living_room"}},
                msg_id=1,
            )
            tool_data = json.loads(tool_result["result"]["content"][0]["text"])

        # Get state via resource
        resource_result = await self._call(
            view,
            "resources/read",
            {"uri": "hass://entity/light.living_room"},
            msg_id=2,
        )
        resource_data = json.loads(resource_result["result"]["contents"][0]["text"])

        # Both should return identical entity data
        assert tool_data["entity_id"] == resource_data["entity_id"]
        assert tool_data["state"] == resource_data["state"]
        assert tool_data["attributes"] == resource_data["attributes"]
        assert tool_data["last_changed"] == resource_data["last_changed"]
        assert tool_data["aliases"] == ["Living Room Lamp", "Lounge Light"]

    async def test_config_consistency_across_tools_and_resources(self, view):
        """Verify that get_config tool and resources/read return consistent data."""
        tool_result = await self._call(
            view,
            "tools/call",
            {"name": "get_config", "arguments": {}},
            msg_id=1,
        )
        tool_data = json.loads(tool_result["result"]["content"][0]["text"])

        resource_result = await self._call(
            view,
            "resources/read",
            {"uri": "hass://config"},
            msg_id=2,
        )
        resource_data = json.loads(resource_result["result"]["contents"][0]["text"])

        assert tool_data["location_name"] == resource_data["location_name"]
        assert tool_data["version"] == resource_data["version"]
        assert tool_data["time_zone"] == resource_data["time_zone"]
        assert tool_data["unit_system"] == resource_data["unit_system"]

    async def test_list_entities_domain_filtering(self, view, mock_entity_registry):
        """Test that domain filtering works correctly across entity types."""
        with patch(
            "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
            return_value=mock_entity_registry,
        ):
            # All entities
            result = await self._call(
                view, "tools/call", {"name": "list_entities", "arguments": {}}, msg_id=1
            )
            all_entities = json.loads(result["result"]["content"][0]["text"])
            assert len(all_entities) == 5

            # Light entities only
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_entities", "arguments": {"domain": "light"}},
                msg_id=2,
            )
            lights = json.loads(result["result"]["content"][0]["text"])
            assert len(lights) == 2
            assert all(e["entity_id"].startswith("light.") for e in lights)

            # Verify aliases are included
            living_light = next(e for e in lights if e["entity_id"] == "light.living_room")
            assert living_light["aliases"] == ["Living Room Lamp", "Lounge Light"]
            bedroom_light = next(e for e in lights if e["entity_id"] == "light.bedroom")
            assert bedroom_light["aliases"] == []

            # Automation entities
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_entities", "arguments": {"domain": "automation"}},
                msg_id=3,
            )
            automations = json.loads(result["result"]["content"][0]["text"])
            assert len(automations) == 1
            assert automations[0]["entity_id"] == "automation.morning_routine"
            assert automations[0]["aliases"] == ["Wake Up Routine"]

            # Nonexistent domain
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_entities", "arguments": {"domain": "climate"}},
                msg_id=4,
            )
            empty = json.loads(result["result"]["content"][0]["text"])
            assert len(empty) == 0

    async def test_list_devices_with_area_filtering(self, view, mock_device_registry):
        """Test device listing and area filtering."""
        with patch(
            "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
            return_value=mock_device_registry,
        ):
            # All devices
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_devices", "arguments": {}},
                msg_id=1,
            )
            all_devices = json.loads(result["result"]["content"][0]["text"])
            assert len(all_devices) == 3

            # Filter by area
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_devices", "arguments": {"area_id": "living_room"}},
                msg_id=2,
            )
            living_devices = json.loads(result["result"]["content"][0]["text"])
            assert len(living_devices) == 1
            assert living_devices[0]["manufacturer"] == "Philips"

            # Nonexistent area
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_devices", "arguments": {"area_id": "garage"}},
                msg_id=3,
            )
            empty = json.loads(result["result"]["content"][0]["text"])
            assert len(empty) == 0

    async def test_list_services_with_domain_filtering(self, view):
        """Test service listing and domain filtering."""
        # All services
        result = await self._call(
            view, "tools/call", {"name": "list_services", "arguments": {}}, msg_id=1
        )
        all_services = json.loads(result["result"]["content"][0]["text"])
        assert len(all_services) == 4
        assert "light" in all_services
        assert "automation" in all_services

        # Filter by domain
        result = await self._call(
            view,
            "tools/call",
            {"name": "list_services", "arguments": {"domain": "light"}},
            msg_id=2,
        )
        light_services = json.loads(result["result"]["content"][0]["text"])
        assert "light" in light_services
        assert "switch" not in light_services
        assert set(light_services["light"]) == {"turn_on", "turn_off", "toggle"}

    async def test_call_service_end_to_end(self, view, populated_hass):
        """Test calling a service passes correct data to hass."""
        result = await self._call(
            view,
            "tools/call",
            {
                "name": "call_service",
                "arguments": {
                    "domain": "light",
                    "service": "turn_on",
                    "entity_id": "light.bedroom",
                    "data": {"brightness": 128, "transition": 2},
                },
            },
            msg_id=1,
        )
        assert "Successfully called" in result["result"]["content"][0]["text"]
        populated_hass.services.async_call.assert_called_once_with(
            "light",
            "turn_on",
            {"brightness": 128, "transition": 2, "entity_id": "light.bedroom"},
            blocking=True,
        )

    async def test_get_state_for_nonexistent_entity(self, view, mock_entity_registry):
        """Test get_state gracefully handles missing entities."""
        with patch(
            "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
            return_value=mock_entity_registry,
        ):
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_state", "arguments": {"entity_id": "light.nonexistent"}},
            )
            text = result["result"]["content"][0]["text"]
            assert "not found" in text

    async def test_resource_read_entity_not_found(self, view):
        """Test resources/read for nonexistent entity returns error."""
        result = await self._call(
            view,
            "resources/read",
            {"uri": "hass://entity/sensor.nonexistent"},
        )
        assert "error" in result
        assert "not found" in result["error"]["message"]

    async def test_areas_consistency_across_tools_and_resources(self, view, mock_area_registry):
        """Verify list_areas tool and resources/read hass://areas return consistent data."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.ar.async_get",
                return_value=mock_area_registry,
            ),
            patch(
                "custom_components.mcp_server_http_transport.resources.ar.async_get",
                return_value=mock_area_registry,
            ),
        ):
            # Via tool
            tool_result = await self._call(
                view,
                "tools/call",
                {"name": "list_areas", "arguments": {}},
                msg_id=1,
            )
            tool_areas = json.loads(tool_result["result"]["content"][0]["text"])

            # Via resource
            resource_result = await self._call(
                view,
                "resources/read",
                {"uri": "hass://areas"},
                msg_id=2,
            )
            resource_areas = json.loads(resource_result["result"]["contents"][0]["text"])

            assert tool_areas == resource_areas
            assert len(tool_areas) == 3

    async def test_completions_match_actual_entities(self, view, mock_entity_registry):
        """Verify completions return entity IDs that actually exist."""
        # Get completions for "light."
        comp_result = await self._call(
            view,
            "completion/complete",
            {
                "ref": {"type": "ref/tool", "name": "get_state"},
                "argument": {"name": "entity_id", "value": "light."},
            },
        )
        completed_ids = comp_result["result"]["completion"]["values"]
        assert set(completed_ids) == {"light.bedroom", "light.living_room"}

        # Verify each completed entity_id actually resolves via get_state
        with patch(
            "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
            return_value=mock_entity_registry,
        ):
            for entity_id in completed_ids:
                result = await self._call(
                    view,
                    "tools/call",
                    {"name": "get_state", "arguments": {"entity_id": entity_id}},
                )
                data = json.loads(result["result"]["content"][0]["text"])
                assert data["entity_id"] == entity_id

    async def test_completions_domains_match_actual_domains(self, view, mock_entity_registry):
        """Verify domain completions match domains from list_entities."""
        with patch(
            "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
            return_value=mock_entity_registry,
        ):
            # Get all entities to find actual domains
            result = await self._call(
                view, "tools/call", {"name": "list_entities", "arguments": {}}, msg_id=1
            )
            all_entities = json.loads(result["result"]["content"][0]["text"])
            actual_domains = sorted(set(e["entity_id"].split(".")[0] for e in all_entities))

        # Get domain completions with empty prefix
        comp_result = await self._call(
            view,
            "completion/complete",
            {
                "ref": {"type": "ref/tool", "name": "list_entities"},
                "argument": {"name": "domain", "value": ""},
            },
            msg_id=2,
        )
        completed_domains = comp_result["result"]["completion"]["values"]

        assert completed_domains == actual_domains

    async def test_completions_automation_id(self, view, populated_hass):
        """Verify completions return automation IDs from automations.yaml."""
        populated_hass.config.path = Mock(return_value="/config/automations.yaml")

        yaml_store = [
            {"id": "abc-123", "alias": "Auto One"},
            {"id": "abc-456", "alias": "Auto Two"},
        ]

        def mock_load_list(path):
            return list(yaml_store)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            side_effect=mock_load_list,
        ):
            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/tool", "name": "get_automation_config"},
                    "argument": {"name": "automation_id", "value": "abc"},
                },
            )
            values = comp_result["result"]["completion"]["values"]
            assert set(values) == {"abc-123", "abc-456"}

            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/tool", "name": "get_automation_config"},
                    "argument": {"name": "automation_id", "value": "abc-1"},
                },
                msg_id=2,
            )
            values = comp_result["result"]["completion"]["values"]
            assert values == ["abc-123"]

    async def test_completions_automation_id_via_prompt_ref(self, view, populated_hass):
        """Verify automation_id completions work with a prompt ref."""
        populated_hass.config.path = Mock(return_value="/config/automations.yaml")

        yaml_store = [
            {"id": "rev-001", "alias": "Review Target"},
        ]

        def mock_load_list(path):
            return list(yaml_store)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            side_effect=mock_load_list,
        ):
            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/prompt", "name": "automation_review"},
                    "argument": {"name": "automation_id", "value": "rev"},
                },
            )
            values = comp_result["result"]["completion"]["values"]
            assert values == ["rev-001"]

    async def test_completions_script_key(self, view, populated_hass):
        """Verify completions return script keys from scripts.yaml."""
        populated_hass.config.path = Mock(return_value="/config/scripts.yaml")

        yaml_store = {
            "morning_routine": {"alias": "Morning"},
            "movie_time": {"alias": "Movie"},
        }

        def mock_load_dict(path):
            return dict(yaml_store)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
            side_effect=mock_load_dict,
        ):
            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/tool", "name": "get_script_config"},
                    "argument": {"name": "key", "value": "mo"},
                },
            )
            values = comp_result["result"]["completion"]["values"]
            assert set(values) == {"morning_routine", "movie_time"}

            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/tool", "name": "get_script_config"},
                    "argument": {"name": "key", "value": "morning"},
                },
                msg_id=2,
            )
            values = comp_result["result"]["completion"]["values"]
            assert values == ["morning_routine"]

    async def test_completions_scene_id(self, view, populated_hass):
        """Verify completions return scene IDs from scenes.yaml."""
        populated_hass.config.path = Mock(return_value="/config/scenes.yaml")

        yaml_store = [
            {"id": "scene-001", "name": "Movie Night"},
            {"id": "scene-002", "name": "Dinner"},
        ]

        def mock_load_list(path):
            return list(yaml_store)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            side_effect=mock_load_list,
        ):
            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/tool", "name": "get_scene_config"},
                    "argument": {"name": "scene_id", "value": "scene"},
                },
            )
            values = comp_result["result"]["completion"]["values"]
            assert set(values) == {"scene-001", "scene-002"}

            comp_result = await self._call(
                view,
                "completion/complete",
                {
                    "ref": {"type": "ref/tool", "name": "get_scene_config"},
                    "argument": {"name": "scene_id", "value": "scene-001"},
                },
                msg_id=2,
            )
            values = comp_result["result"]["completion"]["values"]
            assert values == ["scene-001"]

    async def test_prompts_troubleshoot_with_real_entity(self, view):
        """Test troubleshoot prompt includes actual entity state data."""
        result = await self._call(
            view,
            "prompts/get",
            {
                "name": "troubleshoot_device",
                "arguments": {"entity_id": "sensor.temperature"},
            },
        )
        prompt = result["result"]
        text = prompt["messages"][0]["content"]["text"]

        # Prompt should contain the actual entity state
        assert "22.5" in text
        assert "temperature" in text.lower()

    async def test_unknown_tool_returns_error(self, view):
        """Test calling a nonexistent tool returns a proper error."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "delete_everything", "arguments": {}},
                "id": 1,
            }
        )
        request.url.origin.return_value = "https://ha.local"

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert body["error"]["code"] == -32603
        assert "Unknown tool" in body["error"]["message"]

    async def test_unknown_method_returns_proper_jsonrpc_error(self, view):
        """Test unknown JSON-RPC method returns -32601."""
        result = await self._call(view, "nonexistent/method")
        assert result["error"]["code"] == -32601
        assert "Method not found" in result["error"]["message"]

    async def test_notification_without_id_returns_202(self, view):
        """Test JSON-RPC notification (no id) returns 202."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 202

    async def test_automation_crud_round_trip(self, view, populated_hass):
        """Test create, update, delete automation round-trip."""
        populated_hass.config.path = Mock(return_value="/config/automations.yaml")
        populated_hass.services.async_call = AsyncMock()

        yaml_store = []

        def mock_load_list(path):
            return list(yaml_store)

        def mock_save(path, data):
            yaml_store.clear()
            yaml_store.extend(data)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                side_effect=mock_load_list,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
                side_effect=mock_save,
            ),
        ):
            # Create
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "create_automation",
                    "arguments": {"config": {"alias": "Test Auto", "trigger": []}},
                },
                msg_id=100,
            )
            text = result["result"]["content"][0]["text"]
            assert "Successfully created" in text
            auto_id = text.split("id: ")[1]
            assert len(yaml_store) == 1

            # List
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_automations", "arguments": {}},
                msg_id=101,
            )
            entries = json.loads(result["result"]["content"][0]["text"])
            assert len(entries) == 1
            assert entries[0]["alias"] == "Test Auto"

            # Get config
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_automation_config", "arguments": {"automation_id": auto_id}},
                msg_id=102,
            )
            entry = json.loads(result["result"]["content"][0]["text"])
            assert entry["alias"] == "Test Auto"
            assert entry["id"] == auto_id

            # Get config not found
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_automation_config", "arguments": {"automation_id": "nonexistent"}},
                msg_id=103,
            )
            assert "Error" in result["result"]["content"][0]["text"]

            # Update
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "update_automation",
                    "arguments": {
                        "automation_id": auto_id,
                        "config": {"alias": "Updated Auto"},
                    },
                },
                msg_id=104,
            )
            assert "Successfully updated" in result["result"]["content"][0]["text"]
            assert yaml_store[0]["alias"] == "Updated Auto"

            # Delete
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "delete_automation",
                    "arguments": {"automation_id": auto_id},
                },
                msg_id=105,
            )
            assert "Successfully deleted" in result["result"]["content"][0]["text"]
            assert len(yaml_store) == 0

            # List empty
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_automations", "arguments": {}},
                msg_id=106,
            )
            entries = json.loads(result["result"]["content"][0]["text"])
            assert entries == []

    async def test_script_crud_round_trip(self, view, populated_hass):
        """Test create, update, delete script round-trip."""
        populated_hass.config.path = Mock(return_value="/config/scripts.yaml")
        populated_hass.services.async_call = AsyncMock()

        yaml_store = {}

        def mock_load_dict(path):
            return dict(yaml_store)

        def mock_save(path, data):
            yaml_store.clear()
            yaml_store.update(data)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                side_effect=mock_load_dict,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
                side_effect=mock_save,
            ),
        ):
            # Create
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "create_script",
                    "arguments": {
                        "key": "test_script",
                        "config": {"alias": "Test Script", "sequence": []},
                    },
                },
                msg_id=110,
            )
            assert "Successfully created script" in result["result"]["content"][0]["text"]
            assert "test_script" in yaml_store

            # List
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_scripts", "arguments": {}},
                msg_id=114,
            )
            entries = json.loads(result["result"]["content"][0]["text"])
            assert "test_script" in entries
            assert entries["test_script"]["alias"] == "Test Script"

            # Get config
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_script_config", "arguments": {"key": "test_script"}},
                msg_id=115,
            )
            entry = json.loads(result["result"]["content"][0]["text"])
            assert entry["alias"] == "Test Script"

            # Get config not found
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_script_config", "arguments": {"key": "nonexistent"}},
                msg_id=116,
            )
            assert "Error" in result["result"]["content"][0]["text"]

            # Update
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "update_script",
                    "arguments": {
                        "key": "test_script",
                        "config": {"alias": "Updated Script", "sequence": []},
                    },
                },
                msg_id=111,
            )
            assert "Successfully updated" in result["result"]["content"][0]["text"]
            assert yaml_store["test_script"]["alias"] == "Updated Script"

            # Duplicate create fails
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "create_script",
                    "arguments": {
                        "key": "test_script",
                        "config": {"alias": "Dup"},
                    },
                },
                msg_id=112,
            )
            assert "Error creating script" in result["result"]["content"][0]["text"]

            # Delete
            result = await self._call(
                view,
                "tools/call",
                {"name": "delete_script", "arguments": {"key": "test_script"}},
                msg_id=113,
            )
            assert "Successfully deleted" in result["result"]["content"][0]["text"]
            assert "test_script" not in yaml_store

    async def test_scene_crud_round_trip(self, view, populated_hass):
        """Test create, list, get, update, delete scene round-trip."""
        populated_hass.config.path = Mock(return_value="/config/scenes.yaml")
        populated_hass.services.async_call = AsyncMock()

        yaml_store = []

        def mock_load_list(path):
            return list(yaml_store)

        def mock_save(path, data):
            yaml_store.clear()
            yaml_store.extend(data)

        async def run_fn(fn, *args):
            return fn(*args)

        populated_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                side_effect=mock_load_list,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_dumper.save_yaml",
                side_effect=mock_save,
            ),
        ):
            # Create
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "create_scene",
                    "arguments": {
                        "config": {
                            "name": "Movie Night",
                            "entities": {"light.living_room": {"state": "on"}},
                        }
                    },
                },
                msg_id=120,
            )
            text = result["result"]["content"][0]["text"]
            assert "Successfully created" in text
            scene_id = text.split("id: ")[1]
            assert len(yaml_store) == 1

            # List
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_scenes", "arguments": {}},
                msg_id=121,
            )
            entries = json.loads(result["result"]["content"][0]["text"])
            assert len(entries) == 1
            assert entries[0]["name"] == "Movie Night"

            # Get config
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_scene_config", "arguments": {"scene_id": scene_id}},
                msg_id=122,
            )
            entry = json.loads(result["result"]["content"][0]["text"])
            assert entry["name"] == "Movie Night"
            assert entry["id"] == scene_id

            # Get config not found
            result = await self._call(
                view,
                "tools/call",
                {"name": "get_scene_config", "arguments": {"scene_id": "nonexistent"}},
                msg_id=123,
            )
            assert "Error" in result["result"]["content"][0]["text"]

            # Update
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "update_scene",
                    "arguments": {
                        "scene_id": scene_id,
                        "config": {"name": "Updated Scene"},
                    },
                },
                msg_id=124,
            )
            assert "Successfully updated" in result["result"]["content"][0]["text"]
            assert yaml_store[0]["name"] == "Updated Scene"

            # Delete
            result = await self._call(
                view,
                "tools/call",
                {"name": "delete_scene", "arguments": {"scene_id": scene_id}},
                msg_id=125,
            )
            assert "Successfully deleted" in result["result"]["content"][0]["text"]
            assert len(yaml_store) == 0

            # List empty
            result = await self._call(
                view,
                "tools/call",
                {"name": "list_scenes", "arguments": {}},
                msg_id=126,
            )
            entries = json.loads(result["result"]["content"][0]["text"])
            assert entries == []

    async def test_list_tools_error_handling(self, view, populated_hass):
        """Test that list tools return errors when read helpers fail."""
        populated_hass.config.path = Mock(return_value="/config/test.yaml")
        populated_hass.async_add_executor_job = AsyncMock(side_effect=OSError("disk read error"))

        result = await self._call(
            view,
            "tools/call",
            {"name": "list_automations", "arguments": {}},
        )
        assert "Error" in result["result"]["content"][0]["text"]

        result = await self._call(
            view,
            "tools/call",
            {"name": "list_scenes", "arguments": {}},
            msg_id=2,
        )
        assert "Error" in result["result"]["content"][0]["text"]

        result = await self._call(
            view,
            "tools/call",
            {"name": "list_scripts", "arguments": {}},
            msg_id=3,
        )
        assert "Error" in result["result"]["content"][0]["text"]

    async def test_dashboard_config_round_trip(self, view, populated_hass):
        """Test get/save/delete dashboard config round-trip."""
        dashboard_store = {}

        mock_dashboard = AsyncMock()
        mock_dashboard.async_load.return_value = dict(dashboard_store)
        mock_dashboard.config = {"mode": "storage", "title": "Energy", "icon": "mdi:flash"}

        async def mock_save(config):
            dashboard_store.clear()
            dashboard_store.update(config)

        async def mock_delete():
            dashboard_store.clear()

        mock_dashboard.async_save = AsyncMock(side_effect=mock_save)
        mock_dashboard.async_delete = AsyncMock(side_effect=mock_delete)

        lovelace_data = Mock()
        lovelace_data.dashboards = {"energy": mock_dashboard}
        populated_hass.data = {"lovelace": lovelace_data}

        # Get (empty)
        result = await self._call(
            view,
            "tools/call",
            {"name": "get_dashboard_config", "arguments": {"url_path": "energy"}},
            msg_id=120,
        )
        config = json.loads(result["result"]["content"][0]["text"])
        assert config == {}

        # Save
        new_config = {"views": [{"title": "Energy", "cards": []}]}
        result = await self._call(
            view,
            "tools/call",
            {
                "name": "save_dashboard_config",
                "arguments": {"url_path": "energy", "config": new_config},
            },
            msg_id=121,
        )
        assert "Successfully saved" in result["result"]["content"][0]["text"]
        assert dashboard_store == new_config

        # Get (after save) — update mock to return saved data
        mock_dashboard.async_load.return_value = dict(dashboard_store)
        result = await self._call(
            view,
            "tools/call",
            {"name": "get_dashboard_config", "arguments": {"url_path": "energy"}},
            msg_id=122,
        )
        config = json.loads(result["result"]["content"][0]["text"])
        assert config["views"][0]["title"] == "Energy"

        # Delete config
        result = await self._call(
            view,
            "tools/call",
            {"name": "delete_dashboard_config", "arguments": {"url_path": "energy"}},
            msg_id=123,
        )
        assert "Successfully deleted config" in result["result"]["content"][0]["text"]
        assert dashboard_store == {}

    # ── New tools: fire_event, search_entities, get_logbook ──────────

    async def test_fire_event(self, view, populated_hass):
        """Test firing an event on the event bus."""
        populated_hass.bus = Mock()
        populated_hass.bus.async_fire = Mock()

        result = await self._call(
            view,
            "tools/call",
            {
                "name": "fire_event",
                "arguments": {
                    "event_type": "test_event",
                    "event_data": {"source": "test"},
                },
            },
            msg_id=200,
        )
        assert "Successfully fired event" in result["result"]["content"][0]["text"]
        populated_hass.bus.async_fire.assert_called_once_with("test_event", {"source": "test"})

    async def test_search_entities_by_query(self, view, populated_hass, mock_entity_registry):
        """Test searching entities by friendly name."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_entity_registry,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            result = await self._call(
                view,
                "tools/call",
                {"name": "search_entities", "arguments": {"query": "bedroom"}},
                msg_id=201,
            )
        data = json.loads(result["result"]["content"][0]["text"])
        entity_ids = [e["entity_id"] for e in data]
        assert "light.bedroom" in entity_ids

    async def test_search_entities_by_device_class(
        self, view, populated_hass, mock_entity_registry
    ):
        """Test searching entities by device class."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_entity_registry,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            result = await self._call(
                view,
                "tools/call",
                {
                    "name": "search_entities",
                    "arguments": {"device_class": "temperature"},
                },
                msg_id=202,
            )
        data = json.loads(result["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "sensor.temperature"
        assert data[0]["device_class"] == "temperature"

    async def test_search_entities_by_alias(self, view, populated_hass, mock_entity_registry):
        """Test searching entities matches aliases."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.er.async_get",
                return_value=mock_entity_registry,
            ),
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=Mock(),
            ),
        ):
            result = await self._call(
                view,
                "tools/call",
                {"name": "search_entities", "arguments": {"query": "lounge"}},
                msg_id=203,
            )
        data = json.loads(result["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "light.living_room"

    # ── New resources: devices, services, floors ─────────────────────

    async def test_devices_consistency_across_tools_and_resources(self, view, mock_device_registry):
        """Verify list_devices tool and hass://devices resource return consistent data."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.tools.entities.dr.async_get",
                return_value=mock_device_registry,
            ),
            patch(
                "custom_components.mcp_server_http_transport.resources.dr.async_get",
                return_value=mock_device_registry,
            ),
        ):
            tool_result = await self._call(
                view,
                "tools/call",
                {"name": "list_devices", "arguments": {}},
                msg_id=210,
            )
            tool_data = json.loads(tool_result["result"]["content"][0]["text"])

            resource_result = await self._call(
                view,
                "resources/read",
                {"uri": "hass://devices"},
                msg_id=211,
            )
            resource_data = json.loads(resource_result["result"]["contents"][0]["text"])

        assert len(tool_data) == len(resource_data)
        tool_ids = {d["id"] for d in tool_data}
        resource_ids = {d["id"] for d in resource_data}
        assert tool_ids == resource_ids

    async def test_services_consistency_across_tools_and_resources(self, view, populated_hass):
        """Verify list_services tool and hass://services resource return consistent data."""
        tool_result = await self._call(
            view,
            "tools/call",
            {"name": "list_services", "arguments": {}},
            msg_id=212,
        )
        tool_data = json.loads(tool_result["result"]["content"][0]["text"])

        resource_result = await self._call(
            view,
            "resources/read",
            {"uri": "hass://services"},
            msg_id=213,
        )
        resource_data = json.loads(resource_result["result"]["contents"][0]["text"])

        assert tool_data == resource_data

    async def test_floors_resource(self, view):
        """Test reading floors resource."""
        mock_floor = Mock()
        mock_floor.floor_id = "ground_floor"
        mock_floor.name = "Ground Floor"
        mock_floor.icon = "mdi:home-floor-g"
        mock_floor.level = 0
        mock_floor.aliases = set()
        mock_registry = Mock()
        mock_registry.async_list_floors.return_value = [mock_floor]

        with patch(
            "custom_components.mcp_server_http_transport.resources.fr.async_get",
            return_value=mock_registry,
        ):
            result = await self._call(
                view,
                "resources/read",
                {"uri": "hass://floors"},
                msg_id=214,
            )

        data = json.loads(result["result"]["contents"][0]["text"])
        assert len(data) == 1
        assert data[0]["floor_id"] == "ground_floor"
        assert data[0]["level"] == 0

    # ── New prompts: automation_review, setup_guide ───────────────────

    async def test_prompts_setup_guide_with_real_entity(self, view):
        """Test setup_guide prompt includes entity domain and device class."""
        result = await self._call(
            view,
            "prompts/get",
            {"name": "setup_guide", "arguments": {"entity_id": "sensor.temperature"}},
            msg_id=220,
        )
        prompt = result["result"]
        text = prompt["messages"][0]["content"]["text"]
        assert "sensor" in text
        assert "temperature" in text
        assert "22.5" in text

    async def test_prompts_automation_review(self, view, populated_hass):
        """Test automation_review prompt fetches config and builds review."""
        mock_config = {
            "id": "test-auto",
            "alias": "Test Automation",
            "trigger": [{"platform": "state"}],
            "action": [{"service": "light.turn_on"}],
        }

        populated_hass.config.path = Mock(return_value="/config/automations.yaml")

        with patch(
            "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
            new_callable=AsyncMock,
            return_value=mock_config,
        ):
            result = await self._call(
                view,
                "prompts/get",
                {
                    "name": "automation_review",
                    "arguments": {"automation_id": "test-auto"},
                },
                msg_id=221,
            )

        text = result["result"]["messages"][0]["content"]["text"]
        assert "Test Automation" in text
        assert "light.turn_on" in text

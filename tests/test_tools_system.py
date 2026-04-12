"""Tests for system tools (get_config, render_template, get_history, fire_event, get_logbook)."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestToolsSystem:
    """Test system-related MCP tools."""

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
        from homeassistant.const import __version__ as HA_VERSION

        assert config["version"] == HA_VERSION
        assert config["time_zone"] == "Europe/Stockholm"
        assert config["unit_system"]["temperature"] == "°C"

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

    async def test_post_tools_call_fire_event(self, view, mock_hass):
        """Test POST with tools/call for fire_event."""
        mock_hass.bus = Mock()
        mock_hass.bus.async_fire = Mock()

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "fire_event",
                    "arguments": {
                        "event_type": "custom_event",
                        "event_data": {"key": "value"},
                    },
                },
                "id": 80,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully fired event" in body["result"]["content"][0]["text"]
        mock_hass.bus.async_fire.assert_called_once_with("custom_event", {"key": "value"})

    async def test_post_tools_call_fire_event_without_data(self, view, mock_hass):
        """Test fire_event without event_data defaults to empty dict."""
        mock_hass.bus = Mock()
        mock_hass.bus.async_fire = Mock()

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "fire_event",
                    "arguments": {"event_type": "simple_event"},
                },
                "id": 81,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        mock_hass.bus.async_fire.assert_called_once_with("simple_event", {})

    async def test_post_tools_call_fire_event_blocked_type(self, view, mock_hass):
        """Test fire_event rejects system event types."""
        mock_hass.bus = Mock()
        mock_hass.bus.async_fire = Mock()

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "fire_event",
                    "arguments": {"event_type": "homeassistant_stop"},
                },
                "id": 81,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "not allowed" in body["result"]["content"][0]["text"]
        mock_hass.bus.async_fire.assert_not_called()

    async def test_post_tools_call_fire_event_error(self, view, mock_hass):
        """Test fire_event when bus.async_fire raises."""
        mock_hass.bus = Mock()
        mock_hass.bus.async_fire = Mock(side_effect=Exception("Bus error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "fire_event",
                    "arguments": {"event_type": "bad_event"},
                },
                "id": 96,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error firing event" in body["result"]["content"][0]["text"]

    async def test_post_tools_call_get_logbook(self, view, mock_hass):
        """Test POST with tools/call for get_logbook."""
        mock_events = [{"when": "2024-01-01T12:00:00", "name": "Light", "entity_id": "light.test"}]
        mock_processor = Mock()
        mock_processor.get_events.return_value = mock_events

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_events)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_logbook",
                    "arguments": {
                        "start_time": "2024-01-01T00:00:00",
                        "entity_id": "light.test",
                    },
                },
                "id": 85,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.logbook.processor.EventProcessor",
                return_value=mock_processor,
            ),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["entity_id"] == "light.test"

    async def test_post_tools_call_get_logbook_without_entity_id(self, view, mock_hass):
        """Test get_logbook without entity_id returns all entries."""
        mock_events = [
            {"when": "2024-01-01T12:00:00", "name": "Light", "entity_id": "light.a"},
            {"when": "2024-01-01T13:00:00", "name": "Switch", "entity_id": "switch.b"},
        ]
        mock_processor = Mock()
        mock_processor.get_events.return_value = mock_events

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_events)

        mock_event_processor_cls = Mock(return_value=mock_processor)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_logbook",
                    "arguments": {"start_time": "2024-01-01T00:00:00"},
                },
                "id": 85,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.logbook.processor.EventProcessor",
                mock_event_processor_cls,
            ),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        # Verify entity_ids=None was passed when no entity_id argument
        call_kwargs = mock_event_processor_cls.call_args
        assert call_kwargs[1]["entity_ids"] is None

    async def test_post_tools_call_get_logbook_with_end_time(self, view, mock_hass):
        """Test get_logbook with explicit end_time."""
        mock_events = []
        mock_processor = Mock()
        mock_processor.get_events.return_value = mock_events

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_events)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_logbook",
                    "arguments": {
                        "start_time": "2024-01-01T00:00:00",
                        "end_time": "2024-01-02T00:00:00",
                    },
                },
                "id": 85,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.logbook.processor.EventProcessor",
                return_value=mock_processor,
            ),
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=mock_recorder,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data == []

    async def test_post_tools_call_get_logbook_error(self, view, mock_hass):
        """Test get_logbook when EventProcessor fails."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_logbook",
                    "arguments": {"start_time": "2024-01-01T00:00:00"},
                },
                "id": 97,
            }
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "homeassistant.components.logbook.processor.EventProcessor",
                side_effect=Exception("Logbook unavailable"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Error getting logbook" in body["result"]["content"][0]["text"]

"""Tests for statistics tools."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


class TestToolsStatistics:
    """Tests for tools/statistics.py tools."""

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

    async def test_post_tools_call_get_statistics(self, view, mock_hass):
        """Test POST with tools/call for get_statistics."""
        mock_stats = {
            "sensor.energy": [
                {
                    "start": "2024-01-01T00:00:00",
                    "mean": 100.5,
                    "min": 90.0,
                    "max": 110.0,
                },
                {
                    "start": "2024-01-01T01:00:00",
                    "mean": 105.0,
                    "min": 95.0,
                    "max": 115.0,
                },
            ]
        }

        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_stats)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_statistics",
                    "arguments": {
                        "entity_id": "sensor.energy",
                        "start_time": "2024-01-01T00:00:00",
                    },
                },
                "id": 212,
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
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["mean"] == 100.5

    async def test_post_tools_call_get_statistics_invalid_period(self, view, mock_hass):
        """Test POST with tools/call for get_statistics with invalid period."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_statistics",
                    "arguments": {
                        "entity_id": "sensor.energy",
                        "start_time": "2024-01-01T00:00:00",
                        "period": "invalid",
                    },
                },
                "id": 213,
            }
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Invalid period" in text

    async def test_post_tools_call_get_statistics_error(self, view, mock_hass):
        """Test get_statistics when recorder raises."""
        mock_recorder = Mock()
        mock_recorder.async_add_executor_job = AsyncMock(side_effect=Exception("Recorder error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_statistics",
                    "arguments": {
                        "entity_id": "sensor.energy",
                        "start_time": "2024-01-01T00:00:00",
                    },
                },
                "id": 254,
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
        text = body["result"]["content"][0]["text"]
        assert "Error getting statistics" in text

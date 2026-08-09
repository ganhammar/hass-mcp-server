"""Tests for statistics tools."""

import asyncio
import json
from contextlib import ExitStack
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


async def _run_tool(view, name, arguments, recorder=None, extra_patches=()):
    """Drive a tools/call through the HTTP view and return the response text."""
    request = Mock()
    request.headers = {"Authorization": "Bearer valid_token"}
    request.json = AsyncMock(
        return_value={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": 1,
        }
    )

    with ExitStack() as stack:
        stack.enter_context(patch.object(view, "_validate_token", return_value={"sub": "u"}))
        if recorder is not None:
            stack.enter_context(
                patch(
                    "homeassistant.components.recorder.get_instance",
                    return_value=recorder,
                )
            )
        for extra in extra_patches:
            stack.enter_context(extra)
        response = await view.post(request)

    assert response.status == 200
    body = json.loads(response.body)
    return body["result"]["content"][0]["text"]


class TestListStatisticIds:
    """Tests for the list_statistic_ids tool."""

    @pytest.fixture
    def view(self):
        hass = Mock()
        return MCPEndpointView(hass, Mock())

    async def test_lists_metadata(self, view):
        """Metadata is passed through verbatim, including external (colon) IDs."""
        metadata = [
            {
                "statistic_id": "sensor.energy",
                "source": "recorder",
                "has_sum": True,
                "statistics_unit_of_measurement": "kWh",
            },
            {
                "statistic_id": "tibber:energy",
                "source": "tibber",
                "has_sum": True,
                "statistics_unit_of_measurement": "kWh",
            },
        ]
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(return_value=metadata)

        text = await _run_tool(view, "list_statistic_ids", {}, recorder)
        data = json.loads(text)
        assert {row["statistic_id"] for row in data} == {"sensor.energy", "tibber:energy"}

    async def test_filters_by_ids_and_type(self, view):
        """statistic_ids and a valid statistic_type reach the recorder call."""
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(return_value=[])

        await _run_tool(
            view,
            "list_statistic_ids",
            {"statistic_ids": ["sensor.energy"], "statistic_type": "sum"},
            recorder,
        )

        args = recorder.async_add_executor_job.await_args.args
        # (func, hass, id_set, statistic_type)
        assert args[2] == {"sensor.energy"}
        assert args[3] == "sum"

    async def test_rejects_invalid_type(self, view):
        text = await _run_tool(
            view, "list_statistic_ids", {"statistic_type": "median"}, recorder=Mock()
        )
        assert "Invalid statistic_type" in text

    async def test_error_surfaces(self, view):
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(side_effect=Exception("boom"))
        text = await _run_tool(view, "list_statistic_ids", {}, recorder)
        assert "Error listing statistic IDs" in text


class TestValidateStatistics:
    """Tests for the validate_statistics tool."""

    @pytest.fixture
    def view(self):
        hass = Mock()
        return MCPEndpointView(hass, Mock())

    async def test_reports_issues(self, view):
        """ValidationIssue objects are serialized via their as_dict()."""
        issue = Mock()
        issue.as_dict = Mock(
            return_value={
                "type": "unsupported_unit_metadata",
                "data": {"statistic_id": "sensor.energy"},
            }
        )
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(return_value={"sensor.energy": [issue]})

        text = await _run_tool(view, "validate_statistics", {}, recorder)
        data = json.loads(text)
        assert data["sensor.energy"][0]["type"] == "unsupported_unit_metadata"

    async def test_no_issues(self, view):
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(return_value={})
        text = await _run_tool(view, "validate_statistics", {}, recorder)
        assert json.loads(text) == {}

    async def test_error_surfaces(self, view):
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(side_effect=Exception("boom"))
        text = await _run_tool(view, "validate_statistics", {}, recorder)
        assert "Error validating statistics" in text


class TestAdjustStatistics:
    """Tests for the adjust_statistics tool."""

    @pytest.fixture
    def view(self):
        hass = Mock()
        return MCPEndpointView(hass, Mock())

    def _recorder(self, metadata):
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(return_value=metadata)
        recorder.async_adjust_statistics = Mock()
        return recorder

    async def test_adjusts_with_default_unit(self, view):
        """Omitting adjustment_unit defaults to the statistic's own unit."""
        recorder = self._recorder(
            [{"has_sum": True, "statistics_unit_of_measurement": "kWh", "unit_class": "energy"}]
        )
        text = await _run_tool(
            view,
            "adjust_statistics",
            {
                "statistic_id": "sensor.energy",
                "start_time": "2024-01-01T00:00:00+00:00",
                "adjustment": -5.0,
            },
            recorder,
        )
        assert "Queued an adjustment" in text
        call = recorder.async_adjust_statistics.call_args
        assert call.args[0] == "sensor.energy"
        assert call.args[2] == -5.0
        assert call.args[3] == "kWh"

    async def test_invalid_start_time(self, view):
        text = await _run_tool(
            view,
            "adjust_statistics",
            {"statistic_id": "sensor.energy", "start_time": "not-a-date", "adjustment": 1},
            self._recorder([{"has_sum": True, "statistics_unit_of_measurement": "kWh"}]),
        )
        assert "Invalid start_time" in text

    async def test_unknown_statistic_id(self, view):
        text = await _run_tool(
            view,
            "adjust_statistics",
            {
                "statistic_id": "sensor.nope",
                "start_time": "2024-01-01T00:00:00+00:00",
                "adjustment": 1,
            },
            self._recorder([]),
        )
        assert "Unknown statistic ID" in text

    async def test_refuses_without_sum(self, view):
        text = await _run_tool(
            view,
            "adjust_statistics",
            {
                "statistic_id": "sensor.temp",
                "start_time": "2024-01-01T00:00:00+00:00",
                "adjustment": 1,
            },
            self._recorder([{"has_sum": False, "statistics_unit_of_measurement": "°C"}]),
        )
        assert "no sum to adjust" in text

    async def test_refuses_unit_mismatch(self, view):
        text = await _run_tool(
            view,
            "adjust_statistics",
            {
                "statistic_id": "sensor.energy",
                "start_time": "2024-01-01T00:00:00+00:00",
                "adjustment": 1,
                "adjustment_unit": "Wh",
            },
            self._recorder([{"has_sum": True, "statistics_unit_of_measurement": "kWh"}]),
        )
        assert "must match the statistic's unit" in text

    async def test_error_listing_metadata(self, view):
        recorder = Mock()
        recorder.async_add_executor_job = AsyncMock(side_effect=Exception("boom"))
        text = await _run_tool(
            view,
            "adjust_statistics",
            {
                "statistic_id": "sensor.energy",
                "start_time": "2024-01-01T00:00:00+00:00",
                "adjustment": 1,
            },
            recorder,
        )
        assert "Error adjusting statistics" in text

    async def test_error_from_adjust_call(self, view):
        recorder = self._recorder([{"has_sum": True, "statistics_unit_of_measurement": "kWh"}])
        recorder.async_adjust_statistics = Mock(side_effect=Exception("boom"))
        text = await _run_tool(
            view,
            "adjust_statistics",
            {
                "statistic_id": "sensor.energy",
                "start_time": "2024-01-01T00:00:00+00:00",
                "adjustment": 1,
            },
            recorder,
        )
        assert "Error adjusting statistics" in text


class TestClearStatistics:
    """Tests for the clear_statistics tool."""

    @pytest.fixture
    def view(self):
        hass = Mock()
        hass.loop = asyncio.get_event_loop()
        return MCPEndpointView(hass, Mock())

    async def test_clears_when_confirmed(self, view):
        view.hass.loop = asyncio.get_running_loop()
        recorder = Mock()
        recorder.async_clear_statistics = Mock(side_effect=lambda ids, on_done: on_done())

        text = await _run_tool(
            view,
            "clear_statistics",
            {"statistic_ids": ["sensor.energy"], "confirm": True},
            recorder,
        )
        assert "Cleared statistics for: sensor.energy" in text

    async def test_requires_confirm(self, view):
        text = await _run_tool(
            view,
            "clear_statistics",
            {"statistic_ids": ["sensor.energy"], "confirm": False},
            recorder=Mock(),
        )
        assert "requires confirm=true" in text

    async def test_rejects_empty_ids(self, view):
        text = await _run_tool(
            view,
            "clear_statistics",
            {"statistic_ids": [], "confirm": True},
            recorder=Mock(),
        )
        assert "must not be empty" in text

    async def test_timeout(self, view):
        view.hass.loop = asyncio.get_running_loop()
        recorder = Mock()
        # Never invoke on_done, so the wait times out.
        recorder.async_clear_statistics = Mock()

        text = await _run_tool(
            view,
            "clear_statistics",
            {"statistic_ids": ["sensor.energy"], "confirm": True},
            recorder,
            extra_patches=[
                patch(
                    "custom_components.mcp_server_http_transport.tools.statistics."
                    "_CLEAR_STATISTICS_TIMEOUT",
                    0.01,
                )
            ],
        )
        assert "timed out" in text

    async def test_error_surfaces(self, view):
        view.hass.loop = asyncio.get_running_loop()
        recorder = Mock()
        recorder.async_clear_statistics = Mock(side_effect=Exception("boom"))
        text = await _run_tool(
            view,
            "clear_statistics",
            {"statistic_ids": ["sensor.energy"], "confirm": True},
            recorder,
        )
        assert "Error clearing statistics" in text

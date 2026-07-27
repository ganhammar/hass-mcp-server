"""Tests for calendar MCP tools."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.calendar.const import DATA_COMPONENT, CalendarEntityFeature

from custom_components.mcp_server_http_transport.tools import calendar as calendar_mod


@pytest.fixture
def mock_entity():
    entity = Mock()
    entity.supported_features = CalendarEntityFeature.CREATE_EVENT
    entity.async_create_event = AsyncMock()
    return entity


@pytest.fixture
def mock_hass(mock_entity):
    hass = Mock()
    component = Mock()
    component.get_entity.return_value = mock_entity
    hass.data = {DATA_COMPONENT: component}
    return hass, component, mock_entity


class TestCreateRecurringCalendarEvent:
    @pytest.mark.asyncio
    async def test_creates_series(self, mock_hass):
        hass, component, entity = mock_hass
        result = await calendar_mod.create_recurring_calendar_event(
            hass,
            {
                "entity_id": "calendar.cursor_prompts",
                "summary": "Cursor: weekly job",
                "dtstart": "2026-08-04T09:00:00",
                "dtend": "2026-08-04T09:05:00",
                "description": "Do the thing",
                "freq": "WEEKLY",
                "byday": "MO",
            },
        )
        entity.async_create_event.assert_awaited_once()
        kwargs = entity.async_create_event.await_args.kwargs
        assert kwargs["summary"] == "Cursor: weekly job"
        assert kwargs["description"] == "Do the thing"
        assert kwargs["rrule"] == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"
        assert isinstance(kwargs["dtstart"], datetime)
        payload = json.loads(result["content"][0]["text"])
        assert payload["method"] == "calendar_entity_async_create_event"
        assert payload["rrule"] == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"

    @pytest.mark.asyncio
    async def test_unknown_entity(self, mock_hass):
        hass, component, _entity = mock_hass
        component.get_entity.return_value = None
        result = await calendar_mod.create_recurring_calendar_event(
            hass,
            {
                "entity_id": "calendar.missing",
                "summary": "Test",
                "dtstart": "2026-08-04T09:00:00",
                "dtend": "2026-08-04T09:05:00",
                "rrule": "FREQ=DAILY;COUNT=1",
            },
        )
        assert "not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_invalid_rrule(self, mock_hass):
        hass, _component, entity = mock_hass
        result = await calendar_mod.create_recurring_calendar_event(
            hass,
            {
                "entity_id": "calendar.cursor_prompts",
                "summary": "Test",
                "dtstart": "2026-08-04T09:00:00",
                "dtend": "2026-08-04T09:05:00",
                "rrule": "FREQ=HOURLY",
            },
        )
        entity.async_create_event.assert_not_called()
        assert "Invalid or missing FREQ" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_read_only_calendar(self, mock_hass):
        hass, component, entity = mock_hass
        entity.supported_features = 0
        result = await calendar_mod.create_recurring_calendar_event(
            hass,
            {
                "entity_id": "calendar.readonly",
                "summary": "Test",
                "dtstart": "2026-08-04T09:00:00",
                "dtend": "2026-08-04T09:05:00",
                "rrule": "FREQ=DAILY;COUNT=1",
            },
        )
        entity.async_create_event.assert_not_called()
        assert "does not support event creation" in result["content"][0]["text"]

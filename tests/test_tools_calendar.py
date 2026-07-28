"""Tests for calendar MCP tools."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT, CalendarEntityFeature

from custom_components.mcp_server_http_transport.tools import calendar as calendar_mod


def _utc(*args: int) -> datetime:
    """Build a timezone-aware UTC datetime (HA CalendarEvent rejects naive)."""
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def mock_entity():
    entity = Mock()
    entity.supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT
    )
    entity.async_create_event = AsyncMock()
    entity.async_delete_event = AsyncMock()
    entity.async_get_events = AsyncMock(return_value=[])
    return entity


@pytest.fixture
def mock_hass(mock_entity):
    hass = Mock()
    component = Mock()
    component.get_entity.return_value = mock_entity
    hass.data = {DATA_COMPONENT: component}
    hass.services = Mock()
    hass.services.async_call = AsyncMock()
    return hass, component, mock_entity


class TestCreateRecurringCalendarEvent:
    @pytest.mark.asyncio
    async def test_creates_series(self, mock_hass):
        hass, component, entity = mock_hass
        result = await calendar_mod.create_recurring_calendar_event(
            hass,
            {
                "entity_id": "calendar.birthdays",
                "summary": "Weekly reminder",
                "dtstart": "2026-08-04T09:00:00",
                "dtend": "2026-08-04T09:05:00",
                "description": "Do the thing",
                "freq": "WEEKLY",
                "byday": "MO",
            },
        )
        entity.async_create_event.assert_awaited_once()
        kwargs = entity.async_create_event.await_args.kwargs
        assert kwargs["summary"] == "Weekly reminder"
        assert kwargs["description"] == "Do the thing"
        assert kwargs["rrule"] == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"
        assert isinstance(kwargs["dtstart"], datetime)
        payload = json.loads(result["content"][0]["text"])
        assert payload["method"] == "calendar_entity_async_create_event"
        assert payload["rrule"] == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"

    @pytest.mark.asyncio
    async def test_duration_minutes(self, mock_hass):
        hass, _component, entity = mock_hass
        await calendar_mod.create_recurring_calendar_event(
            hass,
            {
                "entity_id": "calendar.birthdays",
                "summary": "Daily",
                "dtstart": "2026-08-04T09:00:00",
                "duration_minutes": 10,
                "freq": "DAILY",
                "count": 1,
            },
        )
        kwargs = entity.async_create_event.await_args.kwargs
        assert kwargs["dtend"] == kwargs["dtstart"] + timedelta(minutes=10)

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
                "entity_id": "calendar.birthdays",
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


class TestCreateCalendarEvent:
    @pytest.mark.asyncio
    async def test_creates_one_off(self, mock_hass):
        hass, _component, entity = mock_hass
        result = await calendar_mod.create_calendar_event(
            hass,
            {
                "entity_id": "calendar.birthdays",
                "summary": "One-off test event",
                "dtstart": "2026-08-04T09:00:00",
                "duration_minutes": 5,
                "description": "Hello",
            },
        )
        entity.async_create_event.assert_awaited_once()
        kwargs = entity.async_create_event.await_args.kwargs
        assert "rrule" not in kwargs
        assert kwargs["description"] == "Hello"
        payload = json.loads(result["content"][0]["text"])
        assert payload["method"] == "calendar_entity_async_create_event"
        assert payload["reload_triggers"] == "false"
        assert payload["triggers_reloaded"] is False
        hass.services.async_call.assert_not_called()


class TestReloadTriggers:
    @pytest.fixture(autouse=True)
    def _utc_local(self):
        """Treat naive datetimes as UTC so lead-time math is stable in tests."""

        def as_utc(value: datetime) -> datetime:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value

        with patch.object(calendar_mod.dt_util, "as_local", side_effect=as_utc):
            yield

    @pytest.mark.asyncio
    async def test_default_false_skips_reload(self, mock_hass):
        hass, _component, _entity = mock_hass
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 4, 8, 55, 0)):
            # Event 5 minutes away — would reload under auto, but default is false
            result = await calendar_mod.create_calendar_event(
                hass,
                {
                    "entity_id": "calendar.birthdays",
                    "summary": "Soon",
                    "dtstart": "2026-08-04T09:00:00",
                    "duration_minutes": 5,
                },
            )
        payload = json.loads(result["content"][0]["text"])
        assert payload["reload_triggers"] == "false"
        assert payload["triggers_reloaded"] is False
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_reloads_within_window(self, mock_hass):
        hass, _component, _entity = mock_hass
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 4, 8, 55, 0)):
            result = await calendar_mod.create_calendar_event(
                hass,
                {
                    "entity_id": "calendar.birthdays",
                    "summary": "Soon",
                    "dtstart": "2026-08-04T09:00:00",
                    "duration_minutes": 5,
                    "reload_triggers": "auto",
                },
            )
        payload = json.loads(result["content"][0]["text"])
        assert payload["reload_triggers"] == "auto"
        assert payload["triggers_reloaded"] is True
        assert hass.services.async_call.await_count == 2

    @pytest.mark.asyncio
    async def test_auto_skips_outside_window(self, mock_hass):
        hass, _component, _entity = mock_hass
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 4, 8, 0, 0)):
            result = await calendar_mod.create_calendar_event(
                hass,
                {
                    "entity_id": "calendar.birthdays",
                    "summary": "Later",
                    "dtstart": "2026-08-04T09:00:00",
                    "duration_minutes": 5,
                    "reload_triggers": "auto",
                },
            )
        payload = json.loads(result["content"][0]["text"])
        assert payload["triggers_reloaded"] is False
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_true_always_reloads(self, mock_hass):
        hass, _component, _entity = mock_hass
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 1, 12, 0, 0)):
            result = await calendar_mod.create_calendar_event(
                hass,
                {
                    "entity_id": "calendar.birthdays",
                    "summary": "Far ahead",
                    "dtstart": "2026-08-04T09:00:00",
                    "duration_minutes": 5,
                    "reload_triggers": "true",
                },
            )
        payload = json.loads(result["content"][0]["text"])
        assert payload["reload_triggers"] == "true"
        assert payload["triggers_reloaded"] is True
        assert hass.services.async_call.await_count == 2

    @pytest.mark.asyncio
    async def test_invalid_mode(self, mock_hass):
        hass, _component, entity = mock_hass
        result = await calendar_mod.create_calendar_event(
            hass,
            {
                "entity_id": "calendar.birthdays",
                "summary": "Bad",
                "dtstart": "2026-08-04T09:00:00",
                "duration_minutes": 5,
                "reload_triggers": "sometimes",
            },
        )
        entity.async_create_event.assert_not_called()
        assert "reload_triggers must be false, true, or auto" in result["content"][0]["text"]

    def test_parse_accepts_bool(self):
        assert calendar_mod._parse_reload_triggers(True) == "true"
        assert calendar_mod._parse_reload_triggers(False) == "false"
        assert calendar_mod._parse_reload_triggers(None) == "false"


class TestListCalendarEvents:
    @pytest.mark.asyncio
    async def test_lists_without_full_description(self, mock_hass):
        hass, _component, entity = mock_hass
        entity.async_get_events.return_value = [
            CalendarEvent(
                start=_utc(2026, 8, 4, 9, 0, 0),
                end=_utc(2026, 8, 4, 9, 5, 0),
                summary="Team standup",
                description="Bring cake and candles",
                uid="abc123",
            )
        ]
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 1, 12, 0, 0)):
            result = await calendar_mod.list_calendar_events(
                hass,
                {"entity_id": "calendar.birthdays", "days": 7},
            )
        payload = json.loads(result["content"][0]["text"])
        assert payload["count"] == 1
        assert payload["events"][0]["uid"] == "abc123"
        assert "description" not in payload["events"][0]
        assert payload["events"][0]["description_preview"] == "Bring cake and candles"

    @pytest.mark.asyncio
    async def test_include_description(self, mock_hass):
        hass, _component, entity = mock_hass
        entity.async_get_events.return_value = [
            CalendarEvent(
                start=_utc(2026, 8, 4, 9, 0, 0),
                end=_utc(2026, 8, 4, 9, 5, 0),
                summary="Team standup",
                description="Full birthday party notes",
                uid="abc123",
            )
        ]
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 1, 12, 0, 0)):
            result = await calendar_mod.list_calendar_events(
                hass,
                {
                    "entity_id": "calendar.birthdays",
                    "days": 7,
                    "include_description": True,
                },
            )
        payload = json.loads(result["content"][0]["text"])
        assert payload["events"][0]["description"] == "Full birthday party notes"
        assert "description_preview" not in payload["events"][0]


class TestDeleteCalendarEvents:
    @pytest.mark.asyncio
    async def test_requires_one_filter(self, mock_hass):
        hass, _component, _entity = mock_hass
        result = await calendar_mod.delete_calendar_events(
            hass,
            {"entity_id": "calendar.birthdays"},
        )
        assert "exactly one" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_delete_by_uid(self, mock_hass):
        hass, _component, entity = mock_hass
        result = await calendar_mod.delete_calendar_events(
            hass,
            {"entity_id": "calendar.birthdays", "uid": "series-1"},
        )
        entity.async_delete_event.assert_awaited_once_with("series-1")
        payload = json.loads(result["content"][0]["text"])
        assert payload["deleted_count"] == 1

    @pytest.mark.asyncio
    async def test_dry_run_by_summary(self, mock_hass):
        hass, _component, entity = mock_hass
        entity.async_get_events.return_value = [
            CalendarEvent(
                start=_utc(2026, 8, 4, 9, 0, 0),
                end=_utc(2026, 8, 4, 9, 5, 0),
                summary="Weekly earnings review",
                uid="uid-weekly",
            )
        ]
        with patch.object(calendar_mod.dt_util, "now", return_value=_utc(2026, 8, 1, 12, 0, 0)):
            result = await calendar_mod.delete_calendar_events(
                hass,
                {
                    "entity_id": "calendar.birthdays",
                    "summary_contains": "weekly",
                    "dry_run": True,
                },
            )
        entity.async_delete_event.assert_not_called()
        payload = json.loads(result["content"][0]["text"])
        assert payload["dry_run"] is True
        assert payload["matches"] == [{"uid": "uid-weekly", "summary": "Weekly earnings review"}]

    @pytest.mark.asyncio
    async def test_summary_contains_min_length(self, mock_hass):
        hass, _component, _entity = mock_hass
        result = await calendar_mod.delete_calendar_events(
            hass,
            {
                "entity_id": "calendar.birthdays",
                "summary_contains": "ab",
            },
        )
        assert "at least 3" in result["content"][0]["text"]

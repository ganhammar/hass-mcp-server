"""Calendar tools — recurring event create via CalendarEntity API."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.calendar.const import (
    DATA_COMPONENT,
    EVENT_DESCRIPTION,
    EVENT_END,
    EVENT_LOCATION,
    EVENT_RRULE,
    EVENT_START,
    EVENT_SUMMARY,
    CalendarEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ..calendar_recurrence import recurrence_from_arguments
from . import _HAJSONEncoder, register_tool

_LOGGER = logging.getLogger(__name__)


def _parse_event_time(raw: str, field: str) -> datetime:
    value = raw.strip()
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"{field} must be ISO datetime (e.g. 2026-08-04T09:00:00), got {value!r}")
    return dt_util.as_local(parsed)


def _text_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, cls=_HAJSONEncoder),
            }
        ]
    }


def _error_text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


@register_tool(
    name="create_recurring_calendar_event",
    description=(
        "Create a recurring calendar event series with an RFC 5545 RRULE. "
        "Uses the calendar entity API (not calendar.create_event, which is one-off only). "
        "Local calendars support open-ended recurrence when count/until are omitted."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity (e.g. calendar.cursor_prompts)",
            },
            "summary": {
                "type": "string",
                "description": "Event title",
            },
            "dtstart": {
                "type": "string",
                "description": "First start, local ISO datetime (e.g. 2026-08-04T09:00:00)",
            },
            "dtend": {
                "type": "string",
                "description": "First end, local ISO datetime (exclusive, after dtstart)",
            },
            "description": {
                "type": "string",
                "description": "Event body (e.g. full Cursor agent prompt)",
            },
            "location": {
                "type": "string",
                "description": "Optional location",
            },
            "rrule": {
                "type": "string",
                "description": (
                    "RFC 5545 RRULE without RRULE: prefix "
                    "(e.g. FREQ=WEEKLY;BYDAY=MO). Overrides freq/interval/count/until/byday."
                ),
            },
            "freq": {
                "type": "string",
                "enum": ["DAILY", "WEEKLY", "MONTHLY", "YEARLY"],
                "description": "Recurrence frequency when rrule is omitted",
            },
            "interval": {
                "type": "integer",
                "description": "Every N periods (default 1)",
            },
            "count": {
                "type": "integer",
                "description": "Number of occurrences (omit for open-ended)",
            },
            "until": {
                "type": "string",
                "description": "Last date YYYY-MM-DD (exclusive end of series)",
            },
            "byday": {
                "type": "string",
                "description": "Weekly only: comma-separated weekdays MO,TU,...,SU",
            },
            "bymonthday": {
                "type": "integer",
                "description": "Monthly only: day of month 1-31",
            },
        },
        "required": ["entity_id", "summary", "dtstart", "dtend"],
    },
)
async def create_recurring_calendar_event(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Create a recurring calendar event series."""
    entity_id = arguments["entity_id"]
    if not entity_id.startswith("calendar."):
        return _error_text(f"entity_id must be a calendar entity; got {entity_id!r}")

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        return _error_text("Calendar component is not loaded")

    entity = component.get_entity(entity_id)
    if entity is None:
        return _error_text(f"Calendar entity {entity_id} not found")

    features = entity.supported_features or 0
    if not features & CalendarEntityFeature.CREATE_EVENT:
        return _error_text(f"Calendar {entity_id} does not support event creation")

    try:
        dtstart = _parse_event_time(arguments["dtstart"], "dtstart")
        dtend = _parse_event_time(arguments["dtend"], "dtend")
        if dtend <= dtstart:
            return _error_text("dtend must be after dtstart")
        rrule = recurrence_from_arguments(arguments)
    except ValueError as exc:
        return _error_text(str(exc))

    event: dict[str, Any] = {
        EVENT_START: dtstart,
        EVENT_END: dtend,
        EVENT_SUMMARY: arguments["summary"],
        EVENT_RRULE: rrule,
    }
    if description := arguments.get("description"):
        event[EVENT_DESCRIPTION] = description
    if location := arguments.get("location"):
        event[EVENT_LOCATION] = location

    try:
        await entity.async_create_event(**event)
    except HomeAssistantError as exc:
        _LOGGER.error("create_recurring_calendar_event failed: %s", exc)
        return _error_text(f"Failed to create recurring event: {exc}")

    return _text_result(
        {
            "entity_id": entity_id,
            "summary": arguments["summary"],
            "dtstart": dtstart.isoformat(),
            "dtend": dtend.isoformat(),
            "rrule": rrule,
            "method": "calendar_entity_async_create_event",
        }
    )

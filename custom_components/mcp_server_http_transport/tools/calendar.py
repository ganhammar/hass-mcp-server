"""Calendar tools — list, create, and delete events via CalendarEntity API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
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
from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    _HAJSONEncoder,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)

DESCRIPTION_PREVIEW_MAX = 80
DELETE_SUMMARY_CONTAINS_MIN = 3
DEFAULT_LIST_DAYS = 14
DEFAULT_PURGE_LOOKAHEAD_DAYS = 400
CALENDAR_TRIGGER_RELOAD_MINUTES = 15


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


def _minutes_until(start: datetime) -> float:
    return (start - dt_util.now()).total_seconds() / 60


def _lead_time_notice(start: datetime) -> str | None:
    lead = _minutes_until(start)
    if lead < 0:
        return "Start time is in the past; calendar automations will not fire."
    if lead < CALENDAR_TRIGGER_RELOAD_MINUTES:
        return (
            f"Event starts in {lead:.0f} minutes. Calendar automations may miss it "
            "unless triggers are reloaded (set reload_triggers=auto or true)."
        )
    if lead < 20:
        return f"Event starts in {lead:.0f} minutes; 20+ minutes ahead is safer for tests."
    return None


def _parse_reload_triggers(raw: Any) -> str:
    """Return reload mode: false (default), auto, or true."""
    if raw is None:
        return "false"
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("true", "false", "auto"):
            return value
    raise ValueError("reload_triggers must be false, true, or auto")


_RELOAD_TRIGGERS_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "boolean"},
        {"type": "string", "enum": ["true", "false", "auto"]},
    ],
    "description": (
        "Whether to reload the calendar config entry and all automations after create "
        "so calendar triggers pick up the new event. "
        f"'false' (default): never reload. "
        f"'auto': reload only when start is within {CALENDAR_TRIGGER_RELOAD_MINUTES} minutes. "
        "'true': always reload after create. Boolean true/false also accepted."
    ),
}


def _get_calendar_entity(hass: HomeAssistant, entity_id: str) -> CalendarEntity | dict[str, Any]:
    if not entity_id.startswith("calendar."):
        return _error_text(f"entity_id must be a calendar entity; got {entity_id!r}")

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        return _error_text("Calendar component is not loaded")

    entity = component.get_entity(entity_id)
    if entity is None:
        return _error_text(f"Calendar entity {entity_id} not found")
    return entity


def _has_feature(entity: CalendarEntity, feature: CalendarEntityFeature) -> bool:
    return bool((entity.supported_features or 0) & feature)


def _serialize_event(
    event: CalendarEvent,
    *,
    include_description: bool,
    description_preview_chars: int,
) -> dict[str, Any]:
    start = (
        event.start_datetime_local.isoformat()
        if hasattr(event, "start_datetime_local")
        else (
            event.start.isoformat()
            if isinstance(event.start, datetime)
            else event.start.isoformat()
        )
    )
    end = (
        event.end_datetime_local.isoformat()
        if hasattr(event, "end_datetime_local")
        else (event.end.isoformat() if isinstance(event.end, datetime) else event.end.isoformat())
    )
    payload: dict[str, Any] = {
        "start": start,
        "end": end,
        "summary": event.summary,
        "uid": event.uid,
        "rrule": event.rrule,
        "recurrence_id": event.recurrence_id,
        "all_day": event.all_day,
    }
    if include_description and event.description:
        payload["description"] = event.description
    elif event.description:
        preview_len = max(0, description_preview_chars)
        if preview_len and len(event.description) > preview_len:
            payload["description_preview"] = event.description[:preview_len] + "…"
        elif preview_len:
            payload["description_preview"] = event.description
    return payload


async def _reload_calendar_triggers(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "homeassistant",
        "reload_config_entry",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.services.async_call("automation", "reload", {}, blocking=True)


async def _maybe_reload_triggers(
    hass: HomeAssistant,
    entity_id: str,
    dtstart: datetime,
    *,
    reload_triggers: str,
) -> bool:
    """Reload calendar/automation triggers per mode (false / auto / true)."""
    if reload_triggers == "false":
        return False
    if reload_triggers == "auto" and _minutes_until(dtstart) >= CALENDAR_TRIGGER_RELOAD_MINUTES:
        return False
    await _reload_calendar_triggers(hass, entity_id)
    return True


def _resolve_event_window(arguments: dict[str, Any]) -> tuple[datetime, datetime]:
    now = dt_util.now()
    start = _parse_event_time(arguments["start"], "start") if arguments.get("start") else now
    if arguments.get("end"):
        end = _parse_event_time(arguments["end"], "end")
    else:
        days = int(arguments.get("days") or DEFAULT_LIST_DAYS)
        if days < 1:
            raise ValueError("days must be >= 1")
        end = start + timedelta(days=days)
    if end <= start:
        raise ValueError("end must be after start")
    return start, end


@register_tool(
    name="create_calendar_event",
    description=(
        "Create a one-off calendar event (no recurrence). Uses the calendar entity API "
        "(preferred over calendar.create_event for local calendars). "
        "Provide dtstart/dtend or dtstart with duration_minutes."
    ),
    annotations=ANNOTATION_NON_IDEMPOTENT,
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity (e.g. calendar.birthdays)",
            },
            "summary": {"type": "string", "description": "Event title"},
            "dtstart": {
                "type": "string",
                "description": "Start, local ISO datetime (e.g. 2026-08-04T09:00:00)",
            },
            "dtend": {
                "type": "string",
                "description": "End ISO datetime after dtstart; omit if duration_minutes set.",
            },
            "duration_minutes": {
                "type": "integer",
                "description": "Event length in minutes when dtend omitted (default 5)",
            },
            "description": {
                "type": "string",
                "description": "Event body (e.g. agenda notes)",
            },
            "location": {"type": "string", "description": "Optional location"},
            "reload_triggers": _RELOAD_TRIGGERS_SCHEMA,
        },
        "required": ["entity_id", "summary", "dtstart"],
    },
)
async def create_calendar_event(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a one-off calendar event."""
    entity_or_error = _get_calendar_entity(hass, arguments["entity_id"])
    if isinstance(entity_or_error, dict):
        return entity_or_error
    entity = entity_or_error

    if not _has_feature(entity, CalendarEntityFeature.CREATE_EVENT):
        return _error_text(f"Calendar {arguments['entity_id']} does not support event creation")

    try:
        dtstart = _parse_event_time(arguments["dtstart"], "dtstart")
        if arguments.get("dtend"):
            dtend = _parse_event_time(arguments["dtend"], "dtend")
        else:
            duration = int(arguments.get("duration_minutes") or 5)
            if duration < 1:
                raise ValueError("duration_minutes must be >= 1")
            dtend = dtstart + timedelta(minutes=duration)
        if dtend <= dtstart:
            return _error_text("dtend must be after dtstart")
        reload_mode = _parse_reload_triggers(arguments.get("reload_triggers"))
    except ValueError as exc:
        return _error_text(str(exc))

    event: dict[str, Any] = {
        EVENT_START: dtstart,
        EVENT_END: dtend,
        EVENT_SUMMARY: arguments["summary"],
    }
    if description := arguments.get("description"):
        event[EVENT_DESCRIPTION] = description
    if location := arguments.get("location"):
        event[EVENT_LOCATION] = location

    try:
        await entity.async_create_event(**event)
    except HomeAssistantError as exc:
        _LOGGER.error(
            "create_calendar_event failed for %s: %s",
            arguments["entity_id"],
            exc,
        )
        return _error_text(f"Failed to create event: {exc}")

    reloaded = await _maybe_reload_triggers(
        hass,
        arguments["entity_id"],
        dtstart,
        reload_triggers=reload_mode,
    )
    result: dict[str, Any] = {
        "entity_id": arguments["entity_id"],
        "summary": arguments["summary"],
        "dtstart": dtstart.isoformat(),
        "dtend": dtend.isoformat(),
        "method": "calendar_entity_async_create_event",
        "reload_triggers": reload_mode,
        "triggers_reloaded": reloaded,
    }
    if notice := _lead_time_notice(dtstart):
        result["lead_time_notice"] = notice
    return _text_result(result)


@register_tool(
    name="create_recurring_calendar_event",
    description=(
        "Create a recurring calendar event series with an RFC 5545 RRULE. "
        "Uses the calendar entity API (not calendar.create_event, which is one-off only). "
        "Local calendars support open-ended recurrence when count/until are omitted."
    ),
    annotations=ANNOTATION_NON_IDEMPOTENT,
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity (e.g. calendar.birthdays)",
            },
            "summary": {"type": "string", "description": "Event title"},
            "dtstart": {
                "type": "string",
                "description": "First start, local ISO datetime (e.g. 2026-08-04T09:00:00)",
            },
            "dtend": {
                "type": "string",
                "description": "First end, local ISO datetime (exclusive, after dtstart)",
            },
            "duration_minutes": {
                "type": "integer",
                "description": "When dtend omitted, length in minutes from dtstart (default 5)",
            },
            "description": {
                "type": "string",
                "description": "Event body (e.g. agenda notes)",
            },
            "location": {"type": "string", "description": "Optional location"},
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
            "reload_triggers": _RELOAD_TRIGGERS_SCHEMA,
        },
        "required": ["entity_id", "summary", "dtstart"],
    },
)
async def create_recurring_calendar_event(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Create a recurring calendar event series."""
    entity_or_error = _get_calendar_entity(hass, arguments["entity_id"])
    if isinstance(entity_or_error, dict):
        return entity_or_error
    entity = entity_or_error

    if not _has_feature(entity, CalendarEntityFeature.CREATE_EVENT):
        return _error_text(f"Calendar {arguments['entity_id']} does not support event creation")

    try:
        dtstart = _parse_event_time(arguments["dtstart"], "dtstart")
        if arguments.get("dtend"):
            dtend = _parse_event_time(arguments["dtend"], "dtend")
        else:
            duration = int(arguments.get("duration_minutes") or 5)
            if duration < 1:
                raise ValueError("duration_minutes must be >= 1")
            dtend = dtstart + timedelta(minutes=duration)
        if dtend <= dtstart:
            return _error_text("dtend must be after dtstart")
        rrule = recurrence_from_arguments(arguments)
        reload_mode = _parse_reload_triggers(arguments.get("reload_triggers"))
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
        _LOGGER.error(
            "create_recurring_calendar_event failed for %s: %s",
            arguments["entity_id"],
            exc,
        )
        return _error_text(f"Failed to create recurring event: {exc}")

    reloaded = await _maybe_reload_triggers(
        hass,
        arguments["entity_id"],
        dtstart,
        reload_triggers=reload_mode,
    )
    result: dict[str, Any] = {
        "entity_id": arguments["entity_id"],
        "summary": arguments["summary"],
        "dtstart": dtstart.isoformat(),
        "dtend": dtend.isoformat(),
        "rrule": rrule,
        "method": "calendar_entity_async_create_event",
        "reload_triggers": reload_mode,
        "triggers_reloaded": reloaded,
    }
    if notice := _lead_time_notice(dtstart):
        result["lead_time_notice"] = notice
    return _text_result(result)


@register_tool(
    name="list_calendar_events",
    description=(
        "List calendar events in a time window. Returns uid for delete operations. "
        "Descriptions are omitted by default; use include_description only when needed."
    ),
    annotations=ANNOTATION_READ_ONLY,
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity (e.g. calendar.birthdays)",
            },
            "start": {
                "type": "string",
                "description": "Window start ISO datetime (default: now)",
            },
            "end": {
                "type": "string",
                "description": "Window end ISO datetime (omit to use days)",
            },
            "days": {
                "type": "integer",
                "description": (
                    f"Days forward from start when end omitted (default {DEFAULT_LIST_DAYS})"
                ),
            },
            "include_description": {
                "type": "boolean",
                "description": "Include full event description/prompt text (default false)",
            },
            "description_preview_chars": {
                "type": "integer",
                "description": (
                    f"When include_description is false, preview length (default "
                    f"{DESCRIPTION_PREVIEW_MAX}, 0 to omit previews)"
                ),
            },
        },
        "required": ["entity_id"],
    },
)
async def list_calendar_events(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List calendar events in a time range."""
    entity_or_error = _get_calendar_entity(hass, arguments["entity_id"])
    if isinstance(entity_or_error, dict):
        return entity_or_error
    entity = entity_or_error

    try:
        start, end = _resolve_event_window(arguments)
    except ValueError as exc:
        return _error_text(str(exc))

    include_description = bool(arguments.get("include_description"))
    preview_chars = int(arguments.get("description_preview_chars", DESCRIPTION_PREVIEW_MAX))

    try:
        events = await entity.async_get_events(hass, start, end)
    except HomeAssistantError as exc:
        _LOGGER.error(
            "list_calendar_events failed for %s: %s",
            arguments["entity_id"],
            exc,
        )
        return _error_text(f"Failed to list events: {exc}")

    unique: dict[str, CalendarEvent] = {}
    for event in events:
        key = event.uid or f"{event.summary}:{event.start}:{event.end}"
        if key not in unique:
            unique[key] = event

    serialized = [
        _serialize_event(
            event,
            include_description=include_description,
            description_preview_chars=preview_chars,
        )
        for event in sorted(unique.values(), key=lambda e: e.start_datetime_local)
    ]

    return _text_result(
        {
            "entity_id": arguments["entity_id"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "count": len(serialized),
            "events": serialized,
        }
    )


@register_tool(
    name="delete_calendar_events",
    description=(
        "Delete calendar event series by uid or by summary filter. Requires a specific "
        "filter (uid, exact summary, or summary_contains with at least 3 characters). "
        "Use dry_run=true to preview matches without deleting."
    ),
    annotations=ANNOTATION_DESTRUCTIVE,
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity (e.g. calendar.birthdays)",
            },
            "uid": {
                "type": "string",
                "description": "Delete one series/instance by uid (from list_calendar_events)",
            },
            "summary": {
                "type": "string",
                "description": "Exact summary match (deletes each matching series once)",
            },
            "summary_contains": {
                "type": "string",
                "description": (
                    f"Case-insensitive substring match (min {DELETE_SUMMARY_CONTAINS_MIN} chars)"
                ),
            },
            "days": {
                "type": "integer",
                "description": (
                    f"Search window in days from now when matching by summary "
                    f"(default {DEFAULT_PURGE_LOOKAHEAD_DAYS})"
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "List matches without deleting (default false)",
            },
        },
        "required": ["entity_id"],
    },
)
async def delete_calendar_events(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete calendar events matching uid or summary filters."""
    entity_or_error = _get_calendar_entity(hass, arguments["entity_id"])
    if isinstance(entity_or_error, dict):
        return entity_or_error
    entity = entity_or_error

    if not _has_feature(entity, CalendarEntityFeature.DELETE_EVENT):
        return _error_text(f"Calendar {arguments['entity_id']} does not support event deletion")

    uid = (arguments.get("uid") or "").strip()
    summary = arguments.get("summary")
    summary_contains = (arguments.get("summary_contains") or "").strip()
    filters = sum(bool(x) for x in (uid, summary, summary_contains))
    if filters != 1:
        return _error_text("Provide exactly one of uid, summary, or summary_contains")
    if summary_contains and len(summary_contains) < DELETE_SUMMARY_CONTAINS_MIN:
        return _error_text(
            f"summary_contains must be at least {DELETE_SUMMARY_CONTAINS_MIN} characters"
        )

    dry_run = bool(arguments.get("dry_run"))
    matches: list[dict[str, Any]] = []

    if uid:
        matches.append({"uid": uid, "summary": None})
    else:
        days = int(arguments.get("days") or DEFAULT_PURGE_LOOKAHEAD_DAYS)
        if days < 1:
            return _error_text("days must be >= 1")
        start = dt_util.now()
        end = start + timedelta(days=days)
        try:
            events = await entity.async_get_events(hass, start, end)
        except HomeAssistantError as exc:
            _LOGGER.error(
                "delete_calendar_events list failed for %s: %s",
                arguments["entity_id"],
                exc,
            )
            return _error_text(f"Failed to search events: {exc}")

        seen_uids: set[str] = set()
        for event in events:
            event_summary = event.summary or ""
            if summary is not None and event_summary != summary:
                continue
            if summary_contains and summary_contains.lower() not in event_summary.lower():
                continue
            if not event.uid or event.uid in seen_uids:
                continue
            seen_uids.add(event.uid)
            matches.append({"uid": event.uid, "summary": event_summary})

    if not matches:
        return _text_result(
            {
                "entity_id": arguments["entity_id"],
                "deleted_count": 0,
                "dry_run": dry_run,
                "matches": [],
            }
        )

    deleted: list[dict[str, Any]] = []
    if not dry_run:
        for match in matches:
            try:
                await entity.async_delete_event(match["uid"])
            except HomeAssistantError as exc:
                _LOGGER.error(
                    "delete_calendar_events failed uid=%s: %s",
                    match["uid"],
                    exc,
                )
                return _error_text(f"Failed to delete uid {match['uid']}: {exc}")
            deleted.append(match)

    return _text_result(
        {
            "entity_id": arguments["entity_id"],
            "deleted_count": 0 if dry_run else len(deleted),
            "dry_run": dry_run,
            "matches": matches,
        }
    )

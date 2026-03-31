"""System, template, and history tools."""

import json
import logging
from datetime import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import register_tool

_LOGGER = logging.getLogger(__name__)


@register_tool(
    name="get_config",
    description="Get Home Assistant configuration info (version, location, units, timezone)",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def get_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get Home Assistant configuration."""
    config = hass.config
    result = {
        "location_name": config.location_name,
        "latitude": config.latitude,
        "longitude": config.longitude,
        "elevation": config.elevation,
        "unit_system": config.units.as_dict(),
        "time_zone": str(config.time_zone),
        "version": config.version,
        "currency": config.currency,
        "country": config.country,
        "language": config.language,
    }

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@register_tool(
    name="render_template",
    description="Evaluate a Jinja2 template in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "description": "The Jinja2 template string to render",
            },
            "variables": {
                "type": "object",
                "description": "Optional variables to pass to the template",
            },
        },
        "required": ["template"],
    },
)
async def render_template(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Render a Jinja2 template."""
    from homeassistant.helpers.template import Template

    template_str = arguments["template"]
    variables = arguments.get("variables", {})

    try:
        tpl = Template(template_str, hass)
        result = tpl.async_render(variables=variables, parse_result=False)
        return {"content": [{"type": "text", "text": str(result)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error rendering template: {str(e)}"}]}


@register_tool(
    name="get_history",
    description="Get state history of an entity over a time range",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID",
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format (e.g., 2024-01-01T00:00:00)",
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format (optional, defaults to now)",
            },
        },
        "required": ["entity_id", "start_time"],
    },
)
async def get_history(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get state history for an entity."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states

    entity_id = arguments["entity_id"]
    start_time = dt.fromisoformat(arguments["start_time"])
    end_time_str = arguments.get("end_time")
    end_time = dt.fromisoformat(end_time_str) if end_time_str else dt_util.utcnow()

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states,
            hass,
            start_time,
            end_time,
            [entity_id],
        )

        history = []
        for state in states.get(entity_id, []):
            history.append(
                {
                    "state": state.state,
                    "last_changed": state.last_changed.isoformat(),
                    "attributes": dict(state.attributes),
                }
            )
        return {"content": [{"type": "text", "text": json.dumps(history, indent=2)}]}
    except Exception as e:
        _LOGGER.error("Error getting history: %s", e)
        return {"content": [{"type": "text", "text": f"Error getting history: {str(e)}"}]}


_BLOCKED_EVENT_TYPES = frozenset(
    {
        "homeassistant_stop",
        "homeassistant_close",
        "homeassistant_final_write",
        "core_config_updated",
        "component_loaded",
        "service_registered",
        "service_removed",
        "state_changed",
        "call_service",
    }
)


@register_tool(
    name="fire_event",
    description=(
        "Fire a custom event on the Home Assistant event bus. "
        "Cannot fire system-level events (e.g., homeassistant_stop). "
        "Use with care as events can trigger automations"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "event_type": {
                "type": "string",
                "description": "The event type to fire (e.g., 'custom_event')",
            },
            "event_data": {
                "type": "object",
                "description": "Optional event data payload",
            },
        },
        "required": ["event_type"],
    },
)
async def fire_event(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Fire an event on the Home Assistant event bus."""
    event_type = arguments["event_type"]
    event_data = arguments.get("event_data", {})

    if event_type in _BLOCKED_EVENT_TYPES:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: firing system event '{event_type}' is not allowed",
                }
            ]
        }

    try:
        hass.bus.async_fire(event_type, event_data)
        return {"content": [{"type": "text", "text": f"Successfully fired event: {event_type}"}]}
    except Exception as e:
        _LOGGER.error("Error firing event: %s", e)
        return {"content": [{"type": "text", "text": f"Error firing event: {str(e)}"}]}


@register_tool(
    name="get_logbook",
    description=(
        "Fetch logbook entries for an entity or time range. "
        "Complements get_history (state changes) with event context "
        "(who triggered what, why)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Filter by entity ID (optional)",
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format (e.g., 2024-01-01T00:00:00)",
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format (optional, defaults to now)",
            },
        },
        "required": ["start_time"],
    },
)
async def get_logbook(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get logbook entries for an entity or time range."""
    from homeassistant.components.logbook.processor import (
        EVENT_LOGBOOK_ENTRY,
        PSEUDO_EVENT_STATE_CHANGED,
        EventProcessor,
    )
    from homeassistant.components.recorder import get_instance

    entity_id = arguments.get("entity_id")
    start_time = dt.fromisoformat(arguments["start_time"])
    end_time_str = arguments.get("end_time")
    end_time = dt.fromisoformat(end_time_str) if end_time_str else dt_util.utcnow()

    entity_ids = [entity_id] if entity_id else None

    try:
        processor = EventProcessor(
            hass,
            (PSEUDO_EVENT_STATE_CHANGED, EVENT_LOGBOOK_ENTRY),
            entity_ids=entity_ids,
        )

        events = await get_instance(hass).async_add_executor_job(
            processor.get_events, start_time, end_time
        )

        return {"content": [{"type": "text", "text": json.dumps(events, indent=2)}]}
    except Exception as e:
        _LOGGER.error("Error getting logbook: %s", e)
        return {"content": [{"type": "text", "text": f"Error getting logbook: {str(e)}"}]}

"""System, template, and history tools."""

import logging
from datetime import datetime as dt
from typing import Any

from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_COMPONENT_LOADED,
    EVENT_CORE_CONFIG_UPDATE,
    EVENT_HOMEASSISTANT_CLOSE,
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_LOGBOOK_ENTRY,
    EVENT_SERVICE_REGISTERED,
    EVENT_SERVICE_REMOVED,
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_READ_ONLY,
    caller_context,
    dumps,
    error_result,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)


def _template_rendering_enabled(hass: HomeAssistant) -> bool:
    """Return True when the template rendering option is on."""
    return hass.data.get(DOMAIN, {}).get("template_rendering", False)


def _template_disabled() -> dict[str, Any]:
    """Build the response telling the caller how to enable template rendering."""
    return error_result(
        "Template rendering is disabled. Enable it in the MCP Server integration "
        "settings: Settings → Devices & Services → MCP Server → Configure → "
        "Enable template rendering."
    )


@register_tool(
    name="get_config",
    description="Get Home Assistant configuration info (version, location, units, timezone)",
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
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
        "version": HA_VERSION,
        "currency": config.currency,
        "country": config.country,
        "language": config.language,
    }

    return {"content": [{"type": "text", "text": dumps(result)}]}


@register_tool(
    name="render_template",
    description=(
        "Evaluate a Jinja2 template in Home Assistant. Disabled by default: it must be "
        'enabled with "Enable template rendering" in the integration options, because a '
        "template can read every state, attribute, device and area on the instance"
    ),
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
    annotations=ANNOTATION_READ_ONLY,
)
async def render_template(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Render a Jinja2 template."""
    if not _template_rendering_enabled(hass):
        return _template_disabled()

    from homeassistant.helpers.template import Template

    template_str = arguments["template"]
    variables = arguments.get("variables", {})

    try:
        tpl = Template(template_str, hass)
        result = tpl.async_render(variables=variables, parse_result=False)
        return {"content": [{"type": "text", "text": str(result)}]}
    except Exception as e:
        return error_result(f"Error rendering template: {str(e)}")


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
    annotations=ANNOTATION_READ_ONLY,
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
        return {"content": [{"type": "text", "text": dumps(history)}]}
    except Exception as e:
        _LOGGER.error("Error getting history: %s", e)
        return error_result(f"Error getting history: {str(e)}")


# Events Home Assistant fires itself that core or a built-in integration acts on as
# fact. A forged one deletes entities or config entries, strips labels, renames
# history, stops the recorder or writes the logbook. The bus looks listeners up by
# the exact event type, so an exact match is what keeps a forged one from them.
# Each literal is the value of a constant pinned in tests/test_ha_api_contracts.py.
CORE_EVENT_TYPES = frozenset(
    {
        EVENT_HOMEASSISTANT_START,
        EVENT_HOMEASSISTANT_STARTED,
        EVENT_HOMEASSISTANT_STOP,
        EVENT_HOMEASSISTANT_FINAL_WRITE,
        EVENT_HOMEASSISTANT_CLOSE,
        EVENT_CORE_CONFIG_UPDATE,
        EVENT_COMPONENT_LOADED,
        EVENT_CALL_SERVICE,
        EVENT_SERVICE_REGISTERED,
        EVENT_SERVICE_REMOVED,
        EVENT_STATE_CHANGED,
        EVENT_STATE_REPORTED,
        EVENT_LOGBOOK_ENTRY,
        EVENT_USER_REMOVED,
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        dr.EVENT_DEVICE_REGISTRY_UPDATED,
        ar.EVENT_AREA_REGISTRY_UPDATED,
        fr.EVENT_FLOOR_REGISTRY_UPDATED,
        lr.EVENT_LABEL_REGISTRY_UPDATED,
        cr.EVENT_CATEGORY_REGISTRY_UPDATED,
        # homeassistant.const.EVENT_LABS_UPDATED, which 2025.6 does not have.
        "labs_updated",
        # automation.EVENT_AUTOMATION_TRIGGERED and script.const.EVENT_SCRIPT_STARTED.
        "automation_triggered",
        "script_started",
    }
)


@register_tool(
    name="fire_event",
    description=(
        "Fire a custom event on the Home Assistant event bus. "
        "Refuses events Home Assistant fires itself (e.g., homeassistant_stop, "
        "state_changed, *_registry_updated). Use with care as events can trigger automations"
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
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def fire_event(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Fire an event on the Home Assistant event bus."""
    event_type = arguments["event_type"]
    event_data = arguments.get("event_data", {})

    if event_type in CORE_EVENT_TYPES:
        return error_result(
            f"Error: firing '{event_type}' is not allowed: it is an event Home Assistant "
            "fires itself, and its own listeners act on it as fact"
        )

    # The integration's own events are its audit trail, so this tool does not fire
    # them. This closes the direct route only: an automation with an event action
    # can still fire anything, so the audit event is evidence, not proof.
    if event_type.startswith(f"{DOMAIN}_"):
        return error_result(f"Error: firing this integration's event '{event_type}' is not allowed")

    try:
        hass.bus.async_fire(event_type, event_data, context=caller_context())
        return {"content": [{"type": "text", "text": f"Successfully fired event: {event_type}"}]}
    except Exception as e:
        _LOGGER.error("Error firing event: %s", e)
        return error_result(f"Error firing event: {str(e)}")


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
    annotations=ANNOTATION_READ_ONLY,
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

        return {"content": [{"type": "text", "text": dumps(events)}]}
    except Exception as e:
        _LOGGER.error("Error getting logbook: %s", e)
        return error_result(f"Error getting logbook: {str(e)}")

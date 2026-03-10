"""MCP tool definitions and handlers for Home Assistant."""

import json
import logging
from datetime import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)

# Tool registry: name -> {"schema": {...}, "handler": callable}
TOOLS: dict[str, dict[str, Any]] = {}


def register_tool(name: str, description: str, input_schema: dict[str, Any]):
    """Decorator to register a tool with its schema and handler."""

    def decorator(func):
        TOOLS[name] = {
            "schema": {
                "name": name,
                "description": description,
                "inputSchema": input_schema,
            },
            "handler": func,
        }
        return func

    return decorator


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return all tool schemas."""
    return [tool["schema"] for tool in TOOLS.values()]


async def call_tool(hass: HomeAssistant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool by name."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    return await tool["handler"](hass, arguments)


# --- Tool Implementations ---


@register_tool(
    name="get_state",
    description="Get the state of a Home Assistant entity",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID (e.g., light.living_room)",
            }
        },
        "required": ["entity_id"],
    },
)
async def get_state(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get entity state."""
    entity_id = arguments["entity_id"]
    state = hass.states.get(entity_id)

    if state is None:
        return {"content": [{"type": "text", "text": f"Entity {entity_id} not found"}]}

    result = {
        "entity_id": state.entity_id,
        "state": state.state,
        "attributes": dict(state.attributes),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@register_tool(
    name="call_service",
    description="Call a Home Assistant service",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The service domain (e.g., light, switch)",
            },
            "service": {
                "type": "string",
                "description": "The service name (e.g., turn_on, turn_off)",
            },
            "entity_id": {
                "type": "string",
                "description": "The entity ID to target",
            },
            "data": {
                "type": "object",
                "description": "Additional service data",
            },
        },
        "required": ["domain", "service"],
    },
)
async def call_service(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a Home Assistant service."""
    domain = arguments["domain"]
    service = arguments["service"]
    entity_id = arguments.get("entity_id")
    data = arguments.get("data", {})

    service_data = {**data}
    if entity_id:
        service_data["entity_id"] = entity_id

    try:
        await hass.services.async_call(domain, service, service_data, blocking=True)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully called {domain}.{service}",
                }
            ]
        }
    except Exception as e:
        _LOGGER.error("Error calling service: %s", e)
        return {"content": [{"type": "text", "text": f"Error calling service: {str(e)}"}]}


@register_tool(
    name="list_entities",
    description="List all entities in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (optional)",
            }
        },
    },
)
async def list_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List entities."""
    domain_filter = arguments.get("domain")

    entities = []
    for state in hass.states.async_all():
        if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
            continue
        entities.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
            }
        )

    return {"content": [{"type": "text", "text": json.dumps(entities, indent=2)}]}


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
    name="list_areas",
    description="List all areas in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_areas(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all areas."""
    registry = ar.async_get(hass)
    areas = [
        {
            "id": area.id,
            "name": area.name,
            "floor_id": area.floor_id,
        }
        for area in registry.async_list_areas()
    ]

    return {"content": [{"type": "text", "text": json.dumps(areas, indent=2)}]}


@register_tool(
    name="list_devices",
    description="List devices in Home Assistant, optionally filtered by area",
    input_schema={
        "type": "object",
        "properties": {
            "area_id": {
                "type": "string",
                "description": "Filter by area ID (optional)",
            }
        },
    },
)
async def list_devices(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List devices."""
    registry = dr.async_get(hass)
    area_filter = arguments.get("area_id")

    devices = []
    for device in registry.devices.values():
        if area_filter and device.area_id != area_filter:
            continue
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "area_id": device.area_id,
                "name_by_user": device.name_by_user,
            }
        )

    return {"content": [{"type": "text", "text": json.dumps(devices, indent=2)}]}


@register_tool(
    name="list_services",
    description="List available services in Home Assistant, optionally filtered by domain",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (optional)",
            }
        },
    },
)
async def list_services(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List available services."""
    domain_filter = arguments.get("domain")
    services = hass.services.async_services()

    if domain_filter:
        services = {k: v for k, v in services.items() if k == domain_filter}

    # Convert service objects to serializable format
    result = {}
    for domain, domain_services in services.items():
        result[domain] = list(domain_services.keys())

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
    end_time = dt.fromisoformat(end_time_str) if end_time_str else dt.now()

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

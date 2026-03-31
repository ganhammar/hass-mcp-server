"""Entity, area, device, and service tools."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import register_tool

_LOGGER = logging.getLogger(__name__)


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

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    aliases = sorted(entry.aliases) if entry and entry.aliases else []

    result = {
        "entity_id": state.entity_id,
        "state": state.state,
        "attributes": dict(state.attributes),
        "aliases": aliases,
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
    registry = er.async_get(hass)

    entities = []
    for state in hass.states.async_all():
        if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
            continue
        entry = registry.async_get(state.entity_id)
        aliases = sorted(entry.aliases) if entry and entry.aliases else []
        entities.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                "aliases": aliases,
            }
        )

    return {"content": [{"type": "text", "text": json.dumps(entities, indent=2)}]}


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
    name="search_entities",
    description=(
        "Search entities by friendly name, attribute values, or device class. "
        "More useful than list_entities when you don't know the exact entity_id"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query to match against entity IDs, " "friendly names, and aliases"
                ),
            },
            "device_class": {
                "type": "string",
                "description": "Filter by device class (e.g., temperature, motion, door)",
            },
            "domain": {
                "type": "string",
                "description": "Filter by domain (e.g., sensor, binary_sensor, light)",
            },
            "area_id": {
                "type": "string",
                "description": "Filter by area ID",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default 100)",
            },
        },
    },
)
async def search_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Search entities by various criteria."""
    query = arguments.get("query", "").lower()
    device_class_filter = arguments.get("device_class")
    domain_filter = arguments.get("domain")
    area_filter = arguments.get("area_id")
    limit = arguments.get("limit", 100)

    if not any([query, device_class_filter, domain_filter, area_filter]):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Error: at least one search parameter "
                        "(query, device_class, domain, area_id) must be provided"
                    ),
                }
            ]
        }

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entities = []
    for state in hass.states.async_all():
        if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
            continue

        if device_class_filter:
            if state.attributes.get("device_class") != device_class_filter:
                continue

        entry = entity_registry.async_get(state.entity_id)
        aliases = sorted(entry.aliases) if entry and entry.aliases else []

        if area_filter:
            entity_area = entry.area_id if entry else None
            if not entity_area and entry and entry.device_id:
                device = device_registry.async_get(entry.device_id)
                entity_area = device.area_id if device else None
            if entity_area != area_filter:
                continue

        if query:
            friendly_name = state.attributes.get("friendly_name", "").lower()
            searchable = [
                state.entity_id.lower(),
                friendly_name,
                *(a.lower() for a in aliases),
            ]
            if not any(query in s for s in searchable):
                continue

        entities.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                "device_class": state.attributes.get("device_class"),
                "area_id": (entry.area_id if entry else None),
                "aliases": aliases,
            }
        )

        if len(entities) >= limit:
            break

    return {"content": [{"type": "text", "text": json.dumps(entities, indent=2)}]}

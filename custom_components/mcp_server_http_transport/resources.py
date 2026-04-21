"""MCP resource definitions and handlers for Home Assistant."""

import json
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from .json_utils import _HAJSONEncoder

RESOURCES = [
    {
        "uri": "hass://config",
        "name": "Home Assistant Configuration",
        "description": "Current HA configuration (version, location, units, timezone)",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://areas",
        "name": "Home Assistant Areas",
        "description": "List of all configured areas",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://devices",
        "name": "Home Assistant Devices",
        "description": "List of all registered devices",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://services",
        "name": "Home Assistant Services",
        "description": "List of all available services by domain",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://floors",
        "name": "Home Assistant Floors",
        "description": "List of all configured floors",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://entities",
        "name": "Home Assistant Entities",
        "description": "All entities organized by domain (entity_id, state, friendly_name)",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://labels",
        "name": "Home Assistant Labels",
        "description": "All labels used for cross-domain grouping of entities, devices, and areas",
        "mimeType": "application/json",
    },
    {
        "uri": "hass://integrations",
        "name": "Home Assistant Integrations",
        "description": "Installed integrations with their status",
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "hass://entity/{entity_id}",
        "name": "Entity State",
        "description": "Current state and attributes of a specific entity",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "hass://dashboard/{url_path}",
        "name": "Dashboard Configuration",
        "description": "Full configuration (views and cards) of a specific dashboard",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "hass://entities/domain/{domain}",
        "name": "Entities by Domain",
        "description": "All entities filtered by a specific domain",
        "mimeType": "application/json",
    },
]


def get_resources() -> dict[str, Any]:
    """Return all resource and resource template definitions."""
    return {
        "resources": RESOURCES,
        "resourceTemplates": RESOURCE_TEMPLATES,
    }


async def read_resource(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read a resource by URI."""
    if uri == "hass://config":
        return _read_config(hass, uri)

    if uri == "hass://areas":
        return _read_areas(hass, uri)

    if uri == "hass://devices":
        return _read_devices(hass, uri)

    if uri == "hass://services":
        return _read_services(hass, uri)

    if uri == "hass://floors":
        return _read_floors(hass, uri)

    if uri == "hass://entities":
        return _read_entities(hass, uri)

    if uri == "hass://labels":
        return _read_labels(hass, uri)

    if uri == "hass://integrations":
        return _read_integrations(hass, uri)

    if uri.startswith("hass://entities/domain/"):
        domain = uri[len("hass://entities/domain/") :]
        return _read_entities_domain(hass, uri, domain)

    if uri.startswith("hass://entity/"):
        entity_id = uri[len("hass://entity/") :]
        return _read_entity(hass, uri, entity_id)

    if uri.startswith("hass://dashboard/"):
        url_path = uri[len("hass://dashboard/") :]
        return await _read_dashboard(hass, uri, url_path)

    raise ValueError(f"Unknown resource: {uri}")


def _read_config(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read HA configuration as a resource."""
    config = hass.config
    data = {
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
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(data, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_areas(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all areas as a resource."""
    registry = ar.async_get(hass)
    areas = [
        {
            "id": area.id,
            "name": area.name,
            "floor_id": area.floor_id,
        }
        for area in registry.async_list_areas()
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(areas, indent=2, cls=_HAJSONEncoder),
        }
    ]


async def _read_dashboard(hass: HomeAssistant, uri: str, url_path: str) -> list[dict[str, Any]]:
    """Read a dashboard configuration as a resource."""
    from .dashboard_manager import get_dashboard_config

    config = await get_dashboard_config(hass, url_path)
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(config, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_devices(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all devices as a resource."""
    registry = dr.async_get(hass)
    devices = [
        {
            "id": device.id,
            "name": device.name,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "area_id": device.area_id,
            "name_by_user": device.name_by_user,
        }
        for device in registry.devices.values()
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(devices, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_services(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all services as a resource."""
    services = hass.services.async_services()
    result = {domain: list(svcs.keys()) for domain, svcs in services.items()}
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(result, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_floors(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all floors as a resource."""
    registry = fr.async_get(hass)
    floors = [
        {
            "floor_id": floor.floor_id,
            "name": floor.name,
            "icon": floor.icon,
            "level": floor.level,
            "aliases": sorted(floor.aliases) if floor.aliases else [],
        }
        for floor in registry.async_list_floors()
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(floors, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_entity(hass: HomeAssistant, uri: str, entity_id: str) -> list[dict[str, Any]]:
    """Read a specific entity state as a resource."""
    state = hass.states.get(entity_id)

    if state is None:
        raise ValueError(f"Entity {entity_id} not found")

    data = {
        "entity_id": state.entity_id,
        "state": state.state,
        "attributes": dict(state.attributes),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(data, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_entities(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all entities organized by domain."""
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for state in hass.states.async_all():
        domain = state.entity_id.split(".")[0]
        by_domain.setdefault(domain, []).append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
            }
        )
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(by_domain, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_entities_domain(hass: HomeAssistant, uri: str, domain: str) -> list[dict[str, Any]]:
    """Read entities filtered by domain."""
    entities = []
    for state in hass.states.async_all():
        if not state.entity_id.startswith(f"{domain}."):
            continue
        entities.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
            }
        )
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(entities, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_labels(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all labels as a resource."""
    registry = lr.async_get(hass)
    labels = [
        {
            "label_id": label.label_id,
            "name": label.name,
            "color": label.color,
            "icon": label.icon,
            "description": label.description,
        }
        for label in registry.async_list_labels()
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(labels, indent=2, cls=_HAJSONEncoder),
        }
    ]


def _read_integrations(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read installed integrations as a resource."""
    entries = hass.config_entries.async_entries()
    integrations = [
        {
            "domain": entry.domain,
            "title": entry.title,
            "state": str(entry.state),
            "entry_id": entry.entry_id,
        }
        for entry in entries
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(integrations, indent=2, cls=_HAJSONEncoder),
        }
    ]

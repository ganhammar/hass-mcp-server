"""MCP resource definitions and handlers for Home Assistant."""

from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from .access import READ, RESOURCE_AREAS, current_grant, not_granted
from .json_utils import dumps
from .tools.labels import label_json
from .tools.registries import area_json, device_entries, floor_json

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


class ResourceNotAvailable(Exception):
    """The caller's access does not include the resource; the transport answers -32602."""


class ResourceNotFound(Exception):
    """No resource exists at the URI; the transport answers with the revision's code for it."""


def resource_key(uri: Any) -> str | None:
    """Return the URI or URI template in RESOURCE_AREAS that a requested URI reads, or None.

    A URI matches a template the way read_resource dispatches it, by what precedes the
    template's first variable.
    """
    if not isinstance(uri, str):
        return None
    if any(resource["uri"] == uri for resource in RESOURCES):
        return uri
    for template in RESOURCE_TEMPLATES:
        if uri.startswith(template["uriTemplate"].split("{", 1)[0]):
            return template["uriTemplate"]
    return None


def _withheld(key: str | None) -> str | None:
    """Return why the request being served may not read a resource, or None when it may."""
    grant = current_grant()
    if grant is None:
        return None
    area = RESOURCE_AREAS.get(key) if key is not None else None
    if area is None:
        return f"The access granted to this application does not include resource {key}"
    if not grant.allows(area, READ):
        return not_granted(f"resource {key}", area, READ)
    return None


def get_resources() -> dict[str, Any]:
    """Return the resources and resource templates the client making the request can read."""
    return {
        "resources": [r for r in RESOURCES if _withheld(r["uri"]) is None],
        **get_resource_templates(),
    }


def get_resource_templates() -> dict[str, Any]:
    """Return the resource templates the client making the request can read."""
    return {
        "resourceTemplates": [t for t in RESOURCE_TEMPLATES if _withheld(t["uriTemplate"]) is None]
    }


async def read_resource(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read a resource by URI.

    A resource the caller's access does not include raises ResourceNotAvailable
    before anything is read, and one that does not exist raises ResourceNotFound.
    """
    key = resource_key(uri)
    if key is not None and (reason := _withheld(key)):
        raise ResourceNotAvailable(reason)
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

    raise ResourceNotFound(f"Unknown resource: {uri}")


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
            "text": dumps(data),
        }
    ]


def _read_areas(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all areas as a resource."""
    registry = ar.async_get(hass)
    areas = [area_json(area) for area in registry.async_list_areas()]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": dumps(areas),
        }
    ]


async def _read_dashboard(hass: HomeAssistant, uri: str, url_path: str) -> list[dict[str, Any]]:
    """Read a dashboard configuration as a resource."""
    from .dashboard_manager import DashboardNotFound, get_dashboard_config

    try:
        config = await get_dashboard_config(hass, url_path)
    except DashboardNotFound as err:
        raise ResourceNotFound(str(err)) from err
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": dumps(config),
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
            "labels": sorted(device.labels),
            "disabled_by": device.disabled_by,
        }
        for device in device_entries(registry)
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": dumps(devices),
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
            "text": dumps(result),
        }
    ]


def _read_floors(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all floors as a resource."""
    registry = fr.async_get(hass)
    floors = [floor_json(floor) for floor in registry.async_list_floors()]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": dumps(floors),
        }
    ]


def _read_entity(hass: HomeAssistant, uri: str, entity_id: str) -> list[dict[str, Any]]:
    """Read a specific entity state as a resource."""
    state = hass.states.get(entity_id)

    if state is None:
        raise ResourceNotFound(f"Entity {entity_id} not found")

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
            "text": dumps(data),
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
            "text": dumps(by_domain),
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
            "text": dumps(entities),
        }
    ]


def _read_labels(hass: HomeAssistant, uri: str) -> list[dict[str, Any]]:
    """Read all labels as a resource."""
    registry = lr.async_get(hass)
    labels = [label_json(label) for label in registry.async_list_labels()]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": dumps(labels),
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
            "disabled_by": entry.disabled_by,
            "supports_options": entry.supports_options,
            "supports_reconfigure": entry.supports_reconfigure,
        }
        for entry in entries
    ]
    return [
        {
            "uri": uri,
            "mimeType": "application/json",
            "text": dumps(integrations),
        }
    ]

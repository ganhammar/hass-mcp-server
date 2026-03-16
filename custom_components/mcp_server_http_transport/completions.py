"""MCP completion handler for Home Assistant."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar

MAX_COMPLETIONS = 100


async def complete(
    hass: HomeAssistant, ref: dict[str, Any], argument: dict[str, Any]
) -> dict[str, Any]:
    """Handle completion/complete requests."""
    arg_name = argument.get("name", "")
    arg_value = argument.get("value", "")

    if arg_name == "entity_id":
        return _complete_entity_id(hass, arg_value)

    if arg_name == "domain":
        return _complete_domain(hass, arg_value)

    if arg_name == "service":
        return _complete_service(hass, arg_value)

    if arg_name == "area_id":
        return _complete_area_id(hass, arg_value)

    if arg_name == "url_path":
        return _complete_url_path(hass, arg_value)

    return {"values": [], "hasMore": False}


def _complete_entity_id(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete entity IDs."""
    all_entities = [s.entity_id for s in hass.states.async_all()]
    matches = sorted(e for e in all_entities if e.startswith(prefix))
    return {
        "values": matches[:MAX_COMPLETIONS],
        "hasMore": len(matches) > MAX_COMPLETIONS,
    }


def _complete_domain(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete domain names."""
    domains = sorted(set(s.entity_id.split(".")[0] for s in hass.states.async_all()))
    matches = [d for d in domains if d.startswith(prefix)]
    return {"values": matches, "hasMore": False}


def _complete_service(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete service names."""
    services = hass.services.async_services()
    all_services = set()
    for svc_dict in services.values():
        for svc_name in svc_dict:
            all_services.add(svc_name)
    matches = sorted(s for s in all_services if s.startswith(prefix))
    return {
        "values": matches[:MAX_COMPLETIONS],
        "hasMore": len(matches) > MAX_COMPLETIONS,
    }


def _complete_area_id(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete area IDs."""
    registry = ar.async_get(hass)
    areas = [area.id for area in registry.async_list_areas()]
    matches = sorted(a for a in areas if a.startswith(prefix))
    return {"values": matches, "hasMore": False}


def _complete_url_path(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete dashboard URL paths."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    try:
        dashboards = hass.data[LOVELACE_DATA].dashboards
    except (KeyError, AttributeError):
        return {"values": [], "hasMore": False}

    paths = []
    for key in dashboards:
        path = "default" if key is None else key
        paths.append(path)

    matches = sorted(p for p in paths if p.startswith(prefix))
    return {"values": matches, "hasMore": False}

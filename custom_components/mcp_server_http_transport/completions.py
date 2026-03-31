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

    if arg_name == "entity_ids":
        return _complete_entity_id(hass, arg_value)

    if arg_name == "domain":
        return _complete_domain(hass, arg_value)

    if arg_name == "service":
        return _complete_service(hass, arg_value)

    if arg_name == "area_id":
        return _complete_area_id(hass, arg_value)

    if arg_name == "url_path":
        return _complete_url_path(hass, arg_value)

    if arg_name == "trigger_type":
        return _complete_trigger_type(arg_value)

    if arg_name == "period":
        return _complete_period(arg_value)

    if arg_name == "config_type":
        return _complete_config_type(arg_value)

    ref_name = ref.get("name", "")

    if arg_name == "automation_id" and ref_name in (
        "get_automation_config",
        "create_automation",
        "update_automation",
        "delete_automation",
        "automation_review",
        "automation_debugger",
    ):
        return await _complete_automation_id(hass, arg_value)

    if arg_name == "scene_id" and ref_name in (
        "get_scene_config",
        "create_scene",
        "update_scene",
        "delete_scene",
    ):
        return await _complete_scene_id(hass, arg_value)

    if arg_name == "key" and ref_name in (
        "get_script_config",
        "create_script",
        "update_script",
        "delete_script",
    ):
        return await _complete_script_key(hass, arg_value)

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


async def _complete_automation_id(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete automation IDs."""
    from .config_manager import read_list_entries

    entries = await read_list_entries(hass, "automations.yaml")
    ids = sorted(str(e["id"]) for e in entries if "id" in e)
    matches = [i for i in ids if i.startswith(prefix)]
    return {
        "values": matches[:MAX_COMPLETIONS],
        "hasMore": len(matches) > MAX_COMPLETIONS,
    }


async def _complete_scene_id(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete scene IDs."""
    from .config_manager import read_list_entries

    entries = await read_list_entries(hass, "scenes.yaml")
    ids = sorted(str(e["id"]) for e in entries if "id" in e)
    matches = [i for i in ids if i.startswith(prefix)]
    return {
        "values": matches[:MAX_COMPLETIONS],
        "hasMore": len(matches) > MAX_COMPLETIONS,
    }


async def _complete_script_key(hass: HomeAssistant, prefix: str) -> dict[str, Any]:
    """Complete script keys."""
    from .config_manager import read_dict_entries

    entries = await read_dict_entries(hass, "scripts.yaml")
    keys = sorted(entries.keys())
    matches = [k for k in keys if k.startswith(prefix)]
    return {
        "values": matches[:MAX_COMPLETIONS],
        "hasMore": len(matches) > MAX_COMPLETIONS,
    }


_TRIGGER_TYPES = [
    "device",
    "event",
    "homeassistant",
    "mqtt",
    "numeric_state",
    "state",
    "sun",
    "tag",
    "template",
    "time",
    "time_pattern",
    "webhook",
    "zone",
]


def _complete_trigger_type(prefix: str) -> dict[str, Any]:
    """Complete trigger types."""
    matches = [t for t in _TRIGGER_TYPES if t.startswith(prefix)]
    return {"values": matches, "hasMore": False}


_PERIODS = ["5minute", "day", "hour", "month", "week"]


def _complete_period(prefix: str) -> dict[str, Any]:
    """Complete statistic periods."""
    matches = [p for p in _PERIODS if p.startswith(prefix)]
    return {"values": matches, "hasMore": False}


_CONFIG_TYPES = ["automation", "scene", "script"]


def _complete_config_type(prefix: str) -> dict[str, Any]:
    """Complete configuration types."""
    matches = [c for c in _CONFIG_TYPES if c.startswith(prefix)]
    return {"values": matches, "hasMore": False}

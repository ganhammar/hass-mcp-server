"""KNX bus tools — read Home Assistant's KNX group-monitor telegram history."""

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

from . import register_tool

_LOGGER = logging.getLogger(__name__)

# KNX_MODULE_KEY is where the KNX integration stores its runtime module
# (same access path used by HA's own `knx/group_monitor_info` websocket command).
# Imported defensively so the MCP server still loads when KNX isn't installed.
try:
    from homeassistant.components.knx.const import KNX_MODULE_KEY
except Exception:  # pragma: no cover - KNX integration not available
    KNX_MODULE_KEY = None


def _get_knx_module(hass: HomeAssistant):
    """Return the KNX runtime module from hass.data, or None if KNX isn't set up."""
    if KNX_MODULE_KEY is None:
        return None
    return hass.data.get(KNX_MODULE_KEY)


def _destination(telegram: dict[str, Any]) -> str:
    """Group address of a telegram, tolerating key naming differences."""
    return str(telegram.get("destination") or telegram.get("destination_address") or "")


@register_tool(
    name="knx_recent_telegrams",
    description=(
        "Return Home Assistant's recent KNX bus telegrams (the group-monitor "
        "history buffer — typically the last several thousand telegrams, a few "
        "hours of bus traffic). Each telegram includes the destination group "
        "address, decoded value, telegram type, and the SOURCE device "
        "(individual address + device name). This is RETROSPECTIVE — it reads "
        "the stored buffer, unlike a live subscription — and is ideal for "
        "diagnosing which KNX device wrote a given group address (e.g. a value "
        "that flaps at dusk). Optional regex filters on group address / name "
        "and a result limit keep the output small."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filter_ga": {
                "type": "string",
                "description": (
                    "Regex matched against the destination group address "
                    "(e.g. '^0/0/249$' for one GA, or '^1/2/' for a sub-tree)."
                ),
            },
            "filter_name": {
                "type": "string",
                "description": "Case-insensitive regex matched against the destination name.",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Max number of most-recent matching telegrams to return (default 200)."
                ),
            },
        },
    },
)
async def knx_recent_telegrams(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return filtered recent KNX telegrams from the group-monitor buffer."""
    knx = _get_knx_module(hass)
    if knx is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "KNX integration is not set up on this Home Assistant instance.",
                }
            ]
        }

    try:
        telegrams = [dict(t) for t in knx.telegrams.recent_telegrams]
    except AttributeError:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "KNX telegram history is unavailable on this Home Assistant version.",
                }
            ]
        }

    try:
        ga_re = re.compile(arguments["filter_ga"]) if arguments.get("filter_ga") else None
        name_re = (
            re.compile(arguments["filter_name"], re.IGNORECASE)
            if arguments.get("filter_name")
            else None
        )
    except re.error as err:
        return {"content": [{"type": "text", "text": f"Invalid regex: {err}"}]}

    matched = [
        t
        for t in telegrams
        if (ga_re is None or ga_re.search(_destination(t)))
        and (name_re is None or name_re.search(str(t.get("destination_name", ""))))
    ]

    limit = arguments.get("limit") or 200
    returned = matched[-limit:]
    timestamps = [t.get("timestamp") for t in telegrams if t.get("timestamp")]

    return {
        "buffer_size": len(telegrams),
        "buffer_span": {
            "oldest": min(timestamps) if timestamps else None,
            "newest": max(timestamps) if timestamps else None,
        },
        "matched": len(matched),
        "returned": len(returned),
        "telegrams": returned,
    }


def _not_setup() -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "KNX integration is not set up."}]}


def _supported_platforms_ui():
    """UI-creatable KNX platforms, or None if unavailable on this HA version."""
    try:
        from homeassistant.components.knx.const import SUPPORTED_PLATFORMS_UI

        return sorted(str(p) for p in SUPPORTED_PLATFORMS_UI)
    except Exception:  # pragma: no cover - older HA / KNX missing
        return None


@register_tool(
    name="knx_get_base_data",
    description=(
        "Return KNX connection and project info: bus connection status, the "
        "gateway's individual address, xknx version, the loaded ETS project "
        "metadata (name / last modified), and the platforms creatable via the "
        "KNX UI. Useful context before inspecting or creating KNX entities."
    ),
    input_schema={"type": "object", "properties": {}},
)
async def knx_get_base_data(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return KNX connection + project base data."""
    knx = _get_knx_module(hass)
    if knx is None:
        return _not_setup()
    connection: dict[str, Any] = {}
    xknx_version = None
    try:
        xknx = knx.xknx
        xknx_version = getattr(xknx, "version", None)
        connection = {
            "connected": bool(xknx.connection_manager.connected.is_set()),
            "current_address": str(getattr(xknx, "current_address", "") or ""),
        }
    except Exception as err:  # noqa: BLE001
        connection = {"error": str(err)[:120]}
    try:
        project_info = knx.project.info
    except Exception:  # noqa: BLE001
        project_info = None
    return {
        "connection": connection,
        "xknx_version": xknx_version,
        "project_info": project_info,
        "supported_platforms": _supported_platforms_ui(),
    }


@register_tool(
    name="knx_get_entities",
    description=(
        "List KNX group addresses and the entities bound to each — the "
        "KNX-specific binding view (which group address maps to which entity) "
        "that generic entity tools don't expose. Optional regex filter on the "
        "group address, plus a limit."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filter_ga": {
                "type": "string",
                "description": (
                    "Regex matched against the group address (e.g. '^0/0/' or '^1/2/15$')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max number of group addresses to return (default 200).",
            },
        },
    },
)
async def knx_get_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List KNX group-address -> entity bindings."""
    knx = _get_knx_module(hass)
    if knx is None:
        return _not_setup()
    try:
        mapping = dict(knx.group_address_entities)
    except AttributeError:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "KNX group-address/entity mapping unavailable on this HA version.",
                }
            ]
        }
    try:
        ga_re = re.compile(arguments["filter_ga"]) if arguments.get("filter_ga") else None
    except re.error as err:
        return {"content": [{"type": "text", "text": f"Invalid regex: {err}"}]}

    rows = []
    for ga, identifiers in mapping.items():
        ga = str(ga)
        if ga_re and not ga_re.search(ga):
            continue
        ents = list(identifiers) if isinstance(identifiers, (list, tuple, set)) else [identifiers]
        rows.append({"group_address": ga, "entities": [str(e) for e in ents]})

    limit = arguments.get("limit") or 200
    return {"count": len(rows), "entities_by_group": rows[:limit]}


# --- Write tools (experimental): mutate HA's KNX UI config via config_store ---


@register_tool(
    name="knx_create_entity",
    description=(
        "Create a KNX entity in Home Assistant's UI config (the KNX "
        "config_store) — experimental. 'platform' is the KNX platform (light, "
        "switch, climate, cover, binary_sensor, sensor, ...) and 'data' is the "
        "platform-specific config (group addresses, DPTs, name) matching HA's "
        "KNX UI schema. Returns the new entity_id and reloads the platform. "
        "Use knx_get_base_data for the supported platforms."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "description": "KNX platform (light, switch, climate, cover, ...).",
            },
            "data": {
                "type": "object",
                "description": "Platform-specific KNX entity config (HA KNX UI schema).",
            },
        },
        "required": ["platform", "data"],
    },
)
async def knx_create_entity(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a KNX UI entity via the config_store."""
    knx = _get_knx_module(hass)
    if knx is None:
        return _not_setup()
    platform = arguments.get("platform")
    data = arguments.get("data")
    if not platform or data is None:
        return {"content": [{"type": "text", "text": "Both 'platform' and 'data' are required."}]}
    try:
        entity_id = await knx.config_store.create_entity(platform, data)
    except Exception as err:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"create_entity failed: {err}"}]}
    return {"created": True, "entity_id": entity_id, "platform": platform}


@register_tool(
    name="knx_update_entity",
    description=(
        "Update an existing UI-managed KNX entity (config_store) — "
        "experimental. Requires 'entity_id', 'platform' and the full 'data' "
        "config (same schema as create). Reloads the platform."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "platform": {"type": "string"},
            "data": {"type": "object"},
        },
        "required": ["entity_id", "platform", "data"],
    },
)
async def knx_update_entity(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update a KNX UI entity via the config_store."""
    knx = _get_knx_module(hass)
    if knx is None:
        return _not_setup()
    entity_id = arguments.get("entity_id")
    platform = arguments.get("platform")
    data = arguments.get("data")
    if not entity_id or not platform or data is None:
        return {
            "content": [
                {"type": "text", "text": "'entity_id', 'platform' and 'data' are required."}
            ]
        }
    try:
        await knx.config_store.update_entity(platform, entity_id, data)
    except Exception as err:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"update_entity failed: {err}"}]}
    return {"updated": True, "entity_id": entity_id, "platform": platform}


@register_tool(
    name="knx_delete_entity",
    description=(
        "Delete a UI-managed KNX entity (config_store) by entity_id — "
        "experimental. Mutates the KNX UI config."
    ),
    input_schema={
        "type": "object",
        "properties": {"entity_id": {"type": "string"}},
        "required": ["entity_id"],
    },
)
async def knx_delete_entity(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a KNX UI entity via the config_store."""
    knx = _get_knx_module(hass)
    if knx is None:
        return _not_setup()
    entity_id = arguments.get("entity_id")
    if not entity_id:
        return {"content": [{"type": "text", "text": "'entity_id' is required."}]}
    try:
        await knx.config_store.delete_entity(entity_id)
    except Exception as err:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"delete_entity failed: {err}"}]}
    return {"deleted": True, "entity_id": entity_id}

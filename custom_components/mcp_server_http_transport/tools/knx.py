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

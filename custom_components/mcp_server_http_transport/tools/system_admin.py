"""System administration and diagnostic tools."""

import json
import logging
from collections import deque
from datetime import datetime
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_READ_ONLY,
    _HAJSONEncoder,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)


@register_tool(
    name="get_error_log",
    description=(
        "Fetch the Home Assistant error log. Returns the last N lines of the log file. "
        "If on-disk file logging is disabled, falls back to the recent WARNING/ERROR "
        "entries Home Assistant keeps in memory"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "lines": {
                "type": "integer",
                "description": "Number of lines to return from the end of the log (default 100)",
            }
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_error_log(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Fetch the Home Assistant error log, falling back to the in-memory buffer."""
    lines = arguments.get("lines", 100)
    log_path = hass.config.path("home-assistant.log")

    def _read_log():
        with open(log_path) as f:
            return "".join(deque(f, maxlen=lines))

    try:
        log_text = await hass.async_add_executor_job(_read_log)
        return {"content": [{"type": "text", "text": log_text}]}
    except FileNotFoundError:
        fallback = _read_system_log_buffer(hass, lines)
        if fallback is not None:
            return {"content": [{"type": "text", "text": fallback}]}
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Log file not found at {log_path}, and Home Assistant's in-memory "
                        "system log buffer is empty or unavailable. Home Assistant may not be "
                        "configured to write logs to disk; enable file logging via the "
                        "'logger:' integration in configuration.yaml."
                    ),
                }
            ]
        }
    except Exception as e:
        _LOGGER.error("Error reading error log: %s", e)
        return {"content": [{"type": "text", "text": f"Error reading error log: {str(e)}"}]}


def _read_system_log_buffer(hass: HomeAssistant, limit: int) -> str | None:
    """Render recent WARNING/ERROR entries from HA's in-memory system log, or None.

    The system_log component keeps a deduplicated buffer of recent warning and
    error records in memory regardless of whether on-disk file logging is
    enabled, so it gives useful output on installs where home-assistant.log is
    absent. Returns None when the component is unavailable or the buffer is empty.
    """
    handler = hass.data.get("system_log")
    to_list = getattr(getattr(handler, "records", None), "to_list", None)
    if not callable(to_list):
        return None
    # The caller invokes this inside `except FileNotFoundError`, where a sibling
    # `except Exception` can't catch a throw originating here. Guard the buffer
    # read and formatting so any failure degrades to the not-found message
    # rather than escaping as an internal error.
    try:
        entries = to_list()
        if not isinstance(entries, list) or not entries:
            return None
        shown = entries[: max(1, limit)]
        formatted = "\n".join(_format_system_log_entry(entry) for entry in shown)
    except Exception as e:
        _LOGGER.debug("Could not read system log buffer: %s", e)
        return None
    header = (
        f"home-assistant.log not found; showing the {len(shown)} most recent "
        "WARNING/ERROR entries from Home Assistant's in-memory system log buffer "
        "(on-disk file logging appears to be disabled):\n\n"
    )
    return header + formatted


def _format_system_log_entry(entry: dict[str, Any]) -> str:
    """Format one system_log entry dict into a readable log-style line."""
    timestamp = entry.get("timestamp")
    try:
        ts = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        ts = str(timestamp)

    source = entry.get("source")
    if isinstance(source, (list, tuple)) and len(source) == 2:
        source_str = f"{source[0]}:{source[1]}"
    elif source is not None:
        source_str = str(source)
    else:
        source_str = "?"

    messages = entry.get("message")
    if isinstance(messages, (list, tuple)):
        message = "; ".join(str(m) for m in messages)
    else:
        message = str(messages) if messages is not None else ""

    count = entry.get("count", 1)
    count_str = f" ({count}x)" if isinstance(count, int) and count > 1 else ""

    line = (
        f"{ts} {entry.get('level', '')} ({entry.get('name', '')}) "
        f"[{source_str}] {message}{count_str}"
    )
    exception = entry.get("exception")
    if exception:
        line += f"\n{exception}"
    return line


@register_tool(
    name="restart_ha",
    description=(
        "Restart Home Assistant. Requires explicit confirmation. "
        "This will temporarily interrupt all operations"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "Must be true to confirm the restart",
            }
        },
        "required": ["confirm"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def restart_ha(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Restart Home Assistant."""
    confirm = arguments.get("confirm", False)

    if confirm is not True:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Restart requires confirm=true. "
                        "This will restart Home Assistant and temporarily interrupt all operations."
                    ),
                }
            ]
        }

    try:
        await hass.services.async_call("homeassistant", "restart", blocking=False)
        return {"content": [{"type": "text", "text": "Home Assistant restart has been initiated"}]}
    except Exception as e:
        _LOGGER.error("Error restarting Home Assistant: %s", e)
        return {"content": [{"type": "text", "text": f"Error restarting Home Assistant: {str(e)}"}]}


@register_tool(
    name="get_system_status",
    description=(
        "Get a combined system status view: HA version, domain counts, entity totals, "
        "and entities in problem states (unavailable, unknown). "
        "Gives a quick orientation without fetching every entity"
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_system_status(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get system status overview."""
    all_states = hass.states.async_all()

    domain_counts: dict[str, int] = {}
    problem_entities = []
    for state in all_states:
        domain = state.entity_id.split(".")[0]
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if state.state in ("unavailable", "unknown"):
            problem_entities.append({"entity_id": state.entity_id, "state": state.state})

    integration_count = len(hass.config_entries.async_entries())

    result = {
        "version": HA_VERSION,
        "total_entities": len(all_states),
        "domain_counts": dict(sorted(domain_counts.items())),
        "problem_entities": problem_entities,
        "integration_count": integration_count,
    }

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, cls=_HAJSONEncoder)}]}


@register_tool(
    name="get_domain_stats",
    description=(
        "Get aggregate stats for a single domain: entity count, state breakdown, "
        "and example entities. Lighter alternative to list_entities when only the big picture "
        "is needed"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The domain to get stats for (e.g., light, sensor, switch)",
            }
        },
        "required": ["domain"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_domain_stats(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get aggregate stats for a domain."""
    domain = arguments["domain"]

    state_counts: dict[str, int] = {}
    examples = []
    total = 0

    for state in hass.states.async_all():
        if not state.entity_id.startswith(f"{domain}."):
            continue
        total += 1
        state_counts[state.state] = state_counts.get(state.state, 0) + 1
        if len(examples) < 5:
            examples.append(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                }
            )

    result = {
        "domain": domain,
        "total": total,
        "state_counts": dict(sorted(state_counts.items())),
        "examples": examples,
    }

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, cls=_HAJSONEncoder)}]}


@register_tool(
    name="check_config",
    description=(
        "Validate Home Assistant configuration without restarting. "
        "Useful after creating or updating automations, scripts, or scenes"
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def check_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate HA configuration."""
    from homeassistant.helpers.check_config import async_check_ha_config_file

    try:
        res = await async_check_ha_config_file(hass)
        errors = [str(err) for err in res.errors] if res.errors else []
        result = {
            "valid": len(errors) == 0,
            "errors": errors,
        }
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, cls=_HAJSONEncoder)}]
        }
    except Exception as e:
        _LOGGER.error("Error checking config: %s", e)
        return {"content": [{"type": "text", "text": f"Error checking config: {str(e)}"}]}


@register_tool(
    name="list_integrations",
    description="List installed integrations and their status",
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_integrations(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List installed integrations."""
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

    return {
        "content": [
            {"type": "text", "text": json.dumps(integrations, indent=2, cls=_HAJSONEncoder)}
        ]
    }

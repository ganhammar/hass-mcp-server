"""MCP tool definitions and handlers for Home Assistant."""

import json
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant


class _HAJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime/date objects in HA state attributes."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)

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


# Import submodules so tools auto-register via @register_tool
from . import config, dashboards, entities, statistics, system, system_admin  # noqa: F401, E402

"""MCP tool definitions and handlers for Home Assistant."""

from typing import Any

from homeassistant.core import HomeAssistant

from ..json_utils import _HAJSONEncoder  # noqa: F401

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
from . import (  # noqa: E402
    config,  # noqa: F401
    config_files,  # noqa: F401
    dashboards,  # noqa: F401
    entities,  # noqa: F401
    helpers,  # noqa: F401
    statistics,  # noqa: F401
    system,  # noqa: F401
    system_admin,  # noqa: F401
)

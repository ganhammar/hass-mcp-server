"""MCP tool definitions and handlers for Home Assistant."""

from typing import Any

from homeassistant.core import HomeAssistant

from ..json_utils import _HAJSONEncoder  # noqa: F401

# Tool registry: name -> {"schema": {...}, "handler": callable}
TOOLS: dict[str, dict[str, Any]] = {}

# Reusable MCP ToolAnnotations (see spec §ToolAnnotations).
ANNOTATION_READ_ONLY: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ANNOTATION_IDEMPOTENT: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ANNOTATION_DESTRUCTIVE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


def register_tool(
    name: str, 
    description: str, 
    input_schema: dict[str, Any],
    annotations: dict[str, Any] | None = None,
):
    """Decorator to register a tool with its schema and handler."""

    def decorator(func):
        schema = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        if annotations is not None:
            schema["annotations"] = annotations

        TOOLS[name] = {
            "schema": schema,
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
    images,  # noqa: F401
    knx,  # noqa: F401
    statistics,  # noqa: F401
    system,  # noqa: F401
    system_admin,  # noqa: F401
)

"""MCP tool definitions and handlers for Home Assistant."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..json_utils import dumps  # noqa: F401

_LOGGER = logging.getLogger(__name__)

# Tool registry: name -> {"schema": {...}, "handler": callable}
TOOLS: dict[str, dict[str, Any]] = {}


class InvalidToolRequest(Exception):
    """The call cannot be dispatched as made, and the caller is the one to fix it."""


class UnknownTool(InvalidToolRequest):
    """No tool is registered under the requested name."""


class InvalidToolArguments(InvalidToolRequest):
    """Arguments do not satisfy the tool's declared input schema."""


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
ANNOTATION_NON_IDEMPOTENT: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
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


def _missing_required(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Return the required properties the arguments do not supply.

    An explicit null counts as absent. A client drifting from the advertised
    schema sends one as readily as it omits the key, and either way the handler
    has no value to work with.
    """
    required = schema.get("inputSchema", {}).get("required", [])
    return [key for key in required if arguments.get(key) is None]


def _describe_missing(schema: dict[str, Any], missing: list[str]) -> str:
    """Name each missing property alongside its description from the schema.

    The description is what carries the reason a property exists — that confirm
    gates an irreversible delete, say — so a caller that never read the schema
    still gets told why the call was rejected.
    """
    properties = schema.get("inputSchema", {}).get("properties", {})
    parts = []
    for key in missing:
        description = properties.get(key, {}).get("description")
        parts.append(f"{key} ({description})" if description else key)
    return ", ".join(parts)


async def call_tool(hass: HomeAssistant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool by name.

    Arguments are checked against the tool's declared required properties before
    dispatch, so a client working from a stale copy of the tool list gets a named
    invalid-params error. A handler that raises anything else becomes an isError
    tool result, which keeps one failing call from turning the whole JSON-RPC
    response into a transport-level failure.

    Anything the caller got wrong — an unknown name, a non-object arguments, a
    missing required property — raises InvalidToolRequest rather than reaching a
    handler, so a malformed call can never run a tool's default behavior.
    """
    tool = TOOLS.get(name)
    if tool is None:
        raise UnknownTool(f"Unknown tool: {name}")

    if not isinstance(arguments, dict):
        got = type(arguments).__name__
        raise InvalidToolArguments(f"Arguments for tool '{name}' must be a JSON object, got {got}")

    missing = _missing_required(tool["schema"], arguments)
    if missing:
        noun = "properties" if len(missing) > 1 else "property"
        raise InvalidToolArguments(
            f"Missing required {noun} for tool '{name}': "
            f"{_describe_missing(tool['schema'], missing)}"
        )

    try:
        return await tool["handler"](hass, arguments)
    except Exception as err:  # noqa: BLE001 - surfaced to the client as isError
        _LOGGER.exception("Tool %s raised an unhandled exception", name)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Tool '{name}' failed: {type(err).__name__}: {err}",
                }
            ],
            "isError": True,
        }


# Import submodules so tools auto-register via @register_tool
from . import (  # noqa: E402
    appdaemon_files,  # noqa: F401
    calendar,  # noqa: F401
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
    traces,  # noqa: F401
)

"""Dashboard tools."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    _HAJSONEncoder,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)


@register_tool(
    name="list_dashboards",
    description="List all Lovelace dashboards with metadata (url_path, title, icon, mode)",
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_dashboards_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all dashboards."""
    from ..dashboard_manager import list_dashboards

    try:
        dashboards = await list_dashboards(hass)
        return {
            "content": [
                {"type": "text", "text": json.dumps(dashboards, indent=2, cls=_HAJSONEncoder)}
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing dashboards: {str(e)}"}]}


@register_tool(
    name="get_dashboard_config",
    description=(
        "Get the configuration (views and cards) of a Lovelace dashboard. "
        'Use url_path="default" for the main Overview dashboard. '
        "Returns the whole config by default, which can be very large on a busy dashboard. "
        "For a large dashboard, start with summary=true to get an outline of the views and "
        "cards with a JSON Pointer for each, then pass one of those pointers as 'path' to read "
        "just that card or view. The same pointers are what patch_dashboard_config edits"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "JSON Pointer to the part of the config to return, e.g. '/views/2' or "
                    "'/views/2/cards/5'. Omit to return the whole config."
                ),
            },
            "summary": {
                "type": "boolean",
                "description": (
                    "Return an outline instead of the full config (default: false): view "
                    "titles plus each card's type, label, and entities, with a JSON Pointer "
                    "for every entry but without the cards' full options. "
                    "Can be combined with path='/views/<n>' to outline a single view"
                ),
            },
        },
        "required": ["url_path"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Get a dashboard configuration, optionally scoped to a pointer or summarized."""
    from ..dashboard_manager import (
        get_dashboard_config,
        summarize_dashboard_config,
        summarize_view,
    )
    from ..json_patch import parse_pointer, resolve_pointer

    pointer = arguments.get("path") or ""

    try:
        config = await get_dashboard_config(hass, arguments["url_path"])

        if arguments.get("summary", False):
            tokens = parse_pointer(pointer)
            if not tokens:
                result: Any = summarize_dashboard_config(config)
            elif len(tokens) == 2 and tokens[0] == "views" and tokens[1].isdecimal():
                view = resolve_pointer(config, pointer)
                result = summarize_view(view, tokens, int(tokens[1]))
            else:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"summary=true supports the whole config (omit path) or a "
                                f"single view (path='/views/0'), not '{pointer}'. "
                                f"Call again without summary to read '{pointer}' in full."
                            ),
                        }
                    ]
                }
        else:
            result = resolve_pointer(config, pointer) if pointer else config

        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, cls=_HAJSONEncoder)}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting dashboard config: {str(e)}"}]}


@register_tool(
    name="save_dashboard_config",
    description=(
        "Save (replace) the full configuration of a Lovelace dashboard. "
        'Use url_path="default" for the main Overview dashboard. '
        "This requires sending every view and card, so for anything short of a rewrite "
        "use patch_dashboard_config instead"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            },
            "config": {
                "type": "object",
                "description": "Full dashboard config with views and cards",
            },
        },
        "required": ["url_path", "config"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def save_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Save dashboard configuration."""
    from ..dashboard_manager import save_dashboard_config

    try:
        await save_dashboard_config(hass, arguments["url_path"], arguments["config"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully saved config for dashboard '{arguments['url_path']}'",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error saving dashboard config: {str(e)}"}]}


def _count(value: Any) -> int:
    """Length of a list-valued config key, 0 for anything else."""
    return len(value) if isinstance(value, list) else 0


def _view_overview(config: dict[str, Any]) -> list[str]:
    """One compact line per view: index, title, and how many cards it now holds."""
    lines: list[str] = []
    for index, view in enumerate(config.get("views") or []):
        if not isinstance(view, dict):
            lines.append(f"  [{index}] (not an object)")
            continue
        sections = [s for s in (view.get("sections") or []) if isinstance(s, dict)]
        cards = _count(view.get("cards")) + sum(_count(s.get("cards")) for s in sections)
        title = view.get("title") or view.get("path") or ""
        parts = [f"{cards} card(s)"]
        if sections:
            parts.append(f"{len(sections)} section(s)")
        lines.append(f"  [{index}] {title}: " + ", ".join(parts))
    return lines


@register_tool(
    name="patch_dashboard_config",
    description=(
        "Edit parts of a Lovelace dashboard without resending the whole config. "
        "Takes RFC 6902 JSON Patch operations addressing locations by JSON Pointer, "
        "e.g. move one card between views or change a single card's entity. "
        'Use url_path="default" for the main Overview dashboard. '
        "Discover pointers with get_dashboard_config(summary=true). "
        "Operations apply in order and all-or-nothing: if one fails the dashboard is "
        "left untouched. Because each operation sees the result of the previous one, "
        "removing several cards from the same view is safest done highest index first. "
        "Guard against a stale read by leading with a 'test' operation"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            },
            "operations": {
                "type": "array",
                "description": (
                    "JSON Patch operations to apply in order. Example: "
                    '[{"op": "test", "path": "/views/1/cards/3/entity", '
                    '"value": "fan.air_purifier"}, '
                    '{"op": "move", "from": "/views/1/cards/3", "path": "/views/2/cards/-"}]'
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["add", "remove", "replace", "move", "copy", "test"],
                            "description": (
                                "add: insert a value (into an array at the given index, "
                                "or set an object key). remove: delete the value. "
                                "replace: overwrite an existing value. "
                                "move/copy: relocate or duplicate the value at 'from'. "
                                "test: fail the whole patch unless the value matches"
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "JSON Pointer to the target, e.g. '/views/2/cards/5' or "
                                "'/views/0/sections/1/cards/0/entity'. For 'add' and 'move', "
                                "end an array path with '/-' to append, or with an index to "
                                "insert before that position"
                            ),
                        },
                        "value": {
                            "description": (
                                "The value for add, replace, and test. "
                                "For a card this is the full card object"
                            ),
                        },
                        "from": {
                            "type": "string",
                            "description": "Source JSON Pointer, required for move and copy",
                        },
                    },
                    "required": ["op", "path"],
                },
            },
        },
        "required": ["url_path", "operations"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def patch_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Apply JSON Patch operations to a dashboard configuration."""
    from ..dashboard_manager import patch_dashboard_config

    url_path = arguments["url_path"]
    operations = arguments.get("operations")

    try:
        config = await patch_dashboard_config(hass, url_path, operations)
        lines = [f"Applied {len(operations)} operation(s) to dashboard '{url_path}'"]
        lines.extend(_view_overview(config))
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error patching dashboard config: {str(e)}"}]}


@register_tool(
    name="delete_dashboard_config",
    description=(
        "Delete (reset) the configuration of a Lovelace dashboard to empty. "
        'Use url_path="default" for the main Overview dashboard.'
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            }
        },
        "required": ["url_path"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Delete dashboard configuration."""
    from ..dashboard_manager import delete_dashboard_config

    try:
        await delete_dashboard_config(hass, arguments["url_path"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully deleted config for dashboard '{arguments['url_path']}'",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting dashboard config: {str(e)}"}]}


@register_tool(
    name="create_dashboard",
    description=(
        "Create a new Lovelace dashboard (experimental). "
        "This uses internal HA APIs that may change between versions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": "URL path for the new dashboard (e.g., 'my-dashboard')",
            },
            "title": {
                "type": "string",
                "description": "Dashboard title shown in sidebar",
            },
            "icon": {
                "type": "string",
                "description": "MDI icon for the sidebar (e.g., 'mdi:view-dashboard')",
            },
            "require_admin": {
                "type": "boolean",
                "description": "Whether the dashboard requires admin access (default: false)",
            },
            "show_in_sidebar": {
                "type": "boolean",
                "description": "Whether to show the dashboard in the sidebar (default: true)",
            },
        },
        "required": ["url_path", "title"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def create_dashboard_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new dashboard."""
    from ..dashboard_manager import create_dashboard

    try:
        item = await create_dashboard(
            hass,
            url_path=arguments["url_path"],
            title=arguments["title"],
            icon=arguments.get("icon"),
            require_admin=arguments.get("require_admin", False),
            show_in_sidebar=arguments.get("show_in_sidebar", True),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Successfully created dashboard '{arguments['url_path']}': "
                        f"{json.dumps(item, indent=2, cls=_HAJSONEncoder)}"
                    ),
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating dashboard: {str(e)}"}]}


@register_tool(
    name="update_dashboard",
    description=(
        "Update a Lovelace dashboard's metadata such as title, icon, or visibility "
        "(experimental). This uses internal HA APIs that may change between versions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": "URL path of the dashboard to update",
            },
            "title": {
                "type": "string",
                "description": "New dashboard title",
            },
            "icon": {
                "type": "string",
                "description": "New MDI icon (e.g., 'mdi:view-dashboard')",
            },
            "require_admin": {
                "type": "boolean",
                "description": "Whether the dashboard requires admin access",
            },
            "show_in_sidebar": {
                "type": "boolean",
                "description": "Whether to show the dashboard in the sidebar",
            },
        },
        "required": ["url_path"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def update_dashboard_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update dashboard metadata."""
    from ..dashboard_manager import update_dashboard

    url_path = arguments["url_path"]
    fields = {k: v for k, v in arguments.items() if k != "url_path"}

    try:
        item = await update_dashboard(hass, url_path, **fields)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Successfully updated dashboard '{url_path}': "
                        f"{json.dumps(item, indent=2, cls=_HAJSONEncoder)}"
                    ),
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating dashboard: {str(e)}"}]}


@register_tool(
    name="delete_dashboard",
    description=(
        "Delete a Lovelace dashboard and its stored configuration (experimental). "
        "This uses internal HA APIs that may change between versions. "
        "Cannot delete the default dashboard."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": "URL path of the dashboard to delete",
            }
        },
        "required": ["url_path"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_dashboard_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a dashboard."""
    from ..dashboard_manager import delete_dashboard

    try:
        await delete_dashboard(hass, arguments["url_path"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully deleted dashboard '{arguments['url_path']}'",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting dashboard: {str(e)}"}]}

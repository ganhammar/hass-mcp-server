"""Dashboard tools."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import _HAJSONEncoder, register_tool

_LOGGER = logging.getLogger(__name__)


@register_tool(
    name="list_dashboards",
    description="List all Lovelace dashboards with metadata (url_path, title, icon, mode)",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_dashboards_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all dashboards."""
    from ..dashboard_manager import list_dashboards

    try:
        dashboards = await list_dashboards(hass)
        return {"content": [{"type": "text", "text": json.dumps(dashboards, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing dashboards: {str(e)}"}]}


@register_tool(
    name="get_dashboard_config",
    description=(
        "Get the full configuration (views and cards) of a Lovelace dashboard. "
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
)
async def get_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Get dashboard configuration."""
    from ..dashboard_manager import get_dashboard_config

    try:
        config = await get_dashboard_config(hass, arguments["url_path"])
        return {"content": [{"type": "text", "text": json.dumps(config, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting dashboard config: {str(e)}"}]}


@register_tool(
    name="save_dashboard_config",
    description=(
        "Save (replace) the full configuration of a Lovelace dashboard. "
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
            },
            "config": {
                "type": "object",
                "description": "Full dashboard config with views and cards",
            },
        },
        "required": ["url_path", "config"],
    },
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

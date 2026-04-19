"""Automation, scene, and script CRUD and read tools."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import _HAJSONEncoder, register_tool

_LOGGER = logging.getLogger(__name__)


# --- Automation Tools ---


@register_tool(
    name="create_automation",
    description="Create a new automation in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": (
                    "Automation configuration (alias, trigger, action, condition, mode, etc.)"
                ),
            }
        },
        "required": ["config"],
    },
)
async def create_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new automation."""
    from ..config_manager import create_list_entry

    try:
        entry_id = await create_list_entry(
            hass, "automations.yaml", arguments["config"], "automation"
        )
        return {
            "content": [
                {"type": "text", "text": f"Successfully created automation with id: {entry_id}"}
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating automation: {str(e)}"}]}


@register_tool(
    name="update_automation",
    description="Update an existing automation in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The automation ID to update",
            },
            "config": {
                "type": "object",
                "description": "Updated automation config"
                " (alias, trigger, action, condition, mode, etc.)",
            },
        },
        "required": ["automation_id", "config"],
    },
)
async def update_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing automation."""
    from ..config_manager import update_list_entry

    try:
        await update_list_entry(
            hass,
            "automations.yaml",
            arguments["automation_id"],
            arguments["config"],
            "automation",
        )
        return {"content": [{"type": "text", "text": "Successfully updated automation"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating automation: {str(e)}"}]}


@register_tool(
    name="delete_automation",
    description="Delete an automation from Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The automation ID to delete",
            }
        },
        "required": ["automation_id"],
    },
)
async def delete_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete an automation."""
    from ..config_manager import delete_list_entry

    try:
        await delete_list_entry(hass, "automations.yaml", arguments["automation_id"], "automation")
        return {"content": [{"type": "text", "text": "Successfully deleted automation"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting automation: {str(e)}"}]}


@register_tool(
    name="list_automations",
    description="List all automations with their full configuration from automations.yaml",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_automations(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all automations with full config."""
    from ..config_manager import read_list_entries

    try:
        entries = await read_list_entries(hass, "automations.yaml")
        return {"content": [{"type": "text", "text": json.dumps(entries, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing automations: {str(e)}"}]}


@register_tool(
    name="get_automation_config",
    description="Get the full configuration of a single automation by its ID",
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The automation ID",
            }
        },
        "required": ["automation_id"],
    },
)
async def get_automation_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a single automation's full config."""
    from ..config_manager import read_list_entry

    try:
        entry = await read_list_entry(hass, "automations.yaml", arguments["automation_id"])
        return {"content": [{"type": "text", "text": json.dumps(entry, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting automation config: {str(e)}"}]}


# --- Scene Tools ---


@register_tool(
    name="create_scene",
    description="Create a new scene in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": "Scene configuration (name, entities, etc.)",
            }
        },
        "required": ["config"],
    },
)
async def create_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new scene."""
    from ..config_manager import create_list_entry

    try:
        entry_id = await create_list_entry(hass, "scenes.yaml", arguments["config"], "scene")
        return {
            "content": [{"type": "text", "text": f"Successfully created scene with id: {entry_id}"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating scene: {str(e)}"}]}


@register_tool(
    name="update_scene",
    description="Update an existing scene in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "scene_id": {
                "type": "string",
                "description": "The scene ID to update",
            },
            "config": {
                "type": "object",
                "description": "Updated scene configuration (name, entities, etc.)",
            },
        },
        "required": ["scene_id", "config"],
    },
)
async def update_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing scene."""
    from ..config_manager import update_list_entry

    try:
        await update_list_entry(
            hass, "scenes.yaml", arguments["scene_id"], arguments["config"], "scene"
        )
        return {"content": [{"type": "text", "text": "Successfully updated scene"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating scene: {str(e)}"}]}


@register_tool(
    name="delete_scene",
    description="Delete a scene from Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "scene_id": {
                "type": "string",
                "description": "The scene ID to delete",
            }
        },
        "required": ["scene_id"],
    },
)
async def delete_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a scene."""
    from ..config_manager import delete_list_entry

    try:
        await delete_list_entry(hass, "scenes.yaml", arguments["scene_id"], "scene")
        return {"content": [{"type": "text", "text": "Successfully deleted scene"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting scene: {str(e)}"}]}


@register_tool(
    name="list_scenes",
    description="List all scenes with their full configuration from scenes.yaml",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_scenes(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all scenes with full config."""
    from ..config_manager import read_list_entries

    try:
        entries = await read_list_entries(hass, "scenes.yaml")
        return {"content": [{"type": "text", "text": json.dumps(entries, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing scenes: {str(e)}"}]}


@register_tool(
    name="get_scene_config",
    description="Get the full configuration of a single scene by its ID",
    input_schema={
        "type": "object",
        "properties": {
            "scene_id": {
                "type": "string",
                "description": "The scene ID",
            }
        },
        "required": ["scene_id"],
    },
)
async def get_scene_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a single scene's full config."""
    from ..config_manager import read_list_entry

    try:
        entry = await read_list_entry(hass, "scenes.yaml", arguments["scene_id"])
        return {"content": [{"type": "text", "text": json.dumps(entry, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting scene config: {str(e)}"}]}


# --- Script Tools ---


@register_tool(
    name="create_script",
    description="Create a new script in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Script identifier (becomes script.{key} entity)",
            },
            "config": {
                "type": "object",
                "description": "Script configuration (alias, sequence, mode, etc.)",
            },
        },
        "required": ["key", "config"],
    },
)
async def create_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new script."""
    from ..config_manager import create_dict_entry

    try:
        key = await create_dict_entry(
            hass, "scripts.yaml", arguments["key"], arguments["config"], "script"
        )
        return {
            "content": [{"type": "text", "text": f"Successfully created script with key: {key}"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating script: {str(e)}"}]}


@register_tool(
    name="update_script",
    description="Update an existing script in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The script key to update",
            },
            "config": {
                "type": "object",
                "description": "Updated script configuration (alias, sequence, mode, etc.)",
            },
        },
        "required": ["key", "config"],
    },
)
async def update_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing script."""
    from ..config_manager import update_dict_entry

    try:
        await update_dict_entry(
            hass, "scripts.yaml", arguments["key"], arguments["config"], "script"
        )
        return {"content": [{"type": "text", "text": "Successfully updated script"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating script: {str(e)}"}]}


@register_tool(
    name="delete_script",
    description="Delete a script from Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The script key to delete",
            }
        },
        "required": ["key"],
    },
)
async def delete_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a script."""
    from ..config_manager import delete_dict_entry

    try:
        await delete_dict_entry(hass, "scripts.yaml", arguments["key"], "script")
        return {"content": [{"type": "text", "text": "Successfully deleted script"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting script: {str(e)}"}]}


@register_tool(
    name="list_scripts",
    description="List all scripts with their full configuration from scripts.yaml",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_scripts(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all scripts with full config."""
    from ..config_manager import read_dict_entries

    try:
        entries = await read_dict_entries(hass, "scripts.yaml")
        return {"content": [{"type": "text", "text": json.dumps(entries, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing scripts: {str(e)}"}]}


@register_tool(
    name="get_script_config",
    description="Get the full configuration of a single script by its key",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The script key (e.g., morning_routine)",
            }
        },
        "required": ["key"],
    },
)
async def get_script_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a single script's full config."""
    from ..config_manager import read_dict_entry

    try:
        entry = await read_dict_entry(hass, "scripts.yaml", arguments["key"])
        return {"content": [{"type": "text", "text": json.dumps(entry, indent=2, cls=_HAJSONEncoder)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting script config: {str(e)}"}]}

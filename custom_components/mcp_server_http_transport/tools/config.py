"""Automation, scene, and script CRUD and read tools."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    dumps,
    register_tool,
)

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
    annotations=ANNOTATION_NON_IDEMPOTENT,
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
    annotations=ANNOTATION_IDEMPOTENT,
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
    annotations=ANNOTATION_DESTRUCTIVE,
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
    annotations=ANNOTATION_READ_ONLY,
)
async def list_automations(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all automations with full config."""
    from ..config_manager import read_list_entries

    try:
        entries = await read_list_entries(hass, "automations.yaml")
        return {"content": [{"type": "text", "text": dumps(entries)}]}
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
    annotations=ANNOTATION_READ_ONLY,
)
async def get_automation_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a single automation's full config."""
    from ..config_manager import read_list_entry

    try:
        entry = await read_list_entry(hass, "automations.yaml", arguments["automation_id"])
        return {"content": [{"type": "text", "text": dumps(entry)}]}
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
    annotations=ANNOTATION_IDEMPOTENT,
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
    annotations=ANNOTATION_IDEMPOTENT,
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
    annotations=ANNOTATION_DESTRUCTIVE,
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
    annotations=ANNOTATION_READ_ONLY,
)
async def list_scenes(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all scenes with full config."""
    from ..config_manager import read_list_entries

    try:
        entries = await read_list_entries(hass, "scenes.yaml")
        return {"content": [{"type": "text", "text": dumps(entries)}]}
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
    annotations=ANNOTATION_READ_ONLY,
)
async def get_scene_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a single scene's full config."""
    from ..config_manager import read_list_entry

    try:
        entry = await read_list_entry(hass, "scenes.yaml", arguments["scene_id"])
        return {"content": [{"type": "text", "text": dumps(entry)}]}
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
    annotations=ANNOTATION_IDEMPOTENT,
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
    annotations=ANNOTATION_IDEMPOTENT,
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
    annotations=ANNOTATION_DESTRUCTIVE,
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
    annotations=ANNOTATION_READ_ONLY,
)
async def list_scripts(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all scripts with full config."""
    from ..config_manager import read_dict_entries

    try:
        entries = await read_dict_entries(hass, "scripts.yaml")
        return {"content": [{"type": "text", "text": dumps(entries)}]}
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
    annotations=ANNOTATION_READ_ONLY,
)
async def get_script_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a single script's full config."""
    from ..config_manager import read_dict_entry

    try:
        entry = await read_dict_entry(hass, "scripts.yaml", arguments["key"])
        return {"content": [{"type": "text", "text": dumps(entry)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting script config: {str(e)}"}]}


@register_tool(
    name="update_automation_json",
    description=(
        "Preview or save a COMPLETE existing automation using a JSON string. "
        "apply=false by default, so previews never write. "
        "An apply creates a backup, checks the HA configuration, and restores "
        "automations.yaml on validation failure. NEVER reloads automations. "
        "A separate explicit automation.reload action is required later."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "Existing ID in automations.yaml",
            },
            "config_json": {
                "type": "string",
                "description": (
                    "A JSON-encoded string containing the full automation config, "
                    "not an object. Include the existing triggers, conditions, "
                    "actions, mode and all other keys. Obtain it using "
                    "get_automation_config first."
                ),
            },
            "apply": {
                "type": "boolean",
                "description": "False (default): preview only. True: write without reload.",
            },
        },
        "required": ["automation_id", "config_json"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def update_automation_json(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Safely update automations.yaml without calling automation.reload."""
    import json
    import os
    from pathlib import Path

    from homeassistant.util.yaml import dumper as yaml_dumper

    from ..config_manager import _load_yaml_list, read_list_entry
    from .config_files import _atomic_write, _create_backup_sync, _run_config_check

    def result(message: str, error: bool = False) -> dict[str, Any]:
        response = {"content": [{"type": "text", "text": message}]}
        if error:
            response["isError"] = True
        return response

    automation_id = arguments["automation_id"]
    if not isinstance(automation_id, str) or not automation_id:
        return result("automation_id must be a non-empty string", True)

    try:
        new_config = json.loads(arguments["config_json"])
        if not isinstance(new_config, dict) or not new_config:
            raise ValueError("config_json must contain a non-empty JSON object")
        if not ("triggers" in new_config or "trigger" in new_config):
            raise ValueError("Complete config must include triggers or trigger")
        if not ("actions" in new_config or "action" in new_config):
            raise ValueError("Complete config must include actions or action")
        if "id" in new_config and str(new_config["id"]) != automation_id:
            raise ValueError("config_json ID differs from automation_id")

        original = await read_list_entry(hass, "automations.yaml", automation_id)
        missing = sorted(set(original) - set(new_config) - {"id"})
        if missing:
            raise ValueError(f"Config is incomplete; original fields missing: {missing}")

        new_config["id"] = automation_id
        changed = sorted(
            key for key in (original.keys() | new_config.keys())
            if original.get(key) != new_config.get(key)
        )
        if not changed:
            return result(f"Automation {automation_id} unchanged; no action needed")
        if arguments.get("apply") is not True:
            return result(
                f"PREVIEW ONLY for {automation_id}; changed top-level keys: {changed}. "
                "No files modified, no reload. Review the full configuration "
                "before resubmitting with apply=true."
            )

        path = Path(hass.config.path("automations.yaml"))
        backup_dir = await hass.async_add_executor_job(
            _create_backup_sync, Path(hass.config.config_dir)
        )
        if not backup_dir:
            raise RuntimeError("Could not create backup; refusing to edit")
        backup_file = Path(hass.config.config_dir) / backup_dir / "automations.yaml"
        if not backup_file.is_file():
            raise RuntimeError("Backup is missing automations.yaml; refusing to edit")

        def write_updated() -> None:
            rows = _load_yaml_list(str(path))
            indexes = [
                index for index, row in enumerate(rows)
                if str(row.get("id")) == automation_id
            ]
            if len(indexes) != 1:
                raise ValueError(f"Expected exactly one entry for {automation_id}")
            # Reject a concurrent edit between preview and write.
            if rows[indexes[0]] != original:
                raise RuntimeError("Automation changed since it was read; refusing to overwrite")
            rows[indexes[0]] = new_config
            tmp = path.with_name(f".{path.name}.mcp_update_tmp")
            try:
                yaml_dumper.save_yaml(str(tmp), rows)
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()

        def restore_backup() -> None:
            _atomic_write(path, backup_file.read_text(encoding="utf-8"))

        wrote = False
        try:
            await hass.async_add_executor_job(write_updated)
            wrote = True
            check = await _run_config_check(hass)
            if not check["valid"]:
                raise ValueError(f"HA config validation failed: {check['errors']}")
        except Exception as write_error:
            if not wrote:
                return result(
                    f"Write refused or failed before commit: {write_error}. "
                    f"No reload. Backup: {backup_dir}", True
                )
            # This restores ALL automations, not just the target, from the snapshot.
            # There is deliberately no reload either on failure or on success.
            try:
                await hass.async_add_executor_job(restore_backup)
            except Exception as rollback_error:
                return result(
                    f"Write/check failed: {write_error}. CRITICAL: restore failed: "
                    f"{rollback_error}. Manual backup: {backup_dir}", True
                )
            return result(
                f"Write/check failed: {write_error}. Backup restored without reload. "
                f"Backup: {backup_dir}", True
            )

        return result(
            f"Saved automation {automation_id} with config check OK. "
            f"Backup: {backup_dir}. NO automation reload performed; "
            "changes will not be active until explicitly reloaded."
        )
    except Exception as exc:
        return result(f"No automation update applied: {exc}", True)

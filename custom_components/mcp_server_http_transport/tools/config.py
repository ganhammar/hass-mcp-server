"""Automation, scene, and script CRUD and read tools."""

import hashlib
import json
import logging
import os
from pathlib import Path
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
from .categories import CATEGORY_PROPERTY, async_category_suffix

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
            },
            "category": CATEGORY_PROPERTY,
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
        suffix = await async_category_suffix(hass, arguments, "automation", "automation", entry_id)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully created automation with id: {entry_id}{suffix}",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating automation: {str(e)}"}]}


def _automation_config_hash(config: dict[str, Any]) -> str:
    """Compute a stable fingerprint for optimistic concurrency checks."""
    canonical = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@register_tool(
    name="update_automation",
    description=(
        "Update an existing automation with a backup and config validation. "
        "dry_run=true previews changed keys and returns an expected_hash without writing. "
        "Pass expected_hash on the subsequent update to prevent overwriting edits "
        "made after the preview. reload=true by default; set reload=false to stage "
        "validated changes without calling automation.reload."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The existing automation ID",
            },
            "config": {
                "type": "object",
                "description": "Complete replacement automation configuration as an object",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Default false; preview changed keys without writing",
            },
            "reload": {
                "type": "boolean",
                "description": "Default true; reload automations after a validated write",
            },
            "expected_hash": {
                "type": "string",
                "description": "Optional original hash returned by a previous dry_run preview",
            },
        },
        "required": ["automation_id", "config"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def update_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Preview or atomically write an automation, validating before optional reload."""
    from homeassistant.util.yaml import dumper as yaml_dumper

    from ..config_manager import _load_yaml_list, read_list_entry
    from .config_files import _atomic_write, _create_backup_sync, _run_config_check

    def result(message: str, error: bool = False) -> dict[str, Any]:
        response = {"content": [{"type": "text", "text": message}]}
        if error:
            response["isError"] = True
        return response

    automation_id = arguments.get("automation_id")
    if not isinstance(automation_id, str) or not automation_id:
        return result("automation_id must be a non-empty string", True)

    submitted = arguments.get("config")
    if not isinstance(submitted, dict) or not submitted:
        return result("config must be a non-empty object", True)
    if "id" in submitted and str(submitted["id"]) != automation_id:
        return result("config ID differs from automation_id", True)

    dry_run = arguments.get("dry_run", False)
    reload = arguments.get("reload", True)
    expected_hash = arguments.get("expected_hash")
    if not isinstance(dry_run, bool) or not isinstance(reload, bool):
        return result("dry_run and reload must be booleans", True)
    if expected_hash is not None and not isinstance(expected_hash, str):
        return result("expected_hash must be a string", True)

    new_config = {**submitted, "id": automation_id}
    try:
        original = await read_list_entry(hass, "automations.yaml", automation_id)
        original_hash = _automation_config_hash(original)

        sentinel = object()
        changed = sorted(
            key
            for key in original.keys() | new_config.keys()
            if original.get(key, sentinel) != new_config.get(key, sentinel)
        )
        if dry_run:
            return result(
                f"PREVIEW ONLY for {automation_id}; changed top-level keys: {changed}. "
                f"Original hash: {original_hash}. No files modified or reloaded. "
                "Pass this hash as expected_hash when applying the update."
            )
        if expected_hash is not None and expected_hash != original_hash:
            return result(
                "Automation changed since preview; expected_hash does not match. "
                "Run dry_run again before updating.",
                True,
            )
        if not changed:
            return result(f"Automation {automation_id} unchanged; no action needed")

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
                index
                for index, row in enumerate(rows)
                if str(row.get("id")) == automation_id
            ]
            if len(indexes) != 1:
                raise ValueError(f"Expected exactly one entry for {automation_id}")
            current = rows[indexes[0]]
            if current != original:
                raise RuntimeError("Automation changed since it was read; refusing to overwrite")
            if expected_hash is not None and _automation_config_hash(current) != expected_hash:
                raise RuntimeError("Automation changed since preview; refusing to overwrite")
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
                    f"No reload. Backup: {backup_dir}",
                    True,
                )
            try:
                await hass.async_add_executor_job(restore_backup)
            except Exception as rollback_error:
                return result(
                    f"Write/check failed: {write_error}. CRITICAL: restore failed: "
                    f"{rollback_error}. Manual backup: {backup_dir}",
                    True,
                )
            return result(
                f"Write/check failed: {write_error}. Backup restored without reload. "
                f"Backup: {backup_dir}",
                True,
            )

        if reload:
            try:
                await hass.services.async_call("automation", "reload", blocking=True)
            except Exception as reload_error:
                return result(
                    f"Saved automation {automation_id} and config check passed, "
                    f"but reload failed: {reload_error}. Backup: {backup_dir}",
                    True,
                )

        status = "automations reloaded" if reload else "no reload performed"
        return result(
            f"Successfully updated automation {automation_id}; config check passed, "
            f"{status}. Backup: {backup_dir}"
        )
    except Exception as exc:
        return result(f"No automation update applied: {exc}", True)


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
            },
            "category": CATEGORY_PROPERTY,
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
        # Scenes from scenes.yaml belong to the "homeassistant" scene platform.
        suffix = await async_category_suffix(hass, arguments, "scene", "homeassistant", entry_id)
        return {
            "content": [
                {"type": "text", "text": f"Successfully created scene with id: {entry_id}{suffix}"}
            ]
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
            "category": CATEGORY_PROPERTY,
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
        suffix = await async_category_suffix(hass, arguments, "script", "script", key)
        return {
            "content": [
                {"type": "text", "text": f"Successfully created script with key: {key}{suffix}"}
            ]
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

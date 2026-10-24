"""Automation, scene, and script CRUD and read tools."""

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_dumps_sorted

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    dumps,
    register_tool,
)
from .categories import CATEGORY_PROPERTY, async_category_suffix
from .icons import ICON_PROPERTY, async_icon_suffix

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
            "icon": ICON_PROPERTY,
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
        suffix += await async_icon_suffix(hass, arguments, "automation", "automation", entry_id)
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
    """Compute a stable original-config fingerprint for preview/apply concurrency."""
    return hashlib.sha256(json_dumps_sorted(config).encode("utf-8")).hexdigest()


@register_tool(
    name="update_automation",
    description=(
        "Update an automation. By default save and reload as before, without a full "
        "configuration check. dry_run=true previews changes and returns an expected_hash. "
        "Pass expected_hash to reject a stale preview. reload=false skips the reload. "
        "validate=true opts into a full Home Assistant config check with rollback of "
        "automations.yaml on failure."
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
                "description": "Complete replacement automation configuration",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview changed top-level keys without writing (default false)",
            },
            "reload": {
                "type": "boolean",
                "description": "Reload automations after updating (default true)",
            },
            "validate": {
                "type": "boolean",
                "description": (
                    "Opt into full HA configuration validation and rollback on failure "
                    "(default false; may be slow on large installations)"
                ),
            },
            "expected_hash": {
                "type": "string",
                "description": "Optional original hash returned by a dry_run preview",
            },
            "icon": ICON_PROPERTY,
        },
        "required": ["automation_id", "config"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def update_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep default updates cheap; offer preview, concurrency checks, and opt-in validation."""
    from homeassistant.util.yaml import dumper as yaml_dumper

    from ..config_manager import _load_yaml_list, read_list_entry, update_list_entry
    from .config_files import _run_config_check

    def result(message: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": message}]}

    automation_id = arguments.get("automation_id")
    submitted = arguments.get("config")
    if not isinstance(automation_id, str) or not automation_id:
        return result("Error updating automation: automation_id must be a non-empty string")
    if not isinstance(submitted, dict) or not submitted:
        return result("Error updating automation: config must be a non-empty object")
    if "id" in submitted and str(submitted["id"]) != automation_id:
        return result("Error updating automation: config ID differs from automation_id")

    dry_run = arguments.get("dry_run", False)
    reload_automations = arguments.get("reload", True)
    validate = arguments.get("validate", False)
    expected_hash = arguments.get("expected_hash")
    if any(type(flag) is not bool for flag in (dry_run, reload_automations, validate)):
        return result("Error updating automation: dry_run, reload and validate must be booleans")
    if expected_hash is not None and not isinstance(expected_hash, str):
        return result("Error updating automation: expected_hash must be a string")

    # Keep the existing fast path (and its mockable CRUD contract) unchanged.
    if not dry_run and reload_automations and not validate and expected_hash is None:
        try:
            await update_list_entry(
                hass, "automations.yaml", automation_id, submitted, "automation",
            )
            suffix = await async_icon_suffix(
                hass, arguments, "automation", "automation", automation_id
            )
            return result(f"Successfully updated automation{suffix}")
        except Exception as exc:
            return result(f"Error updating automation: {exc}")

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
                "Error updating automation: changed since preview; expected_hash "
                "does not match. Run dry_run again before updating."
            )
        if not changed:
            suffix = await async_icon_suffix(
                hass, arguments, "automation", "automation", automation_id
            )
            if suffix:
                return result(f"Successfully updated automation icon{suffix}")
            return result(f"Automation {automation_id} unchanged; no action needed")

        path = Path(hass.config.path("automations.yaml"))

        def write_updated() -> Path | None:
            """Commit one YAML update, preserving only the original file when validating."""
            rows = _load_yaml_list(str(path))
            indexes = [
                index for index, row in enumerate(rows) if str(row.get("id")) == automation_id
            ]
            if len(indexes) != 1:
                raise ValueError(f"Expected exactly one automation with id {automation_id}")
            current = rows[indexes[0]]
            if current != original:
                raise RuntimeError("Automation changed since it was read; refusing to overwrite")
            if expected_hash is not None and _automation_config_hash(current) != expected_hash:
                raise RuntimeError("Automation changed since preview; refusing to overwrite")

            backup: Path | None = None
            if validate:
                with tempfile.NamedTemporaryFile(
                    prefix=".automations.mcp_backup_", dir=path.parent, delete=False
                ) as backup_file:
                    backup = Path(backup_file.name)
                try:
                    shutil.copy2(path, backup)
                except Exception:
                    backup.unlink(missing_ok=True)
                    raise

            rows[indexes[0]] = new_config
            with tempfile.NamedTemporaryFile(
                prefix=f".{path.name}.mcp_update_",
                suffix=".yaml",
                dir=path.parent,
                delete=False,
            ) as staged:
                temp = Path(staged.name)
            try:
                yaml_dumper.save_yaml(str(temp), rows)
                shutil.copymode(path, temp)
                os.replace(temp, path)
            except Exception:
                if backup is not None:
                    backup.unlink(missing_ok=True)
                raise
            finally:
                temp.unlink(missing_ok=True)
            return backup

        backup = await hass.async_add_executor_job(write_updated)
        if validate:
            try:
                check = await _run_config_check(hass)
                if not check["valid"]:
                    raise ValueError(f"HA config validation failed: {check['errors']}")
            except Exception as exc:
                try:
                    await hass.async_add_executor_job(os.replace, backup, path)
                except Exception as restore_exc:
                    # Preserve the backup for manual recovery if restoring fails.
                    return result(
                        f"Error updating automation: validation failed ({exc}); "
                        f"CRITICAL: rollback failed ({restore_exc}). Backup: {backup}"
                    )
                return result(
                    f"Error updating automation: validation failed ({exc}); "
                    "automations.yaml restored without reload."
                )
            # HomeAssistant.async_add_executor_job accepts positional args only.
            await hass.async_add_executor_job(lambda: backup.unlink(missing_ok=True))

        if reload_automations:
            try:
                await hass.services.async_call("automation", "reload", blocking=True)
            except Exception as exc:
                return result(f"Error updating automation: saved but reload failed: {exc}")

        suffix = await async_icon_suffix(
            hass, arguments, "automation", "automation", automation_id
        )
        status = "automations reloaded" if reload_automations else "no reload performed"
        validation = "config validated, " if validate else ""
        return result(
            f"Successfully updated automation {automation_id}; {validation}{status}{suffix}"
        )
    except Exception as exc:
        return result(f"Error updating automation: {exc}")


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
            "icon": ICON_PROPERTY,
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
        suffix += await async_icon_suffix(hass, arguments, "scene", "homeassistant", entry_id)
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
            "icon": ICON_PROPERTY,
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
        suffix = await async_icon_suffix(
            hass, arguments, "scene", "homeassistant", arguments["scene_id"]
        )
        return {"content": [{"type": "text", "text": f"Successfully updated scene{suffix}"}]}
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
            "icon": ICON_PROPERTY,
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
        suffix += await async_icon_suffix(hass, arguments, "script", "script", key)
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
            "icon": ICON_PROPERTY,
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
        suffix = await async_icon_suffix(hass, arguments, "script", "script", arguments["key"])
        return {"content": [{"type": "text", "text": f"Successfully updated script{suffix}"}]}
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

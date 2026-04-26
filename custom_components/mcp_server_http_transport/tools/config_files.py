"""Config file access tools (list, read, write, delete, backup, restore YAML files)."""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from . import register_tool

_LOGGER = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = {".yaml", ".yml"}
_BLOCKED_NAMES = {"secrets.yaml", "secrets.yml"}
_BACKUP_DIR_NAME = "mcp_backups"

_DISABLED_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": (
                "Config file access is disabled. Enable it in the MCP Server integration "
                "settings: Settings → Devices & Services → MCP Server → Configure → "
                "Enable config file access."
            ),
        }
    ]
}


def _is_enabled(hass: HomeAssistant) -> bool:
    return hass.data.get(DOMAIN, {}).get("config_file_access", False)


def _config_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir)


def _resolve_safe(hass: HomeAssistant, filename: str) -> Path:
    """Return resolved Path inside config dir, or raise ValueError."""
    if os.sep in filename or "/" in filename:
        raise ValueError("Subdirectories are not allowed — only first-level files")
    if filename.lower() in _BLOCKED_NAMES:
        raise ValueError(f"Access to '{filename}' is blocked for security reasons")
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(f"Only YAML files are supported (.yaml, .yml), got '{suffix or filename}'")
    path = _config_dir(hass) / filename
    # Paranoia check: resolved path must still be inside config_dir
    if not path.resolve().is_relative_to(_config_dir(hass).resolve()):
        raise ValueError("Path traversal detected")
    return path


def _yaml_files_in(directory: Path) -> list[Path]:
    """Return sorted first-level YAML files excluding secrets."""
    return sorted(
        entry
        for entry in directory.iterdir()
        if entry.is_file()
        and entry.suffix.lower() in _ALLOWED_SUFFIXES
        and entry.name.lower() not in _BLOCKED_NAMES
    )


async def _run_config_check(hass: HomeAssistant) -> dict[str, Any]:
    """Run HA config validation and return a result dict."""
    from homeassistant.helpers.check_config import async_check_ha_config_file

    res = await async_check_ha_config_file(hass)
    errors = [str(err) for err in res.errors] if res.errors else []
    return {"valid": len(errors) == 0, "errors": errors}


def _create_backup(hass: HomeAssistant) -> str | None:
    """Snapshot all first-level YAML files into mcp_backups/; return relative path or None."""
    config_dir = _config_dir(hass)
    files = _yaml_files_in(config_dir)
    if not files:
        return None
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    backup_dir = config_dir / _BACKUP_DIR_NAME / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        shutil.copy2(src, backup_dir / src.name)
    return f"{_BACKUP_DIR_NAME}/{timestamp}"


@register_tool(
    name="list_config_files",
    description=(
        "List YAML configuration files in the Home Assistant config directory "
        "(first level only; secrets.yaml and .storage are excluded)"
    ),
    input_schema={"type": "object", "properties": {}},
)
async def list_config_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List first-level YAML files in the config directory."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    config_dir = _config_dir(hass)
    try:
        files = [f.name for f in _yaml_files_in(config_dir)]
        return {"content": [{"type": "text", "text": json.dumps(files, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing config files: {e}"}]}


@register_tool(
    name="get_config_file",
    description=(
        "Read the contents of a YAML configuration file from the Home Assistant config directory. "
        "First-level files only; secrets.yaml is blocked"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File name, e.g. 'automations.yaml' or 'configuration.yaml'",
            }
        },
        "required": ["filename"],
    },
)
async def get_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read a YAML config file."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        path = _resolve_safe(hass, arguments["filename"])
        if not path.exists():
            return {
                "content": [
                    {"type": "text", "text": f"File '{arguments['filename']}' does not exist"}
                ]
            }
        size = path.stat().st_size
        if size > 1_048_576:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"File '{arguments['filename']}' is too large ({size} bytes). "
                            "Maximum allowed size is 1 MB"
                        ),
                    }
                ]
            }
        content = path.read_text(encoding="utf-8")
        return {"content": [{"type": "text", "text": content}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error reading config file: {e}"}]}


@register_tool(
    name="save_config_file",
    description=(
        "Write or replace a YAML configuration file in the Home Assistant config directory. "
        "First-level files only; secrets.yaml is blocked. "
        "Creates the file if it does not exist. "
        "Automatically backs up all YAML files before writing and runs a config check after"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File name, e.g. 'automations.yaml'",
            },
            "content": {
                "type": "string",
                "description": "Full YAML content to write",
            },
            "run_check": {
                "type": "boolean",
                "description": (
                    "Run Home Assistant config validation after saving (default: true). "
                    "Reports errors without undoing the save"
                ),
            },
        },
        "required": ["filename", "content"],
    },
)
async def save_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Back up all YAML files, write the new content, then optionally validate."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        path = _resolve_safe(hass, arguments["filename"])
        backup_path = _create_backup(hass)
        path.write_text(arguments["content"], encoding="utf-8")
        lines = [f"Successfully saved '{arguments['filename']}'"]
        if backup_path:
            lines.append(f"Backup: {backup_path}")

        if arguments.get("run_check", True):
            try:
                check = await _run_config_check(hass)
                if check["valid"]:
                    lines.append("Config check: OK")
                else:
                    lines.append("Config check: ERRORS FOUND")
                    for err in check["errors"]:
                        lines.append(f"  - {err}")
            except Exception as check_err:
                lines.append(f"Config check failed to run: {check_err}")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error saving config file: {e}"}]}


@register_tool(
    name="delete_config_file",
    description=(
        "Delete a YAML configuration file from the Home Assistant config directory. "
        "First-level files only; secrets.yaml is blocked. "
        "Automatically backs up all YAML files before deleting"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File name to delete, e.g. 'my_custom.yaml'",
            }
        },
        "required": ["filename"],
    },
)
async def delete_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Back up all YAML files, then delete the target file."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        path = _resolve_safe(hass, arguments["filename"])
        if not path.exists():
            return {
                "content": [
                    {"type": "text", "text": f"File '{arguments['filename']}' does not exist"}
                ]
            }
        backup_path = _create_backup(hass)
        path.unlink()
        lines = [f"Successfully deleted '{arguments['filename']}'"]
        if backup_path:
            lines.append(f"Backup: {backup_path}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting config file: {e}"}]}


@register_tool(
    name="backup_config_files",
    description=(
        "Create a timestamped backup of all first-level YAML configuration files "
        "into a 'mcp_backups/<timestamp>' subfolder inside the config directory. "
        "secrets.yaml is never included. "
        "Call this before bulk edits to preserve a rollback snapshot"
    ),
    input_schema={"type": "object", "properties": {}},
)
async def backup_config_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Copy all first-level YAML files (except secrets) into a timestamped backup folder."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        backup_path = _create_backup(hass)
        if backup_path is None:
            return {"content": [{"type": "text", "text": "No YAML files found to back up"}]}
        backup_dir = _config_dir(hass) / backup_path
        backed_up = sorted(f.name for f in backup_dir.iterdir() if f.is_file())
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Backup created at '{backup_path}' "
                        f"({len(backed_up)} files):\n" + "\n".join(f"  - {f}" for f in backed_up)
                    ),
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating backup: {e}"}]}


@register_tool(
    name="list_config_backups",
    description=(
        "List all available config file backups created by backup_config_files, "
        "newest first. Shows the timestamp and number of files in each backup"
    ),
    input_schema={"type": "object", "properties": {}},
)
async def list_config_backups(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List available backup snapshots, newest first."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        backup_root = _config_dir(hass) / _BACKUP_DIR_NAME
        if not backup_root.exists():
            return {"content": [{"type": "text", "text": "No backups found"}]}

        backups = sorted(
            (d for d in backup_root.iterdir() if d.is_dir()),
            key=lambda d: d.name,
            reverse=True,
        )

        if not backups:
            return {"content": [{"type": "text", "text": "No backups found"}]}

        result = []
        for backup_dir in backups:
            files = [f.name for f in backup_dir.iterdir() if f.is_file()]
            result.append({"timestamp": backup_dir.name, "files": sorted(files)})

        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing backups: {e}"}]}


@register_tool(
    name="restore_config_backup",
    description=(
        "Restore YAML config files from a backup snapshot. "
        "Restores the latest backup by default, or a specific one by timestamp. "
        "Only files present in the backup are overwritten; "
        "files added after the backup are left untouched. "
        "Automatically runs a config check after restoring"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "string",
                "description": (
                    "Backup timestamp to restore (as shown by list_config_backups). "
                    "Omit to restore the latest backup"
                ),
            }
        },
    },
)
async def restore_config_backup(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Restore files from a backup snapshot into the config directory."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        backup_root = _config_dir(hass) / _BACKUP_DIR_NAME
        if not backup_root.exists():
            return {"content": [{"type": "text", "text": "No backups found"}]}

        if "timestamp" in arguments:
            backup_dir = backup_root / arguments["timestamp"]
            if not backup_dir.is_dir():
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Backup '{arguments['timestamp']}' not found",
                        }
                    ]
                }
        else:
            candidates = sorted(
                (d for d in backup_root.iterdir() if d.is_dir()), key=lambda d: d.name
            )
            if not candidates:
                return {"content": [{"type": "text", "text": "No backups found"}]}
            backup_dir = candidates[-1]

        config_dir = _config_dir(hass)
        restored = []
        for src in sorted(backup_dir.iterdir()):
            if src.is_file() and src.suffix.lower() in _ALLOWED_SUFFIXES:
                shutil.copy2(src, config_dir / src.name)
                restored.append(src.name)

        if not restored:
            return {
                "content": [
                    {"type": "text", "text": f"Backup '{backup_dir.name}' contained no YAML files"}
                ]
            }

        lines = [
            f"Restored {len(restored)} files from backup '{backup_dir.name}':",
            *[f"  - {f}" for f in restored],
        ]

        try:
            check = await _run_config_check(hass)
            if check["valid"]:
                lines.append("Config check: OK")
            else:
                lines.append("Config check: ERRORS FOUND")
                for err in check["errors"]:
                    lines.append(f"  - {err}")
        except Exception as check_err:
            lines.append(f"Config check failed to run: {check_err}")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error restoring backup: {e}"}]}

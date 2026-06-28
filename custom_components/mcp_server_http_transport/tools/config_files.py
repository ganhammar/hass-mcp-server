"""Config file access tools (list, read, write, delete, backup, restore YAML files)."""

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = {".yaml", ".yml"}

# Files blocked from direct read/write/delete via the config file tools.
# Each entry maps the lowercase filename to the reason the AI sees in the error,
# so when a write is rejected the AI can pick the right alternative tool instead
# of just seeing "blocked." Two distinct blocking reasons live here:
#   - sensitive data (secrets must never be exposed to the AI)
#   - dedicated tool exists (raw YAML edits would bypass HA's storage layer
#     and clobber UI-managed entries; the tool-specific CRUD goes through HA's
#     own write path and keeps registries consistent)
_BLOCKED_FILES = {
    "secrets.yaml": "contains sensitive credentials and must never be exposed",
    "secrets.yml": "contains sensitive credentials and must never be exposed",
    "automations.yaml": (
        "is owned by Home Assistant's UI and is rewritten on every change. "
        "Use create_automation / update_automation / delete_automation instead — "
        "they go through HA's storage layer and keep UI-managed automations consistent"
    ),
    "scenes.yaml": (
        "is owned by Home Assistant's UI and is rewritten on every change. "
        "Use create_scene / update_scene / delete_scene instead — "
        "they go through HA's storage layer and keep UI-managed scenes consistent"
    ),
    "scripts.yaml": (
        "is owned by Home Assistant's UI and is rewritten on every change. "
        "Use create_script / update_script / delete_script instead — "
        "they go through HA's storage layer and keep UI-managed scripts consistent"
    ),
}

_BACKUP_DIR_NAME = "mcp_backups"
_BACKUP_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d+$")

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
    blocked_reason = _BLOCKED_FILES.get(filename.lower())
    if blocked_reason is not None:
        raise ValueError(f"Access to '{filename}' is blocked: {blocked_reason}")
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(f"Only YAML files are supported (.yaml, .yml), got '{suffix or filename}'")
    path = _config_dir(hass) / filename
    # Paranoia check: resolved path must still be inside config_dir
    if not path.resolve().is_relative_to(_config_dir(hass).resolve()):
        raise ValueError("Path traversal detected")
    return path


_SECRETS_FILES = {"secrets.yaml", "secrets.yml"}


def _yaml_files_in(directory: Path) -> list[Path]:
    """Return sorted first-level YAML files for backups, excluding only secrets.

    Note: this is intentionally narrower than `_BLOCKED_FILES`. The block list
    keeps the AI from rewriting registry-owned files via the config tools, but
    those files MUST still be included in backups — otherwise a restore would
    silently drop the user's automations, scenes, or scripts. Only secrets are
    excluded so they never end up in `mcp_backups/`.
    """
    return sorted(
        entry
        for entry in directory.iterdir()
        if entry.is_file()
        and entry.suffix.lower() in _ALLOWED_SUFFIXES
        and entry.name.lower() not in _SECRETS_FILES
    )


async def _run_config_check(hass: HomeAssistant) -> dict[str, Any]:
    """Run HA config validation and return a result dict."""
    from homeassistant.helpers.check_config import async_check_ha_config_file

    res = await async_check_ha_config_file(hass)
    errors = [str(err) for err in res.errors] if res.errors else []
    return {"valid": len(errors) == 0, "errors": errors}


def _create_backup_sync(config_dir: Path) -> str | None:
    """Snapshot all first-level YAML files into mcp_backups/; return relative path or None."""
    files = _yaml_files_in(config_dir)
    if not files:
        return None
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    backup_dir = config_dir / _BACKUP_DIR_NAME / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        shutil.copy2(src, backup_dir / src.name)
    return f"{_BACKUP_DIR_NAME}/{timestamp}"


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + os.replace.

    Avoids leaving the target half-written if the process dies mid-write.
    """
    tmp = path.with_name(f".{path.name}.mcp_tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


@register_tool(
    name="list_config_files",
    description=(
        "List YAML configuration files in the Home Assistant config directory "
        "(first level only; secrets.yaml is excluded). "
        "Some listed files (automations.yaml, scenes.yaml, scripts.yaml) cannot be "
        "edited directly — use the dedicated CRUD tools for those instead"
    ),
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_READ_ONLY,
)
async def list_config_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List first-level YAML files in the config directory."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    config_dir = _config_dir(hass)
    try:
        files = await hass.async_add_executor_job(_list_yaml_filenames_sync, config_dir)
        return {"content": [{"type": "text", "text": json.dumps(files, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing config files: {e}"}]}


def _list_yaml_filenames_sync(config_dir: Path) -> list[str]:
    """Return the first-level YAML filenames in config_dir, sorted."""
    return [f.name for f in _yaml_files_in(config_dir)]


@register_tool(
    name="get_config_file",
    description=(
        "Read the contents of a YAML configuration file from the Home Assistant config directory. "
        "First-level files only. secrets.yaml is blocked, and "
        "automations.yaml / scenes.yaml / scripts.yaml are blocked from direct access — "
        "use list_automations / list_scenes / list_scripts (and their get_*_config variants) "
        "for those"
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
    annotations=ANNOTATION_READ_ONLY,
)
async def get_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read a YAML config file."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        path = _resolve_safe(hass, arguments["filename"])
        result = await hass.async_add_executor_job(
            _read_config_file_sync, path, arguments["filename"]
        )
        return {"content": [{"type": "text", "text": result}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error reading config file: {e}"}]}


def _read_config_file_sync(path: Path, filename: str) -> str:
    """Return file contents, or a user-facing error string for missing / oversized files."""
    if not path.exists():
        return f"File '{filename}' does not exist"
    size = path.stat().st_size
    if size > 1_048_576:
        return f"File '{filename}' is too large ({size} bytes). Maximum allowed size is 1 MB"
    return path.read_text(encoding="utf-8")


@register_tool(
    name="save_config_file",
    description=(
        "Write or replace a YAML configuration file in the Home Assistant config directory. "
        "First-level files only. secrets.yaml is blocked, and "
        "automations.yaml / scenes.yaml / scripts.yaml are blocked from direct edits — "
        "use create_automation/update_automation, create_scene/update_scene, or "
        "create_script/update_script for those. "
        "Creates the file if it does not exist. "
        "Automatically backs up all YAML files before writing and runs a config check after. "
        "When editing multiple files prefer batch_edit_config_files to avoid redundant backups"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File name, e.g. 'configuration.yaml' or 'templates.yaml'",
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
    annotations=ANNOTATION_IDEMPOTENT,
)
async def save_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Back up all YAML files, write the new content, then optionally validate."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        path = _resolve_safe(hass, arguments["filename"])
        backup_path = await hass.async_add_executor_job(
            _backup_and_write_sync, _config_dir(hass), path, arguments["content"]
        )
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
        "First-level files only. secrets.yaml is blocked, and "
        "automations.yaml / scenes.yaml / scripts.yaml are blocked — "
        "use delete_automation, delete_scene, or delete_script for those. "
        "Automatically backs up all YAML files before deleting. "
        "When deleting multiple files prefer batch_edit_config_files to avoid redundant backups"
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
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Back up all YAML files, then delete the target file."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        path = _resolve_safe(hass, arguments["filename"])
        result = await hass.async_add_executor_job(_backup_and_delete_sync, _config_dir(hass), path)
        if not result["existed"]:
            return {
                "content": [
                    {"type": "text", "text": f"File '{arguments['filename']}' does not exist"}
                ]
            }
        lines = [f"Successfully deleted '{arguments['filename']}'"]
        if result["backup"]:
            lines.append(f"Backup: {result['backup']}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting config file: {e}"}]}


def _backup_and_write_sync(config_dir: Path, path: Path, content: str) -> str | None:
    """Snapshot YAML files then atomically write content to path. Returns backup path."""
    backup_path = _create_backup_sync(config_dir)
    _atomic_write(path, content)
    return backup_path


def _backup_and_delete_sync(config_dir: Path, path: Path) -> dict[str, Any]:
    """Snapshot YAML files then delete path. Returns {existed, backup}."""
    if not path.exists():
        return {"existed": False, "backup": None}
    backup_path = _create_backup_sync(config_dir)
    path.unlink()
    return {"existed": True, "backup": backup_path}


def _apply_batch_edit_sync(
    config_dir: Path,
    save_paths: list[tuple[str, Path, str]],
    delete_paths: list[tuple[str, Path]],
) -> dict[str, Any]:
    """Run batch_edit_config_files's FS work: missing-check, backup, writes, deletes."""
    missing = [fn for fn, path in delete_paths if not path.exists()]
    if missing:
        return {"missing": missing}

    backup_path = _create_backup_sync(config_dir)

    saved: list[str] = []
    errors: list[str] = []
    for filename, path, content in save_paths:
        try:
            _atomic_write(path, content)
            saved.append(filename)
        except Exception as e:
            errors.append(f"save '{filename}': {e}")

    deleted: list[str] = []
    for filename, path in delete_paths:
        try:
            path.unlink()
            deleted.append(filename)
        except Exception as e:
            errors.append(f"delete '{filename}': {e}")

    return {
        "missing": None,
        "backup": backup_path,
        "saved": saved,
        "deleted": deleted,
        "errors": errors,
    }


@register_tool(
    name="batch_edit_config_files",
    description=(
        "Write and/or delete multiple YAML config files in one operation. "
        "Creates one backup before any changes and runs one config check after all changes. "
        "Prefer this over repeated save_config_file or delete_config_file calls "
        "when touching more than one file — avoids redundant backups"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "saves": {
                "type": "array",
                "description": "Files to write or create. Each needs 'filename' and 'content'.",
                "items": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "e.g. 'templates.yaml'"},
                        "content": {"type": "string", "description": "Full YAML content to write"},
                    },
                    "required": ["filename", "content"],
                },
            },
            "deletes": {
                "type": "array",
                "description": "File names to delete.",
                "items": {"type": "string"},
            },
            "run_check": {
                "type": "boolean",
                "description": "Run HA config validation after all changes (default: true)",
            },
        },
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def batch_edit_config_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """One backup → apply all saves → apply all deletes → one config check."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE

    saves: list[dict] = arguments.get("saves") or []
    deletes: list[str] = arguments.get("deletes") or []

    if not saves and not deletes:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: provide at least one entry in 'saves' or 'deletes'",
                }
            ]
        }

    # Phase 1: validate all filenames before touching anything
    try:
        save_paths = [
            (entry["filename"], _resolve_safe(hass, entry["filename"]), entry["content"])
            for entry in saves
        ]
    except (ValueError, KeyError) as e:
        return {"content": [{"type": "text", "text": f"Error in saves: {e}"}]}

    try:
        delete_paths = [(fn, _resolve_safe(hass, fn)) for fn in deletes]
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Error in deletes: {e}"}]}

    # Phases 2-4 (check missing-deletes → backup → write → delete) all touch the
    # filesystem and must run off the event loop in one executor hop.
    batch_result = await hass.async_add_executor_job(
        _apply_batch_edit_sync, _config_dir(hass), save_paths, delete_paths
    )

    if batch_result.get("missing"):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Error: file(s) to delete do not exist: "
                        + ", ".join(batch_result["missing"])
                    ),
                }
            ]
        }

    backup_path = batch_result["backup"]
    saved: list[str] = batch_result["saved"]
    deleted: list[str] = batch_result["deleted"]
    errors: list[str] = batch_result["errors"]

    # Phase 5: build response
    lines: list[str] = []
    if saved:
        lines.append(f"Saved ({len(saved)}): " + ", ".join(saved))
    if deleted:
        lines.append(f"Deleted ({len(deleted)}): " + ", ".join(deleted))
    if backup_path:
        lines.append(f"Backup: {backup_path}")
    if errors:
        lines.append("Errors:")
        lines.extend(f"  - {e}" for e in errors)

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


@register_tool(
    name="backup_config_files",
    description=(
        "Create a timestamped backup of all first-level YAML configuration files "
        "into a 'mcp_backups/<timestamp>' subfolder inside the config directory. "
        "secrets.yaml is never included. "
        "Call this before bulk edits to preserve a rollback snapshot"
    ),
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_IDEMPOTENT,
)
async def backup_config_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Copy all first-level YAML files (except secrets) into a timestamped backup folder."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        result = await hass.async_add_executor_job(_backup_and_list_sync, _config_dir(hass))
        if result["backup"] is None:
            return {"content": [{"type": "text", "text": "No YAML files found to back up"}]}
        files = result["files"]
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Backup created at '{result['backup']}' "
                        f"({len(files)} files):\n" + "\n".join(f"  - {f}" for f in files)
                    ),
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating backup: {e}"}]}


def _backup_and_list_sync(config_dir: Path) -> dict[str, Any]:
    """Snapshot and list the files placed into the new backup directory."""
    backup_path = _create_backup_sync(config_dir)
    if backup_path is None:
        return {"backup": None, "files": []}
    backup_dir = config_dir / backup_path
    files = sorted(f.name for f in backup_dir.iterdir() if f.is_file())
    return {"backup": backup_path, "files": files}


@register_tool(
    name="list_config_backups",
    description=(
        "List all available config file backups created by backup_config_files, "
        "newest first. Shows the timestamp and number of files in each backup"
    ),
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_READ_ONLY,
)
async def list_config_backups(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List available backup snapshots, newest first."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        result = await hass.async_add_executor_job(_list_backups_sync, _config_dir(hass))
        if not result:
            return {"content": [{"type": "text", "text": "No backups found"}]}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing backups: {e}"}]}


def _list_backups_sync(config_dir: Path) -> list[dict[str, Any]]:
    """List backup snapshots and their contents, newest first. Empty list if no backups."""
    backup_root = config_dir / _BACKUP_DIR_NAME
    if not backup_root.exists():
        return []
    backups = sorted(
        (d for d in backup_root.iterdir() if d.is_dir() and _BACKUP_TS_RE.match(d.name)),
        key=lambda d: d.name,
        reverse=True,
    )
    result: list[dict[str, Any]] = []
    for backup_dir in backups:
        files = [f.name for f in backup_dir.iterdir() if f.is_file()]
        result.append({"timestamp": backup_dir.name, "files": sorted(files)})
    return result


@register_tool(
    name="cleanup_config_backups",
    description=(
        "Delete backup snapshots older than a given number of days. "
        "Defaults to 30 days. Use this to keep the mcp_backups folder from growing indefinitely. "
        "Returns the number of snapshots deleted and how many remain"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "older_than_days": {
                "type": "integer",
                "description": "Delete backups older than this many days (default: 30, minimum: 1)",
            }
        },
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def cleanup_config_backups(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete backup snapshots older than older_than_days days."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE

    older_than_days = arguments.get("older_than_days", 30)
    if not isinstance(older_than_days, int) or older_than_days < 1:
        return {
            "content": [{"type": "text", "text": "Error: older_than_days must be an integer >= 1"}]
        }

    try:
        result = await hass.async_add_executor_job(
            _cleanup_backups_sync, _config_dir(hass), older_than_days
        )
        if result is None:
            return {"content": [{"type": "text", "text": "No backups found"}]}
        deleted: list[str] = result["deleted"]
        kept: list[str] = result["kept"]
        lines = [f"Deleted {len(deleted)} backup(s) older than {older_than_days} day(s)."]
        if deleted:
            lines.extend(f"  - {name}" for name in sorted(deleted))
        lines.append(f"{len(kept)} backup(s) remaining.")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error cleaning up backups: {e}"}]}


def _cleanup_backups_sync(config_dir: Path, older_than_days: int) -> dict[str, list[str]] | None:
    """Remove backup snapshots older than older_than_days. Returns None if no backups exist."""
    backup_root = config_dir / _BACKUP_DIR_NAME
    if not backup_root.exists():
        return None
    cutoff = datetime.now() - timedelta(days=older_than_days)
    deleted: list[str] = []
    kept: list[str] = []
    for d in backup_root.iterdir():
        if not d.is_dir() or not _BACKUP_TS_RE.match(d.name):
            continue
        try:
            ts = datetime.strptime(d.name, "%Y-%m-%d_%H-%M-%S-%f")
        except ValueError:
            continue
        if ts < cutoff:
            shutil.rmtree(d)
            deleted.append(d.name)
        else:
            kept.append(d.name)
    if not deleted and not kept:
        return None
    return {"deleted": deleted, "kept": kept}


@register_tool(
    name="restore_config_backup",
    description=(
        "Restore YAML config files from a backup snapshot. "
        "Restores the latest backup by default, or a specific one by timestamp. "
        "Only files present in the backup are overwritten; "
        "files added after the backup are left untouched. "
        "Automatically creates a pre-restore snapshot of the current state "
        "and runs a config check after restoring"
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
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def restore_config_backup(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Restore files from a backup snapshot into the config directory."""
    if not _is_enabled(hass):
        return _DISABLED_RESPONSE
    try:
        result = await hass.async_add_executor_job(
            _restore_backup_sync, _config_dir(hass), arguments.get("timestamp")
        )
        if "error" in result:
            return {"content": [{"type": "text", "text": result["error"]}]}

        lines = [
            f"Restored {len(result['restored'])} files from backup '{result['backup_name']}':",
            *[f"  - {f}" for f in result["restored"]],
        ]
        if result["pre_restore"]:
            lines.append(f"Pre-restore snapshot: {result['pre_restore']}")

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


def _restore_backup_sync(config_dir: Path, timestamp: str | None) -> dict[str, Any]:
    """Pick a backup, snapshot the current state, copy backup files back. Returns a result dict."""
    backup_root = config_dir / _BACKUP_DIR_NAME
    if not backup_root.exists():
        return {"error": "No backups found"}

    if timestamp is not None:
        backup_dir = backup_root / timestamp
        if not backup_dir.is_dir():
            return {"error": f"Backup '{timestamp}' not found"}
    else:
        candidates = sorted(
            (d for d in backup_root.iterdir() if d.is_dir() and _BACKUP_TS_RE.match(d.name)),
            key=lambda d: d.name,
        )
        if not candidates:
            return {"error": "No backups found"}
        backup_dir = candidates[-1]

    pre_restore = _create_backup_sync(config_dir)

    restored: list[str] = []
    for src in sorted(backup_dir.iterdir()):
        if src.is_file() and src.suffix.lower() in _ALLOWED_SUFFIXES:
            shutil.copy2(src, config_dir / src.name)
            restored.append(src.name)

    if not restored:
        return {"error": f"Backup '{backup_dir.name}' contained no YAML files"}

    return {
        "backup_name": backup_dir.name,
        "pre_restore": pre_restore,
        "restored": restored,
    }

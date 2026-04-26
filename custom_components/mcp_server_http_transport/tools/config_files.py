"""Config file access tools (list, read, write, delete YAML files in config dir)."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from . import register_tool

_LOGGER = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = {".yaml", ".yml"}
_BLOCKED_NAMES = {"secrets.yaml", "secrets.yml"}


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
    config_dir = _config_dir(hass)
    try:
        files = sorted(
            entry.name
            for entry in config_dir.iterdir()
            if entry.is_file()
            and entry.suffix.lower() in _ALLOWED_SUFFIXES
            and entry.name.lower() not in _BLOCKED_NAMES
        )
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
        "Creates the file if it does not exist"
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
        },
        "required": ["filename", "content"],
    },
)
async def save_config_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Write a YAML config file, creating it if necessary."""
    try:
        path = _resolve_safe(hass, arguments["filename"])
        path.write_text(arguments["content"], encoding="utf-8")
        return {
            "content": [{"type": "text", "text": f"Successfully saved '{arguments['filename']}'"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error saving config file: {e}"}]}


@register_tool(
    name="delete_config_file",
    description=(
        "Delete a YAML configuration file from the Home Assistant config directory. "
        "First-level files only; secrets.yaml is blocked"
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
    """Delete a YAML config file."""
    try:
        path = _resolve_safe(hass, arguments["filename"])
        if not path.exists():
            return {
                "content": [
                    {"type": "text", "text": f"File '{arguments['filename']}' does not exist"}
                ]
            }
        path.unlink()
        return {
            "content": [{"type": "text", "text": f"Successfully deleted '{arguments['filename']}'"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting config file: {e}"}]}

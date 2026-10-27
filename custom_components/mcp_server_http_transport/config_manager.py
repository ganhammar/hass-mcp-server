"""YAML config CRUD helpers for automations, scenes, and scripts."""

import logging
import os
import shutil
import tempfile
import uuid
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import dumper as yaml_dumper
from homeassistant.util.yaml import loader as yaml_loader

_LOGGER = logging.getLogger(__name__)


def _load_yaml_list(path: str) -> list[dict[str, Any]]:
    """Load a YAML file as a list, returning empty list if missing."""
    if not os.path.isfile(path):
        return []
    data = yaml_loader.load_yaml(path)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return []


def _save_yaml(path: str, data: Any) -> None:
    """Write YAML to path atomically: the file is either the old content or the new."""
    directory = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory, delete=False
    ) as staged:
        temp = staged.name
    try:
        yaml_dumper.save_yaml(temp, data)
        if os.path.exists(path):
            shutil.copymode(path, temp)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _copy_aside(path: str) -> str:
    """Copy path to a sibling temp file and return the copy's path."""
    with tempfile.NamedTemporaryFile(
        prefix=f".{os.path.basename(path)}.backup_", dir=os.path.dirname(path), delete=False
    ) as backup:
        backup_path = backup.name
    try:
        shutil.copy2(path, backup_path)
    except Exception:
        os.unlink(backup_path)
        raise
    return backup_path


def _load_yaml_dict(path: str) -> dict[str, Any]:
    """Load a YAML file as a dict, returning empty dict if missing."""
    if not os.path.isfile(path):
        return {}
    data = yaml_loader.load_yaml(path)
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    return {}


# --- List-based CRUD (automations, scenes) ---


async def create_list_entry(
    hass: HomeAssistant,
    config_file: str,
    entry: dict[str, Any],
    reload_domain: str,
) -> str:
    """Create a new entry in a list-based YAML config."""
    path = hass.config.path(config_file)
    entry_id = str(uuid.uuid4())
    entry["id"] = entry_id

    def _write():
        current = _load_yaml_list(path)
        current.append(entry)
        _save_yaml(path, current)

    await hass.async_add_executor_job(_write)
    await hass.services.async_call(reload_domain, "reload", blocking=True)
    return entry_id


async def update_list_entry(
    hass: HomeAssistant,
    config_file: str,
    entry_id: str,
    entry: dict[str, Any],
    reload_domain: str,
    *,
    expected: dict[str, Any] | None = None,
    backup: bool = False,
    reload: bool = True,
) -> str | None:
    """Update an existing entry in a list-based YAML config.

    With ``expected``, the write is refused when the stored entry no longer equals
    it, so a caller that read the entry earlier cannot overwrite a concurrent
    change. With ``backup``, the file is copied aside before the write and the
    copy's path is returned; the caller owns that copy.
    """
    path = hass.config.path(config_file)
    entry["id"] = entry_id

    def _write() -> tuple[bool, str | None]:
        current = _load_yaml_list(path)
        for i, item in enumerate(current):
            if item.get("id") == entry_id:
                if expected is not None and item != expected:
                    raise ValueError(
                        f"Entry '{entry_id}' in {config_file} changed since it was read; "
                        "refusing to overwrite"
                    )
                backup_path = _copy_aside(path) if backup else None
                current[i] = entry
                try:
                    _save_yaml(path, current)
                except Exception:
                    if backup_path is not None:
                        os.unlink(backup_path)
                    raise
                return True, backup_path
        return False, None

    found, backup_path = await hass.async_add_executor_job(_write)
    if not found:
        raise ValueError(f"Entry with id '{entry_id}' not found in {config_file}")
    if reload:
        await hass.services.async_call(reload_domain, "reload", blocking=True)
    return backup_path


async def delete_list_entry(
    hass: HomeAssistant,
    config_file: str,
    entry_id: str,
    reload_domain: str,
) -> None:
    """Delete an entry from a list-based YAML config."""
    path = hass.config.path(config_file)

    def _write():
        current = _load_yaml_list(path)
        original_len = len(current)
        current = [item for item in current if item.get("id") != entry_id]
        if len(current) == original_len:
            return False
        _save_yaml(path, current)
        return True

    found = await hass.async_add_executor_job(_write)
    if not found:
        raise ValueError(f"Entry with id '{entry_id}' not found in {config_file}")
    await hass.services.async_call(reload_domain, "reload", blocking=True)


# --- Dict-based CRUD (scripts) ---


async def create_dict_entry(
    hass: HomeAssistant,
    config_file: str,
    key: str,
    config: dict[str, Any],
    reload_domain: str,
) -> str:
    """Create a new entry in a dict-based YAML config."""
    path = hass.config.path(config_file)

    def _write():
        current = _load_yaml_dict(path)
        if key in current:
            raise ValueError(f"Entry '{key}' already exists in {config_file}")
        current[key] = config
        _save_yaml(path, current)

    await hass.async_add_executor_job(_write)
    await hass.services.async_call(reload_domain, "reload", blocking=True)
    return key


async def update_dict_entry(
    hass: HomeAssistant,
    config_file: str,
    key: str,
    config: dict[str, Any],
    reload_domain: str,
) -> None:
    """Update an existing entry in a dict-based YAML config."""
    path = hass.config.path(config_file)

    def _write():
        current = _load_yaml_dict(path)
        if key not in current:
            raise ValueError(f"Entry '{key}' not found in {config_file}")
        current[key] = config
        _save_yaml(path, current)

    await hass.async_add_executor_job(_write)
    await hass.services.async_call(reload_domain, "reload", blocking=True)


async def delete_dict_entry(
    hass: HomeAssistant,
    config_file: str,
    key: str,
    reload_domain: str,
) -> None:
    """Delete an entry from a dict-based YAML config."""
    path = hass.config.path(config_file)

    def _write():
        current = _load_yaml_dict(path)
        if key not in current:
            raise ValueError(f"Entry '{key}' not found in {config_file}")
        del current[key]
        _save_yaml(path, current)

    await hass.async_add_executor_job(_write)
    await hass.services.async_call(reload_domain, "reload", blocking=True)


# --- Read helpers ---


async def read_list_entries(
    hass: HomeAssistant,
    config_file: str,
) -> list[dict[str, Any]]:
    """Read all entries from a list-based YAML config."""
    path = hass.config.path(config_file)
    return await hass.async_add_executor_job(_load_yaml_list, path)


async def read_list_entry(
    hass: HomeAssistant,
    config_file: str,
    entry_id: str,
) -> dict[str, Any]:
    """Read a single entry from a list-based YAML config by ID."""
    path = hass.config.path(config_file)
    entries = await hass.async_add_executor_job(_load_yaml_list, path)
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    raise ValueError(f"Entry with id '{entry_id}' not found in {config_file}")


async def read_dict_entries(
    hass: HomeAssistant,
    config_file: str,
) -> dict[str, Any]:
    """Read all entries from a dict-based YAML config."""
    path = hass.config.path(config_file)
    return await hass.async_add_executor_job(_load_yaml_dict, path)


async def read_dict_entry(
    hass: HomeAssistant,
    config_file: str,
    key: str,
) -> dict[str, Any]:
    """Read a single entry from a dict-based YAML config by key."""
    path = hass.config.path(config_file)
    entries = await hass.async_add_executor_job(_load_yaml_dict, path)
    if key not in entries:
        raise ValueError(f"Entry '{key}' not found in {config_file}")
    return entries[key]

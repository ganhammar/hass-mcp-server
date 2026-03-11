"""YAML config CRUD helpers for automations, scenes, and scripts."""

import logging
import os
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
        yaml_dumper.save_yaml(path, current)

    await hass.async_add_executor_job(_write)
    await hass.services.async_call(reload_domain, "reload", blocking=True)
    return entry_id


async def update_list_entry(
    hass: HomeAssistant,
    config_file: str,
    entry_id: str,
    entry: dict[str, Any],
    reload_domain: str,
) -> None:
    """Update an existing entry in a list-based YAML config."""
    path = hass.config.path(config_file)
    entry["id"] = entry_id

    def _write():
        current = _load_yaml_list(path)
        for i, item in enumerate(current):
            if item.get("id") == entry_id:
                current[i] = entry
                yaml_dumper.save_yaml(path, current)
                return True
        return False

    found = await hass.async_add_executor_job(_write)
    if not found:
        raise ValueError(f"Entry with id '{entry_id}' not found in {config_file}")
    await hass.services.async_call(reload_domain, "reload", blocking=True)


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
        yaml_dumper.save_yaml(path, current)
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
        yaml_dumper.save_yaml(path, current)

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
        yaml_dumper.save_yaml(path, current)

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
        yaml_dumper.save_yaml(path, current)

    await hass.async_add_executor_job(_write)
    await hass.services.async_call(reload_domain, "reload", blocking=True)

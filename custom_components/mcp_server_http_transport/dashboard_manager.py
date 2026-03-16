"""Lovelace dashboard management helpers."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _resolve_url_path(url_path: str) -> str | None:
    """Map the public url_path value to the internal key.

    Home Assistant uses ``None`` as the key for the default (overview)
    dashboard.  We expose it as the string ``"default"`` in the tool
    interface so that callers always pass a string.
    """
    if url_path == "default":
        return None
    return url_path


async def list_dashboards(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return metadata for every registered Lovelace dashboard."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    dashboards = hass.data[LOVELACE_DATA].dashboards
    result: list[dict[str, Any]] = []
    for key, dashboard in dashboards.items():
        config = getattr(dashboard, "config", None)
        entry: dict[str, Any] = {
            "url_path": "default" if key is None else key,
            "mode": config.get("mode", "storage") if isinstance(config, dict) else "storage",
        }
        if isinstance(config, dict):
            entry["title"] = config.get("title", "")
            entry["icon"] = config.get("icon", "")
            entry["show_in_sidebar"] = config.get("show_in_sidebar", True)
            entry["require_admin"] = config.get("require_admin", False)
        result.append(entry)
    return result


async def get_dashboard_config(hass: HomeAssistant, url_path: str) -> dict[str, Any]:
    """Load and return the full dashboard configuration (views/cards)."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    key = _resolve_url_path(url_path)
    dashboards = hass.data[LOVELACE_DATA].dashboards

    if key not in dashboards:
        raise ValueError(f"Dashboard '{url_path}' not found")

    dashboard = dashboards[key]
    try:
        config = await dashboard.async_load(force=False)
    except Exception as exc:
        raise ValueError(f"Failed to load config for dashboard '{url_path}': {exc}") from exc

    return config if config is not None else {}


async def save_dashboard_config(hass: HomeAssistant, url_path: str, config: dict[str, Any]) -> None:
    """Save (replace) the full dashboard configuration."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    key = _resolve_url_path(url_path)
    dashboards = hass.data[LOVELACE_DATA].dashboards

    if key not in dashboards:
        raise ValueError(f"Dashboard '{url_path}' not found")

    dashboard = dashboards[key]
    try:
        await dashboard.async_save(config)
    except Exception as exc:
        raise ValueError(f"Failed to save config for dashboard '{url_path}': {exc}") from exc


async def delete_dashboard_config(hass: HomeAssistant, url_path: str) -> None:
    """Delete (reset) the dashboard configuration to empty."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    key = _resolve_url_path(url_path)
    dashboards = hass.data[LOVELACE_DATA].dashboards

    if key not in dashboards:
        raise ValueError(f"Dashboard '{url_path}' not found")

    dashboard = dashboards[key]
    try:
        await dashboard.async_delete()
    except Exception as exc:
        raise ValueError(f"Failed to delete config for dashboard '{url_path}': {exc}") from exc


async def create_dashboard(
    hass: HomeAssistant,
    url_path: str,
    title: str,
    icon: str | None = None,
    require_admin: bool = False,
    show_in_sidebar: bool = True,
) -> dict[str, Any]:
    """Create a new Lovelace dashboard (experimental).

    This instantiates a ``DashboardsCollection``, creates the entry, and
    manually replicates the side effects that HA's ``lovelace.async_setup``
    normally wires up (panel registration, dashboards dict update).
    """
    from homeassistant.components import frontend
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import (
        DashboardsCollection,
        LovelaceStorage,
    )

    if url_path == "default":
        raise ValueError("Cannot create the default dashboard")

    collection = DashboardsCollection(hass)
    await collection.async_load()

    data: dict[str, Any] = {
        "url_path": url_path,
        "title": title,
        "require_admin": require_admin,
        "show_in_sidebar": show_in_sidebar,
    }
    if icon is not None:
        data["icon"] = icon

    item = await collection.async_create_item(data)

    # Replicate side effects
    dashboard_obj = LovelaceStorage(hass, item)
    hass.data[LOVELACE_DATA].dashboards[url_path] = dashboard_obj

    try:
        frontend.async_register_built_in_panel(
            hass,
            "lovelace",
            url_path=url_path,
            require_admin=require_admin,
            sidebar_title=title,
            sidebar_icon=icon or "mdi:view-dashboard",
            config={"url_path": url_path},
        )
    except Exception:
        _LOGGER.debug("Panel registration for '%s' (may already exist)", url_path)

    return dict(item)


async def update_dashboard(
    hass: HomeAssistant,
    url_path: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a dashboard's metadata (experimental)."""
    from homeassistant.components import frontend
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import DashboardsCollection

    if url_path == "default":
        raise ValueError("Cannot update the default dashboard")

    collection = DashboardsCollection(hass)
    await collection.async_load()

    # Find the item id for the given url_path
    item_id = None
    for existing_id, existing_item in collection.data.items():
        if existing_item.get("url_path") == url_path:
            item_id = existing_id
            break

    if item_id is None:
        raise ValueError(f"Dashboard '{url_path}' not found in collection")

    item = await collection.async_update_item(item_id, fields)

    # Update the config on the dashboard object
    if url_path in hass.data[LOVELACE_DATA].dashboards:
        hass.data[LOVELACE_DATA].dashboards[url_path].config = item

    # Re-register panel
    try:
        frontend.async_register_built_in_panel(
            hass,
            "lovelace",
            url_path=url_path,
            require_admin=item.get("require_admin", False),
            sidebar_title=item.get("title", ""),
            sidebar_icon=item.get("icon", "mdi:view-dashboard"),
            config={"url_path": url_path},
            update=True,
        )
    except Exception:
        _LOGGER.debug("Panel re-registration for '%s' (may not exist)", url_path)

    return dict(item)


async def delete_dashboard(hass: HomeAssistant, url_path: str) -> None:
    """Delete a dashboard and its stored configuration (experimental)."""
    from homeassistant.components import frontend
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import DashboardsCollection

    if url_path == "default":
        raise ValueError("Cannot delete the default dashboard")

    collection = DashboardsCollection(hass)
    await collection.async_load()

    # Find the item id for the given url_path
    item_id = None
    for existing_id, existing_item in collection.data.items():
        if existing_item.get("url_path") == url_path:
            item_id = existing_id
            break

    if item_id is None:
        raise ValueError(f"Dashboard '{url_path}' not found in collection")

    await collection.async_delete_item(item_id)

    # Remove the panel
    try:
        frontend.async_remove_panel(hass, url_path)
    except Exception:
        _LOGGER.debug("Panel removal for '%s' (may not exist)", url_path)

    # Clean up the dashboard object and its stored config
    dashboard_obj = hass.data[LOVELACE_DATA].dashboards.pop(url_path, None)
    if dashboard_obj is not None:
        try:
            await dashboard_obj.async_delete()
        except Exception:
            _LOGGER.debug("Dashboard config deletion for '%s' failed", url_path)

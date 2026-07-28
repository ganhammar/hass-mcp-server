"""Lovelace dashboard management helpers."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .json_patch import apply_patch, format_pointer

_LOGGER = logging.getLogger(__name__)

# Keys a card may use for its human-readable label, in the order they win.
_CARD_LABEL_KEYS = ("title", "name", "heading")

# Nested cards (stacks, grids) are summarized too, but a hand-written config can
# nest arbitrarily deep and a summary that follows it loses its point.
_MAX_CARD_DEPTH = 6

# An entities card can list dozens of rows; the first few are enough to identify
# the card, and `entity_count` carries the rest of the information.
_MAX_SUMMARY_ENTITIES = 10


def _register_panel(
    hass: HomeAssistant,
    url_path: str,
    config: dict[str, Any],
    update: bool = False,
) -> None:
    """Register a Lovelace panel, mirroring HA's internal _register_panel."""
    from homeassistant.components import frontend

    kwargs: dict[str, Any] = {
        "frontend_url_path": url_path,
        "require_admin": config.get("require_admin", False),
        "config": {"mode": "storage"},
        "update": update,
    }

    if config.get("show_in_sidebar", True):
        kwargs["sidebar_title"] = config.get("title", "")
        kwargs["sidebar_icon"] = config.get("icon", "mdi:view-dashboard")

    try:
        frontend.async_register_built_in_panel(hass, "lovelace", **kwargs)
    except Exception:
        _LOGGER.debug("Panel registration for '%s' failed", url_path)


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


async def patch_dashboard_config(
    hass: HomeAssistant, url_path: str, operations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply JSON Patch operations to a dashboard config and save the result.

    The patch runs against a copy of the loaded config, so a failing operation
    leaves the stored dashboard untouched.
    """
    config = await get_dashboard_config(hass, url_path)
    patched = apply_patch(config, operations)

    # A dashboard config is an object, and its `views` — when it has any — is a
    # list. Anything else renders as a broken dashboard, so it is caught here
    # rather than stored. `views` may legitimately be absent: that is what an
    # empty dashboard looks like before its first view is added.
    if not isinstance(patched, dict):
        raise ValueError(
            "The patched config must be an object with a 'views' list, "
            f"but the operations produced a {type(patched).__name__}"
        )
    if "views" in patched and not isinstance(patched["views"], list):
        raise ValueError(
            "The patched config's 'views' must be a list, but the operations "
            f"produced a {type(patched['views']).__name__}"
        )

    await save_dashboard_config(hass, url_path, patched)
    return patched


def _extract_entity_ids(value: Any) -> list[str]:
    """Pull entity ids out of a card's `entities` value (strings or row objects)."""
    if not isinstance(value, list):
        return []
    entities: list[str] = []
    for row in value:
        if isinstance(row, str):
            entities.append(row)
        elif isinstance(row, dict) and isinstance(row.get("entity"), str):
            entities.append(row["entity"])
    return entities


def summarize_card(card: Any, pointer_tokens: list[str], index: int, depth: int = 0) -> dict:
    """Return an identifying outline of a card: what it is, not how it is configured."""
    entry: dict[str, Any] = {"pointer": format_pointer(pointer_tokens), "index": index}

    if not isinstance(card, dict):
        entry["value"] = card
        return entry

    if isinstance(card.get("type"), str):
        entry["type"] = card["type"]
    for key in _CARD_LABEL_KEYS:
        label = card.get(key)
        if isinstance(label, str) and label:
            entry["label"] = label
            break
    if isinstance(card.get("entity"), str):
        entry["entity"] = card["entity"]

    entities = _extract_entity_ids(card.get("entities"))
    if entities:
        entry["entity_count"] = len(entities)
        entry["entities"] = entities[:_MAX_SUMMARY_ENTITIES]

    nested = card.get("cards")
    if isinstance(nested, list) and nested:
        entry["card_count"] = len(nested)
        if depth >= _MAX_CARD_DEPTH:
            entry["truncated"] = True
        else:
            entry["cards"] = [
                summarize_card(child, pointer_tokens + ["cards", str(i)], i, depth + 1)
                for i, child in enumerate(nested)
            ]

    return entry


def _summarize_card_list(cards: list, pointer_tokens: list[str]) -> list[dict]:
    """Summarize a `cards` / `badges` list."""
    return [summarize_card(card, pointer_tokens + [str(i)], i) for i, card in enumerate(cards)]


def summarize_view(view: Any, pointer_tokens: list[str], index: int) -> dict:
    """Return an outline of a single view and the cards it holds."""
    entry: dict[str, Any] = {"pointer": format_pointer(pointer_tokens), "index": index}

    if not isinstance(view, dict):
        entry["value"] = view
        return entry

    for key in ("title", "path", "icon", "type"):
        value = view.get(key)
        if isinstance(value, str) and value:
            entry[key] = value

    badges = view.get("badges")
    if isinstance(badges, list) and badges:
        entry["badge_count"] = len(badges)
        entry["badges"] = _summarize_card_list(badges, pointer_tokens + ["badges"])

    cards = view.get("cards")
    if isinstance(cards, list):
        entry["card_count"] = len(cards)
        entry["cards"] = _summarize_card_list(cards, pointer_tokens + ["cards"])

    sections = view.get("sections")
    if isinstance(sections, list):
        entry["section_count"] = len(sections)
        entry["sections"] = [
            _summarize_section(section, pointer_tokens + ["sections", str(i)], i)
            for i, section in enumerate(sections)
        ]

    return entry


def _summarize_section(section: Any, pointer_tokens: list[str], index: int) -> dict:
    """Return an outline of one section of a sections-type view."""
    entry: dict[str, Any] = {"pointer": format_pointer(pointer_tokens), "index": index}

    if not isinstance(section, dict):
        entry["value"] = section
        return entry

    for key in ("type", "title"):
        value = section.get(key)
        if isinstance(value, str) and value:
            entry[key] = value

    cards = section.get("cards")
    if isinstance(cards, list):
        entry["card_count"] = len(cards)
        entry["cards"] = _summarize_card_list(cards, pointer_tokens + ["cards"])

    return entry


def summarize_dashboard_config(config: Any) -> dict[str, Any]:
    """Return an outline of every view in a dashboard config.

    Each entry carries the JSON Pointer of the thing it describes, so a caller
    can read one card with get_dashboard_config(path=...) or edit it with
    patch_dashboard_config without ever loading the whole config.
    """
    if not isinstance(config, dict):
        raise ValueError(f"Dashboard config must be an object, got {type(config).__name__}")

    views = config.get("views")
    if not isinstance(views, list):
        return {"views": [], "view_count": 0}

    return {
        "view_count": len(views),
        "views": [summarize_view(view, ["views", str(i)], i) for i, view in enumerate(views)],
    }


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

    _register_panel(hass, url_path, item)

    return dict(item)


async def update_dashboard(
    hass: HomeAssistant,
    url_path: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a dashboard's metadata (experimental)."""
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

    _register_panel(hass, url_path, item, update=True)

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

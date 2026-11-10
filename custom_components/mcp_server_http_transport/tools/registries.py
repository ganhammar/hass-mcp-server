"""Area, floor and device registry tools. Listing areas and devices is in entities.py.

These write the same registries the frontend does over the websocket API. Deleting
an area or a floor needs no cleanup here: the entity and device registries listen
for an area's removal and clear it from what was in it, and the area registry does
the same for a floor.

The registries store whatever ID they are handed, so the tools check that a floor,
area or label exists before assigning it.
"""

from typing import Any

import voluptuous as vol
from homeassistant import loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    dumps,
    register_tool,
)

ICON_PROPERTY = {
    "type": "string",
    "description": "Material Design icon in the form 'mdi:name'; an empty string clears it",
}
ALIASES_PROPERTY = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Alternative names voice assistants accept. Replaces the whole list; an empty "
        "list clears it"
    ),
}
LABELS_PROPERTY = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Label IDs to carry (see list_labels). Replaces the whole list; an empty list "
        "removes every label"
    ),
}
AREA_ID_PROPERTY = {
    "type": "string",
    "description": "ID of the area to assign (see list_areas); an empty string clears it",
}
FLOOR_ID_PROPERTY = {
    "type": "string",
    "description": "ID of the floor the area is on (see list_floors); an empty string clears it",
}
LEVEL_PROPERTY = {
    "type": "integer",
    "description": "Floor number, where 0 is the ground floor and basements are negative",
}


def area_json(area: ar.AreaEntry) -> dict[str, Any]:
    """The fields list_areas and hass://areas report for an area."""
    return {
        "id": area.id,
        "name": area.name,
        "floor_id": area.floor_id,
        "icon": area.icon,
        "labels": sorted(area.labels),
        "aliases": sorted(area.aliases),
    }


def floor_json(floor: fr.FloorEntry) -> dict[str, Any]:
    """The fields list_floors and hass://floors report for a floor."""
    return {
        "floor_id": floor.floor_id,
        "name": floor.name,
        "icon": floor.icon,
        "level": floor.level,
        "aliases": sorted(floor.aliases),
    }


def device_json(device: dr.DeviceEntry) -> dict[str, Any]:
    """What update_device reports: the list_devices fields and the ones it can change."""
    return {
        "id": device.id,
        "name": device.name,
        "name_by_user": device.name_by_user,
        "area_id": device.area_id,
        "labels": sorted(device.labels),
        "disabled_by": device.disabled_by,
    }


def string_argument(arguments: dict[str, Any], field: str) -> str | None:
    """Return a string argument, None if absent or null, and refuse any other type.

    call_tool checks that required arguments are present, not what type they are.
    """
    value = arguments.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string, not {type(value).__name__}")
    return value


def boolean_argument(arguments: dict[str, Any], field: str) -> bool | None:
    """Return a boolean argument, None if absent or null, and refuse any other type."""
    value = arguments.get(field)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"'{field}' must be true or false, not {type(value).__name__}")
    return value


def string_set_argument(arguments: dict[str, Any], field: str) -> set[str] | None:
    """Return a list-of-strings argument as a set of its non-blank, trimmed items."""
    value = arguments.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{field}' must be a list of strings")
    return {item.strip() for item in value if item.strip()}


def labels_argument(hass: HomeAssistant, arguments: dict[str, Any]) -> set[str] | None:
    """Return the label IDs given, refusing any the label registry does not have."""
    labels = string_set_argument(arguments, "labels")
    if labels is None:
        return None
    registry = lr.async_get(hass)
    unknown = sorted(label for label in labels if registry.async_get_label(label) is None)
    if unknown:
        raise ValueError(f"No label with label_id {', '.join(unknown)} (see list_labels)")
    return labels


def area_id_argument(hass: HomeAssistant, arguments: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether an area was given and its ID, with an empty string meaning none."""
    value = string_argument(arguments, "area_id")
    if value is None:
        return False, None
    if value and ar.async_get(hass).async_get_area(value) is None:
        raise ValueError(f"No area with area_id '{value}' (see list_areas)")
    return True, value or None


def _floor_id(hass: HomeAssistant, value: str) -> str | None:
    if value and fr.async_get(hass).async_get_floor(value) is None:
        raise ValueError(f"No floor with floor_id '{value}' (see list_floors)")
    return value or None


def _icon(value: str) -> str | None:
    if not value:
        return None
    try:
        return cv.icon(value)
    except vol.Invalid as err:
        raise ValueError(f"'{value}' is not a valid icon: {err}") from None


def _name(arguments: dict[str, Any], what: str) -> str | None:
    """Return the trimmed name, None if absent, and refuse a blank one."""
    name = string_argument(arguments, "name")
    if name is None:
        return None
    if not name.strip():
        raise ValueError(f"{what} needs a name")
    return name.strip()


def _level(arguments: dict[str, Any]) -> int | None:
    level = arguments.get("level")
    if level is not None and type(level) is not int:
        raise ValueError(f"'level' must be an integer, not {type(level).__name__}")
    return level


def _area_changes(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the optional area fields given, validated, keyed as the registry takes them."""
    changes: dict[str, Any] = {}
    if (floor_id := string_argument(arguments, "floor_id")) is not None:
        changes["floor_id"] = _floor_id(hass, floor_id)
    if (icon := string_argument(arguments, "icon")) is not None:
        changes["icon"] = _icon(icon)
    if (labels := labels_argument(hass, arguments)) is not None:
        changes["labels"] = labels
    if (aliases := string_set_argument(arguments, "aliases")) is not None:
        changes["aliases"] = aliases
    return changes


def _floor_changes(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the optional floor fields given, validated, keyed as the registry takes them."""
    changes: dict[str, Any] = {}
    if (level := _level(arguments)) is not None:
        changes["level"] = level
    if (icon := string_argument(arguments, "icon")) is not None:
        changes["icon"] = _icon(icon)
    if (aliases := string_set_argument(arguments, "aliases")) is not None:
        changes["aliases"] = aliases
    return changes


def _device(registry: dr.DeviceRegistry, device_id: str) -> tuple[Any, Any]:
    """Return a device and the registry method that updates it.

    Registries that know child devices update them through a method of their own.
    They also resolve the ID of a device that has since been split into several,
    and an update to that one would reach every device it was split into, so it
    is refused.
    """
    update_child = getattr(registry, "async_update_child_device", None)
    if update_child is None:
        device = registry.async_get(device_id)
        update = registry.async_update_device
    else:
        if registry.async_get(device_id, include_main_devices=False, include_child_devices=False):
            raise ValueError(
                f"'{device_id}' is the old ID of a device that was split into several. "
                "Use the ID of one of them (see list_devices)"
            )
        device = registry.async_get(device_id, include_composite_devices=False)
        child = isinstance(device, dr.ChildDeviceEntry)
        update = update_child if child else registry.async_update_device
    if device is None:
        raise ValueError(f"No device with device_id '{device_id}' (see list_devices)")
    return device, update


def _count(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _error(action: str, exc: Exception) -> dict[str, Any]:
    return _text(f"Error {action}: {str(exc)}")


@register_tool(
    name="create_area",
    description=(
        "Create an area, a room or zone that devices and entities are assigned to. "
        "Area names must be unique"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the new area"},
            "floor_id": FLOOR_ID_PROPERTY,
            "icon": ICON_PROPERTY,
            "labels": LABELS_PROPERTY,
            "aliases": ALIASES_PROPERTY,
        },
        "required": ["name"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def create_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create an area."""
    try:
        area = ar.async_get(hass).async_create(
            _name(arguments, "An area"), **_area_changes(hass, arguments)
        )
    except Exception as e:
        return _error("creating area", e)
    return _text(f"Created area {dumps(area_json(area))}")


@register_tool(
    name="update_area",
    description=(
        "Update an area's name, floor, icon, labels or aliases. Only the fields given "
        "are changed. The area_id stays the same when the area is renamed"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "area_id": {
                "type": "string",
                "description": "ID of the area to update (see list_areas)",
            },
            "name": {"type": "string", "description": "New name; must not be in use"},
            "floor_id": FLOOR_ID_PROPERTY,
            "icon": ICON_PROPERTY,
            "labels": LABELS_PROPERTY,
            "aliases": ALIASES_PROPERTY,
        },
        "required": ["area_id"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def update_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update the fields of an area that were given."""
    try:
        registry = ar.async_get(hass)
        area_id = arguments["area_id"]
        if registry.async_get_area(area_id) is None:
            raise ValueError(f"No area with area_id '{area_id}' (see list_areas)")

        changes = _area_changes(hass, arguments)
        if (name := _name(arguments, "An area")) is not None:
            changes["name"] = name
        if not changes:
            raise ValueError("Nothing to update: give a name, floor_id, icon, labels or aliases")
        area = registry.async_update(area_id, **changes)
    except Exception as e:
        return _error("updating area", e)
    return _text(f"Updated area {dumps(area_json(area))}")


@register_tool(
    name="delete_area",
    description=(
        "Delete an area. Devices and entities assigned to it are kept and left without "
        "an area. This cannot be undone"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "area_id": {
                "type": "string",
                "description": "ID of the area to delete (see list_areas)",
            },
        },
        "required": ["area_id"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_area(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete an area, reporting what was assigned to it."""
    try:
        registry = ar.async_get(hass)
        area_id = arguments["area_id"]
        area = registry.async_get_area(area_id)
        if area is None:
            raise ValueError(f"No area with area_id '{area_id}' (see list_areas)")

        devices = len(dr.async_entries_for_area(dr.async_get(hass), area_id))
        entities = sum(e.area_id == area_id for e in er.async_get(hass).entities.values())
        registry.async_delete(area_id)
    except Exception as e:
        return _error("deleting area", e)

    left = [
        _count(count, singular, plural)
        for count, singular, plural in (
            (devices, "device", "devices"),
            (entities, "entity", "entities"),
        )
        if count
    ]
    suffix = f", leaving {' and '.join(left)} without an area" if left else ""
    return _text(f"Deleted area '{area.name}' ({area_id}){suffix}")


@register_tool(
    name="list_floors",
    description="List all floors, which group areas by storey",
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_READ_ONLY,
)
async def list_floors(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all floors."""
    return _text(dumps([floor_json(floor) for floor in fr.async_get(hass).async_list_floors()]))


@register_tool(
    name="create_floor",
    description=(
        "Create a floor, which groups areas by storey. Floor names must be unique. "
        "Put an area on it with update_area"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the new floor"},
            "level": LEVEL_PROPERTY,
            "icon": ICON_PROPERTY,
            "aliases": ALIASES_PROPERTY,
        },
        "required": ["name"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def create_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a floor."""
    try:
        floor = fr.async_get(hass).async_create(
            _name(arguments, "A floor"), **_floor_changes(arguments)
        )
    except Exception as e:
        return _error("creating floor", e)
    return _text(f"Created floor {dumps(floor_json(floor))}")


@register_tool(
    name="update_floor",
    description=(
        "Update a floor's name, level, icon or aliases. Only the fields given are "
        "changed. The floor_id stays the same when the floor is renamed"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "floor_id": {
                "type": "string",
                "description": "ID of the floor to update (see list_floors)",
            },
            "name": {"type": "string", "description": "New name; must not be in use"},
            "level": LEVEL_PROPERTY,
            "icon": ICON_PROPERTY,
            "aliases": ALIASES_PROPERTY,
        },
        "required": ["floor_id"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def update_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update the fields of a floor that were given."""
    try:
        registry = fr.async_get(hass)
        floor_id = arguments["floor_id"]
        if registry.async_get_floor(floor_id) is None:
            raise ValueError(f"No floor with floor_id '{floor_id}' (see list_floors)")

        changes = _floor_changes(arguments)
        if (name := _name(arguments, "A floor")) is not None:
            changes["name"] = name
        if not changes:
            raise ValueError("Nothing to update: give a name, level, icon or aliases")
        floor = registry.async_update(floor_id, **changes)
    except Exception as e:
        return _error("updating floor", e)
    return _text(f"Updated floor {dumps(floor_json(floor))}")


@register_tool(
    name="delete_floor",
    description=(
        "Delete a floor. Areas on it are kept and left without a floor. This cannot be undone"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "floor_id": {
                "type": "string",
                "description": "ID of the floor to delete (see list_floors)",
            },
        },
        "required": ["floor_id"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_floor(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a floor, reporting how many areas were on it."""
    try:
        registry = fr.async_get(hass)
        floor_id = arguments["floor_id"]
        floor = registry.async_get_floor(floor_id)
        if floor is None:
            raise ValueError(f"No floor with floor_id '{floor_id}' (see list_floors)")

        areas = sum(a.floor_id == floor_id for a in ar.async_get(hass).areas.values())
        registry.async_delete(floor_id)
    except Exception as e:
        return _error("deleting floor", e)

    suffix = f", leaving {_count(areas, 'area', 'areas')} without a floor" if areas else ""
    return _text(f"Deleted floor '{floor.name}' ({floor_id}){suffix}")


@register_tool(
    name="update_device",
    description=(
        "Rename a device, move it to an area, set its labels, or disable or enable it. "
        "Only the fields given are changed. Disabling a device also disables its "
        "entities, and they come back when it is enabled again"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "ID of the device to update (see list_devices)",
            },
            "name": {
                "type": "string",
                "description": (
                    "Name to show for the device; an empty string restores the name its "
                    "integration gave it"
                ),
            },
            "area_id": AREA_ID_PROPERTY,
            "labels": LABELS_PROPERTY,
            "disabled": {
                "type": "boolean",
                "description": "True disables the device, false enables it",
            },
        },
        "required": ["device_id"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def update_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update the fields of a device that were given."""
    try:
        registry = dr.async_get(hass)
        device_id = arguments["device_id"]
        device, update = _device(registry, device_id)

        changes: dict[str, Any] = {}
        if (name := string_argument(arguments, "name")) is not None:
            changes["name_by_user"] = name.strip() or None
        given, area_id = area_id_argument(hass, arguments)
        if given:
            changes["area_id"] = area_id
        if (labels := labels_argument(hass, arguments)) is not None:
            changes["labels"] = labels
        # A device something else disabled stays attributed to that; only the
        # user's own disabling is set here, as in the frontend.
        if (disabled := boolean_argument(arguments, "disabled")) is not None:
            if disabled != device.disabled:
                changes["disabled_by"] = dr.DeviceEntryDisabler.USER if disabled else None
            elif not changes:
                state = "disabled" if disabled else "enabled"
                return _text(f"Device is already {state}: {dumps(device_json(device))}")

        if not changes:
            raise ValueError("Nothing to update: give a name, area_id, labels or disabled")
        device = update(device_id, **changes) or device
    except Exception as e:
        return _error("updating device", e)
    return _text(f"Updated device {dumps(device_json(device))}")


def _config_entry_ids(device: Any) -> list[str]:
    """Return the config entries a device belongs to: one, or several on older cores."""
    if (entry_id := getattr(device, "config_entry_id", None)) is not None:
        return [entry_id]
    return sorted(device.config_entries)


async def _integration_lets_go(hass: HomeAssistant, config_entry: Any, device: Any) -> bool:
    """Ask the integration behind a config entry whether its device may be removed."""
    try:
        integration = await loader.async_get_integration(hass, config_entry.domain)
        component = await integration.async_get_component()
    except (ImportError, loader.IntegrationNotFound):
        raise ValueError(f"The integration '{config_entry.domain}' could not be loaded") from None
    return await component.async_remove_config_entry_device(hass, config_entry, device)


def _already_detached(domains: list[str]) -> str:
    """Say which integrations had let go of a shared device before a delete stopped."""
    if not domains:
        return ""
    return (
        f". The device is still there, but {', '.join(domains)} had already let go of it "
        "and its entities from there are gone"
    )


@register_tool(
    name="delete_device",
    description=(
        "Delete a device from Home Assistant together with its entities and any child "
        "devices, for a device that is gone or was replaced. Only integrations that "
        "support removing a device allow it, and what happens to a device that is still "
        "connected is up to the integration: some refuse, some let Home Assistant add it "
        "again, and some remove it from their network for real (Matter unpairs the "
        "device). This cannot be undone. Requires explicit confirmation"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "ID of the device to delete (see list_devices)",
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true to confirm deleting the device and its entities",
            },
        },
        "required": ["device_id", "confirm"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a device the way the frontend does, with its integrations' consent."""
    if arguments["confirm"] is not True:
        return _text(
            "Deleting a device requires confirm=true. It removes the device together with "
            "its entities."
        )
    try:
        registry = dr.async_get(hass)
        device_id = arguments["device_id"]
        device, _ = _device(registry, device_id)

        config_entries = []
        for entry_id in _config_entry_ids(device):
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry is None:
                raise ValueError(f"The device's config entry '{entry_id}' no longer exists")
            if not config_entry.supports_remove_device:
                raise ValueError(
                    f"The integration '{config_entry.domain}' does not support deleting a "
                    "device. Remove it in the integration itself, or delete the whole "
                    "config entry with delete_integration"
                )
            config_entries.append(config_entry)

        # Removing a device takes its child devices with it, on cores that have them.
        children = [
            child.id
            for child in getattr(registry, "child_devices", ())
            if child.parent_device_id == device_id
        ]
        entity_registry = er.async_get(hass)
        entities = sum(
            len(er.async_entries_for_device(entity_registry, each, include_disabled_entities=True))
            for each in (device_id, *children)
        )
        # A device several integrations share is let go of one at a time, and is
        # gone once the last has. Each may have removed it already.
        detached: list[str] = []
        for position, config_entry in enumerate(config_entries, start=1):
            try:
                let_go = await _integration_lets_go(hass, config_entry, device)
            except Exception as err:
                raise ValueError(f"{err}{_already_detached(detached)}") from err
            if not let_go:
                raise ValueError(
                    f"The integration '{config_entry.domain}' refused to remove the device, "
                    f"usually because it is still connected{_already_detached(detached)}"
                )
            if registry.async_get(device_id) is None:
                break
            if position < len(config_entries):
                registry.async_update_device(
                    device_id, remove_config_entry_id=config_entry.entry_id
                )
                detached.append(config_entry.domain)
            else:
                registry.async_remove_device(device_id)
    except Exception as e:
        return _error("deleting device", e)

    name = device.name_by_user or device.name
    gone = [
        _count(count, singular, plural)
        for count, singular, plural in (
            (len(children), "child device", "child devices"),
            (entities, "entity", "entities"),
        )
        if count
    ]
    suffix = f", with {' and '.join(gone)}" if gone else ""
    return _text(f"Deleted device '{name}' ({device_id}){suffix}")

"""Label tools: create, update and delete. Listing is list_labels, in entities.py.

Labels live in the label registry and tag entities, devices and areas across
domains. Deleting a label needs no cleanup here: the entity, device and area
registries each listen for the removal and drop the label from what carried it.
"""

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    dumps,
    register_tool,
)

COLOR_PROPERTY = {
    "type": "string",
    "description": (
        "Label color: a Home Assistant color name such as 'red' or 'light-green', or a "
        "hex value such as '#ff9800'"
    ),
}
ICON_PROPERTY = {
    "type": "string",
    "description": "Material Design icon in the form 'mdi:name' (e.g. 'mdi:tag')",
}
DESCRIPTION_PROPERTY = {
    "type": "string",
    "description": "Free-text description of what the label is for",
}

# Optional label fields an update may change, in the order they are reported.
_OPTIONAL_FIELDS = ("color", "icon", "description")

# The color names the frontend's label dialog offers, and the only ones the websocket
# command accepts besides #RRGGBB (SUPPORTED_LABEL_THEME_COLORS in core's
# components/config/label_registry.py). The registry itself stores any string, so
# this is the one place they are enforced; test_ha_api_contracts.py checks core
# still accepts every name here.
THEME_COLORS = frozenset(
    {
        "primary",
        "accent",
        "disabled",
        "red",
        "pink",
        "purple",
        "deep-purple",
        "indigo",
        "blue",
        "light-blue",
        "cyan",
        "teal",
        "green",
        "light-green",
        "lime",
        "yellow",
        "amber",
        "orange",
        "deep-orange",
        "brown",
        "light-grey",
        "grey",
        "dark-grey",
        "blue-grey",
        "black",
        "white",
    }
)


def label_json(label: lr.LabelEntry) -> dict[str, Any]:
    """The fields list_labels and hass://labels report for a label."""
    return {
        "label_id": label.label_id,
        "name": label.name,
        "color": label.color,
        "icon": label.icon,
        "description": label.description,
    }


def _get_label(registry: lr.LabelRegistry, label_id: str) -> lr.LabelEntry:
    label = registry.async_get_label(label_id)
    if label is None:
        raise ValueError(f"No label with label_id '{label_id}' (see list_labels)")
    return label


def _string_argument(arguments: dict[str, Any], field: str) -> str | None:
    """Return a string argument, None if absent or null, and refuse any other type.

    call_tool checks that required arguments are present, not what type they are.
    """
    value = arguments.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string, not {type(value).__name__}")
    return value


def _clean_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("A label needs a name")
    return name


def _color(value: str) -> str:
    """Accept what the websocket command does: #RRGGBB or a theme color name."""
    if value in THEME_COLORS:
        return value
    try:
        return cv.color_hex(value)
    except vol.Invalid:
        raise ValueError(
            f"'{value}' is not a valid color: use #RRGGBB or one of "
            f"{', '.join(sorted(THEME_COLORS))}"
        ) from None


def _optional_value(field: str, value: str | None) -> str | None:
    """Normalize an optional field's argument: empty means none, otherwise it must be valid."""
    if not value:
        return None
    if field == "color":
        return _color(value)
    return cv.icon(value) if field == "icon" else value


def _error(action: str, exc: Exception) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error {action}: {str(exc)}"}]}


@register_tool(
    name="create_label",
    description=(
        "Create a label, which can tag entities, devices and areas across domains. "
        "Label names must be unique"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the new label"},
            "color": COLOR_PROPERTY,
            "icon": ICON_PROPERTY,
            "description": DESCRIPTION_PROPERTY,
        },
        "required": ["name"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def create_label(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a label."""
    try:
        label = lr.async_get(hass).async_create(
            _clean_name(_string_argument(arguments, "name")),
            **{f: _optional_value(f, _string_argument(arguments, f)) for f in _OPTIONAL_FIELDS},
        )
    except Exception as e:
        return _error("creating label", e)
    return {"content": [{"type": "text", "text": f"Created label {dumps(label_json(label))}"}]}


@register_tool(
    name="update_label",
    description=(
        "Update a label's name, color, icon or description. Only the fields given are "
        "changed; an empty string clears color, icon or description"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "label_id": {
                "type": "string",
                "description": "ID of the label to update (see list_labels)",
            },
            "name": {"type": "string", "description": "New name; must not be in use"},
            "color": COLOR_PROPERTY,
            "icon": ICON_PROPERTY,
            "description": DESCRIPTION_PROPERTY,
        },
        "required": ["label_id"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def update_label(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update the fields of a label that were given."""
    try:
        registry = lr.async_get(hass)
        label = _get_label(registry, arguments["label_id"])

        changes: dict[str, Any] = {}
        for field in _OPTIONAL_FIELDS:
            if (value := _string_argument(arguments, field)) is not None:
                changes[field] = _optional_value(field, value)
        if (name := _string_argument(arguments, "name")) is not None:
            name = _clean_name(name)
            taken = registry.async_get_label_by_name(name)
            if taken is not None and taken.label_id != label.label_id:
                raise ValueError(f"The name '{name}' is already used by label '{taken.label_id}'")
            changes["name"] = name

        if not changes:
            raise ValueError("Nothing to update: give a name, color, icon or description")
        label = registry.async_update(label.label_id, **changes)
    except Exception as e:
        return _error("updating label", e)
    return {"content": [{"type": "text", "text": f"Updated label {dumps(label_json(label))}"}]}


@register_tool(
    name="delete_label",
    description=(
        "Delete a label and remove it from every entity, device and area that carries "
        "it. This cannot be undone"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "label_id": {
                "type": "string",
                "description": "ID of the label to delete (see list_labels)",
            },
        },
        "required": ["label_id"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_label(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a label, reporting what it was removed from."""
    try:
        registry = lr.async_get(hass)
        label = _get_label(registry, arguments["label_id"])
        label_id = label.label_id

        carriers = {
            ("entity", "entities"): sum(
                label_id in e.labels for e in er.async_get(hass).entities.values()
            ),
            ("device", "devices"): len(dr.async_entries_for_label(dr.async_get(hass), label_id)),
            ("area", "areas"): sum(label_id in a.labels for a in ar.async_get(hass).areas.values()),
        }
        registry.async_delete(label_id)
    except Exception as e:
        return _error("deleting label", e)

    removed = ", ".join(
        f"{count} {singular if count == 1 else plural}"
        for (singular, plural), count in carriers.items()
        if count
    )
    suffix = f", removing it from {removed}" if removed else ""
    return {
        "content": [{"type": "text", "text": f"Deleted label '{label.name}' ({label_id}){suffix}"}]
    }

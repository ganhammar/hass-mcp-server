"""Read and narrowly update Home Assistant's entity registry, never raw storage."""

from typing import Any

from homeassistant.core import HomeAssistant, valid_domain, valid_entity_id
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..json_utils import dumps
from . import ANNOTATION_DESTRUCTIVE, ANNOTATION_READ_ONLY, register_tool
from .registries import (
    AREA_ID_PROPERTY,
    LABELS_PROPERTY,
    area_id_argument,
    boolean_argument,
    labels_argument,
)

_WARNING = (
    "References are not migrated by this tool. Audit automations, scripts, scenes, "
    "dashboards, templates and external consumers separately. Home Assistant may notify "
    "listeners and update live entities; this tool does not reload or restart anything. "
    "A preview does not validate whether the proposed entity ID will be accepted."
)
_ENTITY_ID = {"type": "string", "description": "Existing registered entity ID (domain.object_id)."}


def _result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": dumps(value)}]}


def _error(message: str) -> dict[str, Any]:
    """Return a normal tool text response, consistent with other handlers."""
    return {"content": [{"type": "text", "text": f"Error: {message}"}]}


def _entry(entry: er.RegistryEntry) -> dict[str, Any]:
    """Expose selected registry metadata, not runtime state or integration options."""
    return {
        **{
            key: getattr(entry, key)
            for key in (
                "entity_id",
                "name",
                "original_name",
                "unique_id",
                "platform",
                "config_entry_id",
                "device_id",
                "area_id",
                "disabled_by",
                "hidden_by",
            )
        },
        "labels": sorted(entry.labels),
    }


def _device_is_disabled(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    return device is not None and device.disabled


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and valid_entity_id(value)


@register_tool(
    name="list_entity_registry",
    description=(
        "List registered entities, including disabled entities absent from live states. "
        "Supports domain filtering and pagination (100 entries by default)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Optional entity domain, e.g. sensor or light.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum entries per page (default 100; max 500).",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of matching entries to skip (default 0).",
            },
        },
        "additionalProperties": False,
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_entity_registry(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return sorted and bounded registry entries, not live states."""
    domain = arguments.get("domain")
    if domain is not None and (not isinstance(domain, str) or not valid_domain(domain)):
        return _error("domain must be a valid entity domain.")

    limit = arguments.get("limit", 100)
    offset = arguments.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 500:
        return _error("limit must be an integer between 1 and 500.")
    if type(offset) is not int or offset < 0:
        return _error("offset must be a nonnegative integer.")

    registry = er.async_get(hass)
    entries = sorted(
        (
            entry
            for entry in registry.entities.values()
            if domain is None or entry.entity_id.split(".", 1)[0] == domain
        ),
        key=lambda entry: entry.entity_id,
    )
    return _result(
        {
            "entities": [_entry(entry) for entry in entries[offset : offset + limit]],
            "total": len(entries),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < len(entries),
        }
    )


@register_tool(
    name="get_entity_registry",
    description="Get registry metadata for one entity, even when disabled or unavailable.",
    input_schema={
        "type": "object",
        "properties": {"entity_id": _ENTITY_ID},
        "required": ["entity_id"],
        "additionalProperties": False,
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_entity_registry(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Look up the registry, not hass.states."""
    if not _valid_id(arguments["entity_id"]):
        return _error("entity_id must be a valid entity ID (domain.object_id).")
    entry = er.async_get(hass).async_get(arguments["entity_id"])
    if entry is None:
        return _error("Entity is not in the registry.")
    return _result(_entry(entry))


@register_tool(
    name="update_entity_registry",
    description=(
        "Preview (default) or apply an entity registry update: name, same-domain "
        "new_entity_id, area, labels, and whether the entity is disabled or hidden. Only "
        "the fields given are changed. References are NOT migrated when the ID changes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": _ENTITY_ID,
            "new_entity_id": {
                "type": "string",
                "description": "Valid unused ID in the same domain.",
            },
            "name": {
                "type": "string",
                "description": "Non-blank display name.",
            },
            "area_id": {
                **AREA_ID_PROPERTY,
                "description": (
                    f"{AREA_ID_PROPERTY['description']}, so the entity follows its device's area"
                ),
            },
            "labels": LABELS_PROPERTY,
            "disabled": {
                "type": "boolean",
                "description": (
                    "True disables the entity, removing it from live states; false enables "
                    "it, which takes effect when Home Assistant reloads its integration "
                    "about 30 seconds later."
                ),
            },
            "hidden": {
                "type": "boolean",
                "description": (
                    "True hides the entity from dashboards and voice assistants while it "
                    "keeps updating; false shows it again."
                ),
            },
            "apply": {
                "type": "boolean",
                "default": False,
                "description": "True explicitly applies the update.",
            },
        },
        "required": ["entity_id"],
        "additionalProperties": False,
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def update_entity_registry(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Preview changes or let Home Assistant validate and perform the update."""
    entity_id = arguments["entity_id"]
    if not _valid_id(entity_id):
        return _error("Invalid entity_id; expected domain.object_id.")

    apply = arguments.get("apply", False)
    if not isinstance(apply, bool):
        return _error("apply must be a boolean, not a string or number.")

    changes = {key: arguments[key] for key in ("new_entity_id", "name") if key in arguments}

    if "name" in changes:
        name = changes["name"]
        if not isinstance(name, str) or not name.strip():
            return _error("name must be a non-blank string.")

    new_id = changes.get("new_entity_id", entity_id)
    if not isinstance(new_id, str):
        return _error("new_entity_id must be a string.")

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return _error(f"Entity is not in the registry: {entity_id}")

    try:
        area_given, area_id = area_id_argument(hass, arguments)
        labels = labels_argument(hass, arguments)
        disabled = boolean_argument(arguments, "disabled")
        hidden = boolean_argument(arguments, "hidden")
    except ValueError as err:
        return _error(str(err))

    if area_given:
        changes["area_id"] = area_id
    if labels is not None:
        changes["labels"] = labels
    # An entity something else disabled or hid stays attributed to that; only the
    # user's own choice is set here, as in the frontend.
    if disabled is not None and disabled != (entry.disabled_by is not None):
        if not disabled and _device_is_disabled(hass, entry):
            return _error("The entity's device is disabled. Enable it with update_device.")
        changes["disabled_by"] = er.RegistryEntryDisabler.USER if disabled else None
    if hidden is not None and hidden != (entry.hidden_by is not None):
        changes["hidden_by"] = er.RegistryEntryHider.USER if hidden else None

    if not changes and disabled is None and hidden is None:
        return _error("Supply new_entity_id, name, area_id, labels, disabled or hidden.")

    before = _entry(entry)
    after = {**before, "entity_id": new_id}
    for key in ("name", "area_id", "disabled_by", "hidden_by"):
        if key in changes:
            after[key] = changes[key]
    if "labels" in changes:
        after["labels"] = sorted(changes["labels"])

    if apply and after != before:
        try:
            after = _entry(registry.async_update_entity(entity_id, **changes))
        except ValueError as err:
            return _error(f"Registry update rejected: {err}")

    return _result(
        {
            "applied": apply,
            "changed": before != after,
            "mode": "applied" if apply else "preview",
            "before": before,
            "after": after,
            "warning": _WARNING,
        }
    )

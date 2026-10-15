"""Read and narrowly update Home Assistant's entity registry, never raw storage."""

import unicodedata
from typing import Any

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import entity_registry as er

from ..json_utils import dumps
from . import ANNOTATION_DESTRUCTIVE, ANNOTATION_READ_ONLY, register_tool

_WARNING = (
    "References are not migrated by this tool. Audit automations, scripts, scenes, "
    "dashboards, templates and external consumers separately. Home Assistant may notify "
    "listeners and update live entities; this tool does not reload or restart anything."
)
_ENTITY_ID = {"type": "string", "description": "Existing registered entity ID (domain.object_id)."}


def _result(value: Any, *, error: bool = False) -> dict[str, Any]:
    result = {"content": [{"type": "text", "text": dumps(value)}]}
    if error:
        result["isError"] = True
    return result


def _entry(entry: er.RegistryEntry) -> dict[str, Any]:
    """Expose selected registry metadata, not runtime state or integration options."""
    return {
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
    }


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and valid_entity_id(value)


@register_tool(
    name="list_entity_registry",
    description="List registered entities, including disabled entities absent from live states.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    annotations=ANNOTATION_READ_ONLY,
)
async def list_entity_registry(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return registry entries in stable entity ID order."""
    if arguments:
        return _result({"error": "No arguments are supported."}, error=True)
    registry = er.async_get(hass)
    return _result(
        {
            "entities": [
                _entry(e) for e in sorted(registry.entities.values(), key=lambda e: e.entity_id)
            ]
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
    if set(arguments) - {"entity_id"} or not _valid_id(arguments.get("entity_id")):
        return _result({"error": "Only a valid entity_id is supported."}, error=True)
    entry = er.async_get(hass).async_get(arguments["entity_id"])
    if entry is None:
        return _result({"error": "Entity is not in the registry."}, error=True)
    return _result(_entry(entry))


@register_tool(
    name="update_entity_registry",
    description=(
        "Preview (default) or apply an entity registry name/ID update. Only name and "
        "same-domain new_entity_id are writable. References are NOT migrated."
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
                "description": "Nonblank display name without control characters.",
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
    """Validate without yielding, then optionally call HA's official registry API."""

    def fail(message: str) -> dict[str, Any]:
        return _result({"error": message, "applied": False}, error=True)

    if set(arguments) - {"entity_id", "new_entity_id", "name", "apply"}:
        return fail("Only entity_id, new_entity_id, name and apply are supported.")
    entity_id = arguments.get("entity_id")
    if not _valid_id(entity_id):
        return fail("Invalid entity_id; expected domain.object_id.")
    apply = arguments.get("apply", False)
    if not isinstance(apply, bool):
        return fail("apply must be a boolean, not a string or number.")
    changes = {key: arguments[key] for key in ("new_entity_id", "name") if key in arguments}
    if not changes:
        return fail("Supply new_entity_id and/or name.")
    if "name" in changes:
        name = changes["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(unicodedata.category(char).startswith("C") for char in name)
        ):
            return fail("name must be a nonblank string without control characters.")
    new_id = changes.get("new_entity_id", entity_id)
    if not _valid_id(new_id):
        return fail("Invalid new_entity_id; expected domain.object_id.")
    if new_id.split(".", 1)[0] != entity_id.split(".", 1)[0]:
        return fail("new_entity_id must have the same domain as entity_id.")
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return fail(f"Entity is not in the registry: {entity_id}")
    if new_id != entity_id and (
        registry.async_get(new_id) is not None or not hass.states.async_available(new_id)
    ):
        return fail(
            f"new_entity_id already exists or is reserved (including disabled entities): {new_id}"
        )
    before = _entry(entry)
    after = {**before, "entity_id": new_id}
    if "name" in changes:
        after["name"] = changes["name"]
    if apply and after != before:
        try:
            after = _entry(registry.async_update_entity(entity_id, **changes))
        except ValueError as err:
            return fail(f"Registry update rejected: {err}")
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

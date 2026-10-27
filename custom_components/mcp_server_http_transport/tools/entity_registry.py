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
    if set(arguments) - {"domain", "limit", "offset"}:
        return _error("Only domain, limit and offset are supported.")

    domain = arguments.get("domain")
    if domain is not None and (not isinstance(domain, str) or not _valid_id(f"{domain}.example")):
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
    if set(arguments) - {"entity_id"} or not _valid_id(arguments.get("entity_id")):
        return _error("Only a valid entity_id is supported.")
    entry = er.async_get(hass).async_get(arguments["entity_id"])
    if entry is None:
        return _error("Entity is not in the registry.")
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
    """Preview changes or let Home Assistant validate and perform the update."""
    if set(arguments) - {"entity_id", "new_entity_id", "name", "apply"}:
        return _error("Only entity_id, new_entity_id, name and apply are supported.")

    entity_id = arguments.get("entity_id")
    if not _valid_id(entity_id):
        return _error("Invalid entity_id; expected domain.object_id.")

    apply = arguments.get("apply", False)
    if not isinstance(apply, bool):
        return _error("apply must be a boolean, not a string or number.")

    changes = {key: arguments[key] for key in ("new_entity_id", "name") if key in arguments}
    if not changes:
        return _error("Supply new_entity_id and/or name.")

    if "name" in changes:
        name = changes["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(unicodedata.category(char).startswith("C") for char in name)
        ):
            return _error("name must be a nonblank string without control characters.")

    new_id = changes.get("new_entity_id", entity_id)
    if not isinstance(new_id, str):
        return _error("new_entity_id must be a string.")

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return _error(f"Entity is not in the registry: {entity_id}")

    before = _entry(entry)
    after = {**before, "entity_id": new_id}
    if "name" in changes:
        after["name"] = changes["name"]

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

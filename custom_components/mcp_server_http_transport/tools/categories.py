"""Category definition CRUD tools and shared category-assignment helpers."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er

from . import _HAJSONEncoder, register_tool

_LOGGER = logging.getLogger(__name__)

VALID_SCOPES = frozenset({"automation", "script", "scene", "entity"})


def resolve_category_id(hass: HomeAssistant, scope: str, name: str) -> str:
    """Return the category_id for a category name within a scope, or raise."""
    reg = cr.async_get(hass)
    for entry in reg.async_list_categories(scope=scope):
        if entry.name.casefold() == name.casefold():
            return entry.category_id
    raise ValueError(
        f"No category named '{name}' in scope '{scope}'. Create it with create_category first."
    )


def write_entity_category(
    hass: HomeAssistant, entity_id: str, scope: str, category_id: str | None
) -> None:
    """Set (category_id given) or clear (None) the entity's category for a scope."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry is None:
        raise ValueError(f"Entity '{entity_id}' not found in entity registry")
    categories = dict(entry.categories)
    if category_id is None:
        categories.pop(scope, None)
    else:
        categories[scope] = category_id
    ent_reg.async_update_entity(entity_id, categories=categories)


def _category_dict(entry: cr.CategoryEntry) -> dict[str, Any]:
    return {
        "category_id": entry.category_id,
        "name": entry.name,
        "icon": entry.icon,
        "created_at": entry.created_at,
        "modified_at": entry.modified_at,
    }


@register_tool(
    name="list_categories",
    description="List all category definitions within a scope",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "One of: automation, script, scene, entity",
            }
        },
        "required": ["scope"],
    },
)
async def list_categories(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List category definitions in a scope."""
    try:
        scope = arguments["scope"]
        if scope not in VALID_SCOPES:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown scope '{scope}'. "
                        f"Supported: {', '.join(sorted(VALID_SCOPES))}",
                    }
                ]
            }
        reg = cr.async_get(hass)
        cats = [_category_dict(c) for c in reg.async_list_categories(scope=scope)]
        return {
            "content": [{"type": "text", "text": json.dumps(cats, indent=2, cls=_HAJSONEncoder)}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing categories: {str(e)}"}]}


@register_tool(
    name="create_category",
    description="Create a new category definition within a scope",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "One of: automation, script, scene, entity",
            },
            "name": {
                "type": "string",
                "description": "Category name",
            },
            "icon": {
                "type": "string",
                "description": "Optional icon, e.g. 'mdi:lightbulb'",
            },
        },
        "required": ["scope", "name"],
    },
)
async def create_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a category definition in a scope."""
    try:
        scope = arguments["scope"]
        if scope not in VALID_SCOPES:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown scope '{scope}'. "
                        f"Supported: {', '.join(sorted(VALID_SCOPES))}",
                    }
                ]
            }
        entry = cr.async_get(hass).async_create(
            scope=scope, name=arguments["name"], icon=arguments.get("icon")
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Created category '{entry.name}' with id: {entry.category_id}",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating category: {str(e)}"}]}


@register_tool(
    name="update_category",
    description="Rename or change the icon of a category definition",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "One of: automation, script, scene, entity",
            },
            "category_id": {
                "type": "string",
                "description": "The category ID to update",
            },
            "name": {
                "type": "string",
                "description": "New category name",
            },
            "icon": {
                "type": "string",
                "description": "New icon, e.g. 'mdi:lightbulb'",
            },
        },
        "required": ["scope", "category_id"],
    },
)
async def update_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update a category definition in a scope."""
    try:
        scope = arguments["scope"]
        if scope not in VALID_SCOPES:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown scope '{scope}'. "
                        f"Supported: {', '.join(sorted(VALID_SCOPES))}",
                    }
                ]
            }
        kwargs: dict[str, Any] = {}
        if "name" in arguments:
            kwargs["name"] = arguments["name"]
        if "icon" in arguments:
            kwargs["icon"] = arguments["icon"]
        entry = cr.async_get(hass).async_update(
            scope=scope, category_id=arguments["category_id"], **kwargs
        )
        return {"content": [{"type": "text", "text": f"Updated category {entry.category_id}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating category: {str(e)}"}]}


@register_tool(
    name="delete_category",
    description="Delete a category definition (HA clears it from all assigned entities)",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "One of: automation, script, scene, entity",
            },
            "category_id": {
                "type": "string",
                "description": "The category ID to delete",
            },
        },
        "required": ["scope", "category_id"],
    },
)
async def delete_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a category definition from a scope."""
    try:
        scope = arguments["scope"]
        if scope not in VALID_SCOPES:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown scope '{scope}'. "
                        f"Supported: {', '.join(sorted(VALID_SCOPES))}",
                    }
                ]
            }
        cr.async_get(hass).async_delete(scope=scope, category_id=arguments["category_id"])
        return {
            "content": [{"type": "text", "text": f"Deleted category {arguments['category_id']}"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting category: {str(e)}"}]}

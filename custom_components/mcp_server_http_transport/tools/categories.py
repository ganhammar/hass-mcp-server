"""Category tools for automations, scripts, scenes and helpers.

Categories are not part of an item's YAML or storage config. They live in the
category registry, scoped by the frontend page they belong to ("automation",
"script", "scene", "helpers"), and an entity is assigned to at most one category
per scope through its entity registry entry.
"""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from . import ANNOTATION_IDEMPOTENT, ANNOTATION_READ_ONLY, dumps, register_tool

_LOGGER = logging.getLogger(__name__)

# Domains with a category scope of their own; every other helper shares "helpers".
_DOMAIN_SCOPES = frozenset({"automation", "script", "scene"})
HELPERS_SCOPE = "helpers"

CATEGORY_PROPERTY = {
    "type": "string",
    "description": (
        "Category to put the new item in, by category_id or name (see list_categories). "
        "A name that does not exist yet creates a new category"
    ),
}


async def _async_scope_for(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """Return the category scope an entity is categorized under in the frontend.

    Automations, scripts and scenes each have their own scope. Helpers share one,
    and a helper is any entity whose integration declares integration_type
    "helper": the input_* domains, counter, timer and schedule, as well as config
    entry helpers such as template or utility_meter.
    """
    if entry.domain in _DOMAIN_SCOPES:
        return entry.domain
    try:
        integration = await async_get_integration(hass, entry.platform)
    except Exception:
        integration = None
    if integration is not None and integration.integration_type == "helper":
        return HELPERS_SCOPE
    raise ValueError(
        f"'{entry.entity_id}' is not an automation, script, scene or helper, "
        "so it cannot be categorized"
    )


def _resolve_category(hass: HomeAssistant, scope: str, category: str) -> cr.CategoryEntry:
    """Find a category by ID or case-insensitive name, creating it if neither matches."""
    registry = cr.async_get(hass)
    entry = registry.async_get_category(scope=scope, category_id=category)
    if entry is not None:
        return entry
    for entry in registry.async_list_categories(scope=scope):
        if entry.name.casefold() == category.casefold():
            return entry
    return registry.async_create(scope=scope, name=category)


async def async_assign_category(hass: HomeAssistant, entity_id: str, category: str | None) -> str:
    """Assign an entity to a category, or clear its category when category is empty.

    Only the entity's own scope is touched; categories it holds in other scopes
    are kept. Returns a phrase describing the outcome for the response text.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        raise ValueError(f"'{entity_id}' is not in the entity registry")
    scope = await _async_scope_for(hass, entry)

    categories = dict(entry.categories)
    if category:
        category_entry = _resolve_category(hass, scope, category)
        categories[scope] = category_entry.category_id
        outcome = f"category '{category_entry.name}' ({category_entry.category_id})"
    else:
        categories.pop(scope, None)
        outcome = "no category"
    registry.async_update_entity(entity_id, categories=categories)
    return outcome


def registered_entity_id(hass: HomeAssistant, domain: str, platform: str, unique_id: str) -> str:
    """Return the entity ID an item's platform registered it under.

    A saved item is loaded, and so registered, before the tool that saved it
    returns, which is what makes it addressable by the ID the tool just wrote.
    """
    entity_id = er.async_get(hass).async_get_entity_id(domain, platform, unique_id)
    if entity_id is None:
        raise ValueError(f"{domain} '{unique_id}' is not in the entity registry")
    return entity_id


async def async_category_suffix(
    hass: HomeAssistant,
    arguments: dict[str, Any],
    domain: str,
    platform: str,
    unique_id: str,
) -> str:
    """Apply a create tool's optional category argument, describing it for the response.

    The item is already saved and loaded by the time this runs, so it is looked up
    by the unique ID its platform registered it under. A failure is reported
    alongside the success rather than as a failed call, since the item exists.
    """
    if not arguments.get("category"):
        return ""
    try:
        entity_id = registered_entity_id(hass, domain, platform, unique_id)
        return f", with {await async_assign_category(hass, entity_id, arguments['category'])}"
    except Exception as e:
        return f", but the category could not be set: {str(e)}"


@register_tool(
    name="list_categories",
    description=(
        "List the categories for automations, scripts, scenes or helpers, with the "
        "entity IDs assigned to each. Categories are scoped per page in the UI"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["automation", "script", "scene", HELPERS_SCOPE],
                "description": "Which categories to list (default: automation)",
            }
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_categories(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List the categories in a scope and the entities assigned to each."""
    scope = arguments.get("scope") or "automation"
    members: dict[str, list[str]] = {}
    for entry in er.async_get(hass).entities.values():
        if category_id := entry.categories.get(scope):
            members.setdefault(category_id, []).append(entry.entity_id)

    categories = [
        {
            "category_id": category.category_id,
            "name": category.name,
            "icon": category.icon,
            "entity_ids": sorted(members.get(category.category_id, [])),
        }
        for category in cr.async_get(hass).async_list_categories(scope=scope)
    ]
    return {"content": [{"type": "text", "text": dumps(categories)}]}


@register_tool(
    name="set_category",
    description=(
        "Put an automation, script, scene or helper in a category, or take it out of "
        "one. The scope follows from the entity, so a category name only matches "
        "categories of the same kind"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": (
                    "Entity ID of the automation, script, scene or helper, "
                    "e.g. automation.porch_light or input_boolean.guest_mode"
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "Category by category_id or name (see list_categories). A name that "
                    "does not exist yet creates a new category; an empty string removes "
                    "the entity from its category"
                ),
            },
        },
        "required": ["entity_id", "category"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def set_category(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Assign an entity to a category or clear its category."""
    entity_id = arguments["entity_id"]
    try:
        outcome = await async_assign_category(hass, entity_id, arguments["category"])
        return {"content": [{"type": "text", "text": f"{entity_id} now has {outcome}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error setting category: {str(e)}"}]}

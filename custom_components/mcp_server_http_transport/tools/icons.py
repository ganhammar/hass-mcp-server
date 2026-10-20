"""Icon overrides: the set_icon tool and the create and update tools' icon argument.

The icon is set as the entity registry's icon override, the same field the
frontend's entity settings dialog writes. Automations have no icon in their
config at all, so this is the only place theirs can go; using it for every kind
keeps one meaning for the argument. The override takes precedence over an icon
in a script's, scene's or helper's own config.
"""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from . import ANNOTATION_IDEMPOTENT, register_tool
from .categories import registered_entity_id

ICON_PROPERTY = {
    "type": "string",
    "description": (
        "Material Design icon in the form 'mdi:name' (e.g. 'mdi:lightbulb'), set as the "
        "entity's icon override; it takes precedence over any icon in config. An empty "
        "string removes the override"
    ),
}


def async_set_icon(hass: HomeAssistant, entity_id: str, icon: str) -> str:
    """Set an entity's icon override, or remove it when icon is empty.

    Returns a phrase describing the outcome for the response text.
    """
    registry = er.async_get(hass)
    if registry.async_get(entity_id) is None:
        raise ValueError(
            f"'{entity_id}' is not in the entity registry, so it has no icon override "
            "(entities without a unique ID cannot be customized)"
        )
    if not icon:
        registry.async_update_entity(entity_id, icon=None)
        return "no icon override"
    icon = cv.icon(icon)
    registry.async_update_entity(entity_id, icon=icon)
    return f"icon {icon}"


async def async_icon_suffix(
    hass: HomeAssistant,
    arguments: dict[str, Any],
    domain: str,
    platform: str,
    unique_id: str,
) -> str:
    """Apply a tool's optional icon argument, describing it for the response.

    Runs after the item is saved and loaded, like the category argument, and
    reports a failure alongside the success for the same reason: the item exists.
    """
    icon = arguments.get("icon")
    if icon is None:
        return ""
    try:
        entity_id = registered_entity_id(hass, domain, platform, unique_id)
        return f", with {async_set_icon(hass, entity_id, icon)}"
    except Exception as e:
        return f", but the icon could not be set: {str(e)}"


@register_tool(
    name="set_icon",
    description=(
        "Set or remove the icon of an entity, such as an automation, script, scene or "
        "helper, without touching its config. Works for any entity in the entity "
        "registry, as the entity settings dialog does"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Entity ID, e.g. automation.porch_light or script.morning",
            },
            "icon": ICON_PROPERTY,
        },
        "required": ["entity_id", "icon"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def set_icon(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Set or remove an entity's icon override."""
    entity_id = arguments["entity_id"]
    try:
        outcome = async_set_icon(hass, entity_id, arguments["icon"])
        return {"content": [{"type": "text", "text": f"{entity_id} now has {outcome}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error setting icon: {str(e)}"}]}

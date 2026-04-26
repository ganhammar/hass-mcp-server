"""Helper entity CRUD tools (input_boolean, input_text, counter, timer, etc.)."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import _HAJSONEncoder, register_tool

_LOGGER = logging.getLogger(__name__)

HELPER_DOMAINS = frozenset(
    {
        "counter",
        "input_boolean",
        "input_button",
        "input_datetime",
        "input_number",
        "input_select",
        "input_text",
        "schedule",
        "timer",
    }
)


def _get_collection(hass: HomeAssistant, domain: str):
    """Return the storage collection for a helper domain, or raise."""
    collection = hass.data.get(domain)
    if collection is None or not hasattr(collection, "async_create_item"):
        raise ValueError(
            f"Helper domain '{domain}' is not available. "
            "Ensure the domain is loaded and is a UI-managed helper."
        )
    return collection


async def _entity_id_to_item_id(hass: HomeAssistant, entity_id: str) -> tuple[str, str]:
    """Resolve entity_id to (domain, item_id) via the entity registry."""
    domain = entity_id.split(".")[0]
    if domain not in HELPER_DOMAINS:
        raise ValueError(f"'{entity_id}' is not a helper entity (domain: {domain})")
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        raise ValueError(f"Entity '{entity_id}' not found in entity registry")
    return domain, entry.unique_id


@register_tool(
    name="list_helpers",
    description=(
        "List all helper entities in Home Assistant "
        "(input_boolean, input_number, input_text, input_select, "
        "input_datetime, input_button, counter, timer, schedule). "
        "Returns their current state and configuration"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": (
                    "Filter by helper domain, e.g. 'input_boolean' or 'counter'. "
                    "Omit to list all helper types"
                ),
            }
        },
    },
)
async def list_helpers(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List helpers, optionally filtered by domain."""
    domain_filter = arguments.get("domain")

    if domain_filter and domain_filter not in HELPER_DOMAINS:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Unknown helper domain '{domain_filter}'. "
                        f"Supported: {', '.join(sorted(HELPER_DOMAINS))}"
                    ),
                }
            ]
        }

    domains = {domain_filter} if domain_filter else HELPER_DOMAINS
    registry = er.async_get(hass)
    helpers = []

    for domain in sorted(domains):
        for state in hass.states.async_all():
            if not state.entity_id.startswith(f"{domain}."):
                continue
            entry = registry.async_get(state.entity_id)
            helpers.append(
                {
                    "entity_id": state.entity_id,
                    "domain": domain,
                    "state": state.state,
                    "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                    "attributes": dict(state.attributes),
                    "unique_id": entry.unique_id if entry else None,
                }
            )

    return {
        "content": [{"type": "text", "text": json.dumps(helpers, indent=2, cls=_HAJSONEncoder)}]
    }


@register_tool(
    name="get_helper_config",
    description=(
        "Get the full stored configuration of a UI-managed helper by its entity ID. "
        "Returns the raw storage config, not just the current state"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The helper entity ID (e.g., input_boolean.my_flag)",
            }
        },
        "required": ["entity_id"],
    },
)
async def get_helper_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a helper's stored config."""
    try:
        domain, item_id = await _entity_id_to_item_id(hass, arguments["entity_id"])
        collection = _get_collection(hass, domain)
        item = collection.data.get(item_id)
        if item is None:
            raise ValueError(
                f"Helper '{arguments['entity_id']}' not found in storage "
                "(it may be YAML-configured)"
            )
        return {
            "content": [{"type": "text", "text": json.dumps(item, indent=2, cls=_HAJSONEncoder)}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting helper config: {str(e)}"}]}


@register_tool(
    name="create_helper",
    description=(
        "Create a new helper entity in Home Assistant. "
        "Supported domains: input_boolean, input_number, input_text, input_select, "
        "input_datetime, input_button, counter, timer, schedule"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": ("Helper type to create, e.g. 'input_boolean', 'counter', 'timer'"),
            },
            "config": {
                "type": "object",
                "description": (
                    "Helper configuration. All types require 'name'. "
                    "input_number also requires 'min' and 'max'. "
                    "input_select requires 'options' (list of strings). "
                    "Optional for most types: icon, initial, restore"
                ),
            },
        },
        "required": ["domain", "config"],
    },
)
async def create_helper(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new helper."""
    domain = arguments["domain"]
    config = arguments["config"]

    if domain not in HELPER_DOMAINS:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Unknown helper domain '{domain}'. "
                        f"Supported: {', '.join(sorted(HELPER_DOMAINS))}"
                    ),
                }
            ]
        }

    try:
        collection = _get_collection(hass, domain)
        item = await collection.async_create_item(config)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully created {domain} helper with id: {item['id']}",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating helper: {str(e)}"}]}


@register_tool(
    name="update_helper",
    description="Update an existing UI-managed helper entity by its entity ID",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The helper entity ID (e.g., input_boolean.my_flag)",
            },
            "config": {
                "type": "object",
                "description": (
                    "Updated helper configuration. Include all fields, not just changed ones"
                ),
            },
        },
        "required": ["entity_id", "config"],
    },
)
async def update_helper(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing helper."""
    try:
        domain, item_id = await _entity_id_to_item_id(hass, arguments["entity_id"])
        collection = _get_collection(hass, domain)
        await collection.async_update_item(item_id, arguments["config"])
        return {"content": [{"type": "text", "text": "Successfully updated helper"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating helper: {str(e)}"}]}


@register_tool(
    name="delete_helper",
    description="Delete a UI-managed helper entity from Home Assistant by its entity ID",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The helper entity ID (e.g., input_boolean.my_flag)",
            }
        },
        "required": ["entity_id"],
    },
)
async def delete_helper(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a helper."""
    try:
        domain, item_id = await _entity_id_to_item_id(hass, arguments["entity_id"])
        collection = _get_collection(hass, domain)
        await collection.async_delete_item(item_id)
        return {"content": [{"type": "text", "text": "Successfully deleted helper"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting helper: {str(e)}"}]}

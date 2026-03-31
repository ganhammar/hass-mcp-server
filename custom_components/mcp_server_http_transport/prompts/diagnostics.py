"""Diagnostic and troubleshooting prompts."""

import json
from typing import Any

from homeassistant.core import HomeAssistant

from . import register_prompt


@register_prompt(
    name="troubleshoot_device",
    description="Diagnostic prompt for troubleshooting a device or entity",
    arguments=[
        {
            "name": "entity_id",
            "description": "The entity ID to troubleshoot",
            "required": True,
        }
    ],
)
def troubleshoot_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a troubleshooting prompt for a device/entity."""
    entity_id = arguments.get("entity_id", "")
    state = hass.states.get(entity_id)

    if state is None:
        state_info = f"Entity {entity_id} not found"
    else:
        state_info = json.dumps(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
                "last_changed": state.last_changed.isoformat(),
                "last_updated": state.last_updated.isoformat(),
            },
            indent=2,
        )

    return {
        "description": f"Troubleshoot {entity_id}",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"I need help troubleshooting the following Home Assistant entity.\n\n"
                        f"Current state:\n```json\n{state_info}\n```\n\n"
                        f"Please analyze the entity state and suggest potential issues and fixes. "
                        f"Check if the state seems normal, look at attributes for anomalies, "
                        f"and suggest common troubleshooting steps."
                    ),
                },
            }
        ],
    }


@register_prompt(
    name="setup_guide",
    description="Guided troubleshooting steps for an entity in a problem state",
    arguments=[
        {
            "name": "entity_id",
            "description": "The entity ID in a problem state",
            "required": True,
        }
    ],
)
def setup_guide(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a setup/troubleshooting guide for a problem entity."""
    entity_id = arguments.get("entity_id", "")
    state = hass.states.get(entity_id)

    if state is None:
        state_info = f"Entity {entity_id} not found in Home Assistant."
        domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
        device_class = "unknown"
    else:
        domain = state.entity_id.split(".")[0]
        device_class = state.attributes.get("device_class", "generic")
        state_info = json.dumps(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
                "last_changed": state.last_changed.isoformat(),
                "last_updated": state.last_updated.isoformat(),
            },
            indent=2,
        )

    return {
        "description": f"Setup guide for {entity_id}",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"An entity in Home Assistant appears to be in a problem state "
                        f"and needs troubleshooting.\n\n"
                        f"Entity domain: {domain}\n"
                        f"Device class: {device_class}\n\n"
                        f"Current state:\n```json\n{state_info}\n```\n\n"
                        f"Please provide step-by-step troubleshooting guidance specific to "
                        f"this domain ({domain}) and device class ({device_class}). Include:\n"
                        f"1. Common causes for the current problem state\n"
                        f"2. Step-by-step diagnostic checks\n"
                        f"3. Specific HA configuration items to verify\n"
                        f"4. Integration-specific troubleshooting if applicable\n"
                        f"5. How to verify the fix worked"
                    ),
                },
            }
        ],
    }

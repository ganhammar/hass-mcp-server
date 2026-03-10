"""MCP prompt definitions and handlers for Home Assistant."""

import json
from typing import Any

from homeassistant.core import HomeAssistant

PROMPTS = [
    {
        "name": "troubleshoot_device",
        "description": "Diagnostic prompt for troubleshooting a device or entity",
        "arguments": [
            {
                "name": "entity_id",
                "description": "The entity ID to troubleshoot",
                "required": True,
            }
        ],
    },
    {
        "name": "daily_summary",
        "description": "Summary of all state changes over the last day",
        "arguments": [],
    },
]


def get_prompts() -> list[dict[str, Any]]:
    """Return all prompt definitions."""
    return PROMPTS


async def get_prompt(hass: HomeAssistant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a prompt by name with arguments."""
    if name == "troubleshoot_device":
        return _troubleshoot_device(hass, arguments)

    if name == "daily_summary":
        return await _daily_summary(hass)

    raise ValueError(f"Unknown prompt: {name}")


def _troubleshoot_device(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
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


async def _daily_summary(hass: HomeAssistant) -> dict[str, Any]:
    """Generate a daily summary prompt with recent state changes."""
    from datetime import datetime, timedelta

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states

    end_time = datetime.now()
    start_time = end_time - timedelta(days=1)

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states, hass, start_time, end_time, None
        )

        summary_parts = []
        for entity_id, entity_states in states.items():
            if len(entity_states) > 1:
                changes = len(entity_states) - 1
                current = entity_states[-1].state
                summary_parts.append(f"- {entity_id}: {changes} change(s), currently '{current}'")

        summary_text = (
            "\n".join(sorted(summary_parts)[:100])
            if summary_parts
            else "No significant state changes in the last 24 hours."
        )
    except Exception:
        summary_text = (
            "Unable to retrieve history data. " "The recorder component may not be available."
        )

    return {
        "description": "Daily summary of Home Assistant activity",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Here is a summary of state changes in Home Assistant "
                        f"over the last 24 hours:\n\n"
                        f"{summary_text}\n\n"
                        f"Please provide a concise daily summary highlighting notable changes, "
                        f"any potential issues, and suggestions for automation improvements."
                    ),
                },
            }
        ],
    }

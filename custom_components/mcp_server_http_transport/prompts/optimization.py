"""Optimization and analysis prompts."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import register_prompt

_LOGGER = logging.getLogger(__name__)


@register_prompt(
    name="schedule_optimizer",
    description=(
        "Analyze automation schedules and entity history "
        "to suggest timing or grouping improvements"
    ),
    arguments=[
        {
            "name": "entity_id",
            "description": "Optional entity ID to focus the analysis on",
            "required": False,
        }
    ],
)
async def schedule_optimizer(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a schedule optimization prompt."""
    from ..config_manager import read_list_entries

    entity_id = arguments.get("entity_id")

    try:
        automations = await read_list_entries(hass, "automations.yaml")
        automations_text = json.dumps(automations, indent=2)
    except Exception:
        _LOGGER.exception("Error reading automations for schedule optimizer")
        automations_text = "Unable to read automations.yaml"

    entity_context = ""
    if entity_id:
        state = hass.states.get(entity_id)
        if state:
            entity_context = (
                f"\n**Focus entity:** {entity_id}\n"
                f"Current state: {state.state}\n"
                f"Attributes: {json.dumps(dict(state.attributes), indent=2)}\n"
            )
        else:
            entity_context = f"\n**Focus entity:** {entity_id} (not found)\n"

    return {
        "description": "Schedule optimization analysis",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Analyze my Home Assistant automation schedules and suggest "
                        f"improvements.\n"
                        f"{entity_context}\n"
                        f"**All automations:**\n"
                        f"```json\n{automations_text}\n```\n\n"
                        f"Please analyze:\n"
                        f"1. **Timing**: Are there automations that fire too frequently "
                        f"or at suboptimal times?\n"
                        f"2. **Grouping**: Could any automations be combined or "
                        f"coordinated better?\n"
                        f"3. **Execution order**: Are there dependencies between "
                        f"automations that should be explicit?\n"
                        f"4. **Resource usage**: Are there automations polling too "
                        f"aggressively or firing unnecessarily?\n"
                        f"5. **Suggestions**: Specific timing or structural changes "
                        f"to improve efficiency."
                    ),
                },
            }
        ],
    }


@register_prompt(
    name="naming_conventions",
    description=(
        "Scan entity friendly names and IDs for inconsistent naming conventions "
        "and suggest standardization"
    ),
)
async def naming_conventions(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a naming conventions analysis prompt."""
    by_domain: dict[str, list[dict[str, str]]] = {}
    for state in hass.states.async_all():
        domain = state.entity_id.split(".")[0]
        by_domain.setdefault(domain, []).append(
            {
                "entity_id": state.entity_id,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
            }
        )

    entities_text = json.dumps(by_domain, indent=2)
    total = sum(len(v) for v in by_domain.values())

    return {
        "description": "Naming conventions analysis",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Analyze the naming conventions across my Home Assistant entities "
                        f"({total} entities in {len(by_domain)} domains).\n\n"
                        f"**Entities by domain:**\n"
                        f"```json\n{entities_text}\n```\n\n"
                        f"Please analyze:\n"
                        f"1. **Consistency**: Are entity IDs and friendly names following "
                        f"a consistent pattern within each domain?\n"
                        f"2. **Room/area patterns**: Are locations referenced consistently "
                        f"(e.g., 'living_room' vs 'livingroom' vs 'lr')?\n"
                        f"3. **Abbreviations**: Are there inconsistent abbreviations?\n"
                        f"4. **Friendly names**: Do they follow a readable pattern?\n"
                        f"5. **Suggestions**: Propose a standardized naming scheme and "
                        f"list specific entities that should be renamed."
                    ),
                },
            }
        ],
    }

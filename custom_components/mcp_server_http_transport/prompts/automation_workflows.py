"""Automation workflow prompts: building, debugging, and auditing."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..json_utils import _HAJSONEncoder
from . import register_prompt

_LOGGER = logging.getLogger(__name__)


@register_prompt(
    name="automation_builder",
    description="Step-by-step guided automation creation",
    arguments=[
        {
            "name": "trigger_type",
            "description": (
                "Optional trigger type to focus on "
                "(e.g., time, state, sun, device, event, numeric_state, template, zone)"
            ),
            "required": False,
        }
    ],
)
async def automation_builder(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a guided automation builder prompt."""
    trigger_type = arguments.get("trigger_type", "")

    domains = sorted(set(s.entity_id.split(".")[0] for s in hass.states.async_all()))
    domains_text = ", ".join(domains)

    services = hass.services.async_services()
    service_domains = sorted(services.keys())
    services_text = ", ".join(service_domains)

    trigger_guidance = ""
    if trigger_type:
        trigger_guidance = (
            f"\nThe user wants to use a **{trigger_type}** trigger. "
            f"Focus the trigger step on this type and provide relevant examples.\n"
        )

    return {
        "description": "Guided automation builder",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Help me create a new Home Assistant automation step by step.\n"
                        f"{trigger_guidance}\n"
                        f"Available entity domains: {domains_text}\n"
                        f"Available service domains: {services_text}\n\n"
                        f"Walk me through each step:\n"
                        f"1. **Trigger**: What should start this automation? "
                        f"Help me choose the right trigger platform and configure it.\n"
                        f"2. **Conditions** (optional): Should it only run under certain "
                        f"conditions? Help me define any guards.\n"
                        f"3. **Actions**: What should happen? Help me pick services "
                        f"and targets.\n"
                        f"4. **Mode**: Should it be single, restart, queued, or parallel?\n"
                        f"5. **Review**: Show the final automation YAML config.\n\n"
                        f"Ask me questions at each step to understand what I need."
                    ),
                },
            }
        ],
    }


@register_prompt(
    name="automation_debugger",
    description=(
        "Debug why an automation is not firing or misbehaving. "
        "Fetches config, recent logbook entries, and entity states"
    ),
    arguments=[
        {
            "name": "automation_id",
            "description": "The automation ID to debug",
            "required": True,
        }
    ],
)
async def automation_debugger(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate an automation debugging prompt with context."""
    from ..config_manager import read_list_entry

    automation_id = arguments.get("automation_id", "")

    # Fetch automation config
    try:
        config = await read_list_entry(hass, "automations.yaml", automation_id)
        config_text = json.dumps(config, indent=2, cls=_HAJSONEncoder)
    except Exception:
        _LOGGER.exception("Error reading automation config for '%s'", automation_id)
        config_text = f"Automation with id '{automation_id}' not found in automations.yaml"

    # Fetch automation entity state
    entity_id = f"automation.{automation_id}"
    state = hass.states.get(entity_id)
    if state is None:
        # Try finding by matching automation entities
        for s in hass.states.async_all():
            if s.entity_id.startswith("automation.") and s.attributes.get("id") == automation_id:
                state = s
                entity_id = s.entity_id
                break

    if state is not None:
        state_text = json.dumps(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "last_triggered": str(state.attributes.get("last_triggered", "never")),
                "current_state": state.attributes.get("current", 0),
                "mode": state.attributes.get("mode", "single"),
            },
            indent=2,
            cls=_HAJSONEncoder,
        )
    else:
        state_text = f"Automation entity not found for id '{automation_id}'"

    # Fetch recent logbook entries
    logbook_text = "Logbook data not available"
    try:
        from datetime import timedelta

        from homeassistant.components.logbook.processor import (
            EVENT_LOGBOOK_ENTRY,
            PSEUDO_EVENT_STATE_CHANGED,
            EventProcessor,
        )
        from homeassistant.components.recorder import get_instance
        from homeassistant.util import dt as dt_util

        end_time = dt_util.utcnow()
        start_time = end_time - timedelta(days=1)

        if state is not None:
            processor = EventProcessor(
                hass,
                (PSEUDO_EVENT_STATE_CHANGED, EVENT_LOGBOOK_ENTRY),
                entity_ids=[state.entity_id],
            )
            events = await get_instance(hass).async_add_executor_job(
                processor.get_events, start_time, end_time
            )
            logbook_text = (
                json.dumps(events[:20], indent=2, cls=_HAJSONEncoder)
                if events
                else "No recent events"
            )
    except Exception:
        _LOGGER.debug("Could not fetch logbook entries for automation debug")

    return {
        "description": f"Debug automation {automation_id}",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Help me debug the following Home Assistant automation "
                        f"that is not working as expected.\n\n"
                        f"**Automation configuration:**\n"
                        f"```json\n{config_text}\n```\n\n"
                        f"**Automation entity state:**\n"
                        f"```json\n{state_text}\n```\n\n"
                        f"**Recent logbook entries (last 24h):**\n"
                        f"```json\n{logbook_text}\n```\n\n"
                        f"Please analyze:\n"
                        f"1. Is the automation enabled?\n"
                        f"2. Are the triggers correctly configured?\n"
                        f"3. Could conditions be preventing it from firing?\n"
                        f"4. Are there issues with the actions?\n"
                        f"5. What do the logbook entries reveal?\n"
                        f"6. Suggest specific fixes or diagnostic steps."
                    ),
                },
            }
        ],
    }


@register_prompt(
    name="automation_audit",
    description="Audit all automations for conflicts, redundancies, and common anti-patterns",
)
async def automation_audit(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate an automation audit prompt."""
    from ..config_manager import read_list_entries

    automations = None
    try:
        automations = await read_list_entries(hass, "automations.yaml")
        automations_text = json.dumps(automations, indent=2, cls=_HAJSONEncoder)
    except Exception:
        _LOGGER.exception("Error reading automations for audit")
        automations_text = "Unable to read automations.yaml"

    # Gather automation entity states
    auto_states = []
    for state in hass.states.async_all():
        if state.entity_id.startswith("automation."):
            auto_states.append(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "last_triggered": str(state.attributes.get("last_triggered", "never")),
                }
            )
    states_text = json.dumps(auto_states, indent=2, cls=_HAJSONEncoder)

    return {
        "description": "Audit all automations",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please audit all of my Home Assistant automations.\n\n"
                        f"**Automation configurations "
                        f"({len(automations) if isinstance(automations, list) else '?'}):**\n"
                        f"```json\n{automations_text}\n```\n\n"
                        f"**Automation entity states:**\n"
                        f"```json\n{states_text}\n```\n\n"
                        f"Analyze for:\n"
                        f"1. **Conflicts**: Automations that could interfere with each other\n"
                        f"2. **Redundancies**: Duplicate or overlapping automations\n"
                        f"3. **Anti-patterns**: Missing error handling, missing conditions, "
                        f"unsafe modes\n"
                        f"4. **Disabled automations**: Are any disabled and possibly forgotten?\n"
                        f"5. **Never triggered**: Automations that have never fired\n"
                        f"6. **Suggestions**: Improvements, consolidation opportunities"
                    ),
                },
            }
        ],
    }

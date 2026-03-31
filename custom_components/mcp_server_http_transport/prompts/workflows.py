"""Cross-cutting workflow prompts: dashboard building, change validation, security review."""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import register_prompt

_LOGGER = logging.getLogger(__name__)


@register_prompt(
    name="dashboard_builder",
    description=(
        "Given a set of entities or an area, suggest a Lovelace dashboard layout "
        "with appropriate card types"
    ),
    arguments=[
        {
            "name": "area_id",
            "description": "Area ID to build a dashboard for (optional)",
            "required": False,
        },
        {
            "name": "entity_ids",
            "description": "Comma-separated list of entity IDs (optional)",
            "required": False,
        },
    ],
)
async def dashboard_builder(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a dashboard builder prompt."""
    area_id = arguments.get("area_id")
    entity_ids_str = arguments.get("entity_ids", "")
    entity_ids = (
        [e.strip() for e in entity_ids_str.split(",") if e.strip()] if entity_ids_str else []
    )

    entities_info = []
    area_info = ""

    if area_id:
        area_registry = ar.async_get(hass)
        area = area_registry.async_get_area(area_id)
        if area:
            area_info = f"**Area:** {area.name} (id: {area.id})\n"

        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        for state in hass.states.async_all():
            entry = entity_registry.async_get(state.entity_id)
            entity_area = entry.area_id if entry else None
            if not entity_area and entry and entry.device_id:
                device = device_registry.async_get(entry.device_id)
                entity_area = device.area_id if device else None
            if entity_area == area_id:
                entities_info.append(
                    {
                        "entity_id": state.entity_id,
                        "state": state.state,
                        "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                        "device_class": state.attributes.get("device_class"),
                    }
                )

    if entity_ids:
        for eid in entity_ids:
            state = hass.states.get(eid)
            if state:
                entities_info.append(
                    {
                        "entity_id": state.entity_id,
                        "state": state.state,
                        "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                        "device_class": state.attributes.get("device_class"),
                    }
                )

    if not entities_info:
        entities_text = "No entities found for the given criteria."
    else:
        entities_text = json.dumps(entities_info, indent=2)

    return {
        "description": "Dashboard layout builder",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Help me design a Lovelace dashboard layout for the following "
                        f"Home Assistant entities.\n\n"
                        f"{area_info}"
                        f"**Entities ({len(entities_info)}):**\n"
                        f"```json\n{entities_text}\n```\n\n"
                        f"Please suggest:\n"
                        f"1. **Card types**: Which Lovelace card type is best for each "
                        f"entity (e.g., entities, gauge, thermostat, light, button)?\n"
                        f"2. **Layout**: How to organize cards into logical groups\n"
                        f"3. **Views**: Should this be one view or multiple?\n"
                        f"4. **Special cards**: Any conditional, picture-elements, "
                        f"or custom cards that would enhance the dashboard?\n"
                        f"5. **YAML config**: Provide the complete Lovelace YAML config "
                        f"ready to use with save_dashboard_config."
                    ),
                },
            }
        ],
    }


@register_prompt(
    name="change_validator",
    description=(
        "After creating or modifying automations, scripts, or scenes, "
        "review the changes and validate configuration before restarting"
    ),
    arguments=[
        {
            "name": "config_type",
            "description": (
                "Type of configuration to validate: automation, script, or scene (optional)"
            ),
            "required": False,
        }
    ],
)
async def change_validator(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a change validation prompt."""
    from ..config_manager import read_dict_entries, read_list_entries

    config_type = arguments.get("config_type")
    sections = []

    types_to_check = [config_type] if config_type else ["automation", "scene", "script"]

    for ct in types_to_check:
        try:
            if ct == "script":
                entries = await read_dict_entries(hass, "scripts.yaml")
                sections.append(
                    f"**Scripts ({len(entries)}):**\n```json\n{json.dumps(entries, indent=2)}\n```"
                )
            elif ct == "automation":
                entries = await read_list_entries(hass, "automations.yaml")
                sections.append(
                    f"**Automations ({len(entries)}):**\n"
                    f"```json\n{json.dumps(entries, indent=2)}\n```"
                )
            elif ct == "scene":
                entries = await read_list_entries(hass, "scenes.yaml")
                sections.append(
                    f"**Scenes ({len(entries)}):**\n"
                    f"```json\n{json.dumps(entries, indent=2)}\n```"
                )
        except Exception:
            _LOGGER.debug("Could not read %s config for change validator", ct)
            sections.append(f"**{ct.title()}s:** Unable to read configuration")

    # Try config validation
    config_check = ""
    try:
        from homeassistant.helpers.check_config import async_check_ha_config_file

        res = await async_check_ha_config_file(hass)
        errors = [str(err) for err in res.errors] if res.errors else []
        if errors:
            config_check = "\n**Configuration validation errors:**\n" + "\n".join(
                f"- {e}" for e in errors
            )
        else:
            config_check = "\n**Configuration validation:** Passed (no errors)"
    except Exception:
        config_check = "\n**Configuration validation:** Unable to run check"

    configs_text = "\n\n".join(sections)

    return {
        "description": "Pre-flight change validation",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please review the current Home Assistant configuration "
                        f"as a pre-flight check before restarting.\n\n"
                        f"{configs_text}\n"
                        f"{config_check}\n\n"
                        f"Check for:\n"
                        f"1. **Syntax errors**: Invalid YAML or missing required fields\n"
                        f"2. **Entity references**: Do referenced entities exist?\n"
                        f"3. **Service calls**: Are service domains and names valid?\n"
                        f"4. **Logic issues**: Conditions that can never be true, "
                        f"missing error handling\n"
                        f"5. **Breaking changes**: Anything that could cause issues "
                        f"after restart\n"
                        f"6. **Recommendation**: Is it safe to restart?"
                    ),
                },
            }
        ],
    }


@register_prompt(
    name="security_review",
    description=(
        "Scan for entities exposed externally, integrations with known issues, "
        "or insecure configurations"
    ),
)
async def security_review(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a security review prompt."""
    # HA config
    config = hass.config
    config_data = {
        "external_url": getattr(config, "external_url", None),
        "internal_url": getattr(config, "internal_url", None),
    }
    config_text = json.dumps(config_data, indent=2)

    # Integrations
    entries = hass.config_entries.async_entries()
    integrations = [
        {
            "domain": entry.domain,
            "title": entry.title,
            "state": str(entry.state),
        }
        for entry in entries
    ]
    integrations_text = json.dumps(integrations, indent=2)

    # Sensitive entity domains
    sensitive_domains = {"camera", "lock", "alarm_control_panel", "cover"}
    sensitive_entities = []
    for state in hass.states.async_all():
        domain = state.entity_id.split(".")[0]
        if domain in sensitive_domains:
            sensitive_entities.append(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                }
            )
    sensitive_text = json.dumps(sensitive_entities, indent=2)

    return {
        "description": "Security review",
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please perform a security review of my Home Assistant setup.\n\n"
                        f"**Configuration:**\n```json\n{config_text}\n```\n\n"
                        f"**Installed integrations ({len(integrations)}):**\n"
                        f"```json\n{integrations_text}\n```\n\n"
                        f"**Sensitive entities ({len(sensitive_entities)}):**\n"
                        f"```json\n{sensitive_text}\n```\n\n"
                        f"Please review:\n"
                        f"1. **External exposure**: Is HA exposed to the internet? "
                        f"Are sensitive entities accessible remotely?\n"
                        f"2. **Integration health**: Are all integrations in a healthy state? "
                        f"Any with known security issues?\n"
                        f"3. **Sensitive entities**: Are cameras, locks, and alarms "
                        f"properly secured?\n"
                        f"4. **Authentication**: Any concerns about the auth setup?\n"
                        f"5. **Recommendations**: Specific steps to improve security."
                    ),
                },
            }
        ],
    }

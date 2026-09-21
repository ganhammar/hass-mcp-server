"""Reporting and summary prompts."""

import logging
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import InvalidPromptArguments, register_prompt

_LOGGER = logging.getLogger(__name__)


def _parse_timestamp(name: str, value: Any) -> datetime:
    """Parse an ISO 8601 argument, reporting an unusable one as the caller's error."""
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise InvalidPromptArguments(
            f"Argument '{name}' must be an ISO 8601 timestamp "
            f"(e.g., 2024-01-01T00:00:00), got {value!r}"
        ) from err


@register_prompt(
    name="daily_summary",
    description="Summary of all state changes over the last day",
)
async def daily_summary(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate a daily summary prompt with recent state changes."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states

    end_time = dt_util.utcnow()
    start_time = end_time - timedelta(days=1)

    # The recorder refuses a query across every entity, so the state machine
    # supplies the list. An entity that has no state has no history either.
    entity_ids = [state.entity_id for state in hass.states.async_all()]

    # This prompt takes no arguments, so a client that enumerates prompts at
    # connect time runs it on every connect. The summary reads only the row
    # count and the last state, so the attributes join and the start-of-window
    # state, the expensive parts of the query, are left out.
    query = partial(
        get_significant_states,
        hass,
        start_time,
        end_time,
        entity_ids,
        include_start_time_state=False,
        no_attributes=True,
    )

    try:
        states = await get_instance(hass).async_add_executor_job(query) if entity_ids else {}

        summary_parts = []
        for entity_id, entity_states in states.items():
            if entity_states:
                changes = len(entity_states)
                current = entity_states[-1].state
                summary_parts.append(f"- {entity_id}: {changes} change(s), currently '{current}'")

        summary_text = (
            "\n".join(sorted(summary_parts)[:100])
            if summary_parts
            else "No significant state changes in the last 24 hours."
        )
    except Exception:
        _LOGGER.exception("Error retrieving history for daily summary")
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


@register_prompt(
    name="energy_report",
    description="Summarize energy consumption data over a time range",
    arguments=[
        {
            "name": "start_time",
            "description": "Start time in ISO format (e.g., 2024-01-01T00:00:00)",
            "required": True,
        },
        {
            "name": "end_time",
            "description": "End time in ISO format (optional, defaults to now)",
            "required": False,
        },
    ],
)
async def energy_report(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Generate an energy consumption report prompt."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states

    start_time = _parse_timestamp("start_time", arguments["start_time"])
    end_time_str = arguments.get("end_time")
    end_time = _parse_timestamp("end_time", end_time_str) if end_time_str else dt_util.utcnow()

    energy_device_classes = {"energy", "power", "gas"}
    energy_units = {"kWh", "Wh", "W", "m\u00b3"}

    energy_entities = []
    for state in hass.states.async_all():
        attrs = state.attributes
        device_class = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")
        if device_class in energy_device_classes or unit in energy_units:
            energy_entities.append(state.entity_id)

    if not energy_entities:
        summary_text = "No energy-related entities found in Home Assistant."
    else:
        try:
            states = await get_instance(hass).async_add_executor_job(
                get_significant_states,
                hass,
                start_time,
                end_time,
                energy_entities,
            )

            parts = []
            for eid, entity_states in states.items():
                if entity_states:
                    current = entity_states[-1]
                    parts.append(
                        f"- {eid} ({current.attributes.get('friendly_name', eid)}): "
                        f"{len(entity_states)} readings, "
                        f"current: {current.state} "
                        f"{current.attributes.get('unit_of_measurement', '')}"
                    )
            summary_text = "\n".join(sorted(parts)) if parts else "No energy data recorded."
        except Exception:
            _LOGGER.exception("Error retrieving energy history data")
            summary_text = "Unable to retrieve energy history data."

    return {
        "description": (f"Energy report from {start_time.isoformat()} to {end_time.isoformat()}"),
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Here is energy sensor data from Home Assistant for the period "
                        f"{start_time.isoformat()} to {end_time.isoformat()}:\n\n"
                        f"{summary_text}\n\n"
                        f"Please provide an energy consumption summary including:\n"
                        f"1. Total and peak consumption patterns\n"
                        f"2. Notable increases or decreases\n"
                        f"3. Suggestions for reducing energy usage\n"
                        f"4. Any anomalies that might indicate problems"
                    ),
                },
            }
        ],
    }

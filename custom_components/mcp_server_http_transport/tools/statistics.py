"""Long-term statistics tools."""

import json
import logging
from datetime import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import _HAJSONEncoder, register_tool

_LOGGER = logging.getLogger(__name__)

_VALID_PERIODS = {"5minute", "hour", "day", "week", "month"}


@register_tool(
    name="get_statistics",
    description=(
        "Fetch long-term statistics (energy, climate, etc.) for an entity. "
        "Different from get_history which only covers short-term state changes. "
        "This returns aggregated data (mean, min, max, sum) that powers the Energy dashboard "
        "and is useful for trend analysis"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID to get statistics for",
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format (e.g., 2024-01-01T00:00:00)",
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format (optional, defaults to now)",
            },
            "period": {
                "type": "string",
                "description": (
                    "Aggregation period: 5minute, hour, day, week, or month (default: hour)"
                ),
            },
        },
        "required": ["entity_id", "start_time"],
    },
)
async def get_statistics(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Fetch long-term statistics for an entity."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    entity_id = arguments["entity_id"]
    start_time = dt.fromisoformat(arguments["start_time"])
    end_time_str = arguments.get("end_time")
    end_time = dt.fromisoformat(end_time_str) if end_time_str else dt_util.utcnow()
    period = arguments.get("period", "hour")

    if period not in _VALID_PERIODS:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Invalid period '{period}'. "
                        f"Must be one of: {', '.join(sorted(_VALID_PERIODS))}"
                    ),
                }
            ]
        }

    try:
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start_time,
            end_time,
            {entity_id},
            period,
            None,
            {"mean", "min", "max", "sum", "state"},
        )

        entity_stats = stats.get(entity_id, [])
        result = []
        for stat in entity_stats:
            entry = {}
            for key in ("start", "end", "mean", "min", "max", "sum", "state"):
                if key in stat and stat[key] is not None:
                    entry[key] = stat[key]
            result.append(entry)

        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, cls=_HAJSONEncoder)}]
        }
    except Exception as e:
        _LOGGER.error("Error getting statistics: %s", e)
        return {"content": [{"type": "text", "text": f"Error getting statistics: {str(e)}"}]}

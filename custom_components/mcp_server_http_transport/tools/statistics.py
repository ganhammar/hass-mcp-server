"""Long-term statistics tools."""

import asyncio
import json
import logging
from datetime import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_READ_ONLY,
    _HAJSONEncoder,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)

_VALID_PERIODS = {"5minute", "hour", "day", "week", "month"}
_VALID_STATISTIC_TYPES = {"mean", "sum"}

# clear_statistics posts a job to the recorder's queue and does not block; we wait
# this many seconds for it to finish before reporting a timeout. Module-level so
# tests can patch it to something small.
_CLEAR_STATISTICS_TIMEOUT = 10


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
    annotations=ANNOTATION_READ_ONLY,
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


@register_tool(
    name="list_statistic_ids",
    description=(
        "List the statistic IDs the recorder tracks, with their metadata: source, unit, "
        "and whether each carries a mean and/or a sum. Use this to discover which entities "
        "have long-term statistics and to inspect one before adjusting or clearing it. "
        "A statistic ID is usually an entity ID (e.g. sensor.energy) but external "
        "statistics use a colon (e.g. tibber:energy_consumption)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "statistic_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of statistic IDs to restrict the result to",
            },
            "statistic_type": {
                "type": "string",
                "description": "Optional filter by kind: 'mean' or 'sum'",
            },
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_statistic_ids(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List statistic IDs and their metadata."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        list_statistic_ids as recorder_list_statistic_ids,
    )

    statistic_type = arguments.get("statistic_type")
    if statistic_type is not None and statistic_type not in _VALID_STATISTIC_TYPES:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Invalid statistic_type '{statistic_type}'. "
                        f"Must be one of: {', '.join(sorted(_VALID_STATISTIC_TYPES))}"
                    ),
                }
            ]
        }

    ids = arguments.get("statistic_ids")
    id_set = set(ids) if ids else None

    try:
        metadata = await get_instance(hass).async_add_executor_job(
            recorder_list_statistic_ids, hass, id_set, statistic_type
        )
        return {
            "content": [
                {"type": "text", "text": json.dumps(metadata, indent=2, cls=_HAJSONEncoder)}
            ]
        }
    except Exception as e:
        _LOGGER.error("Error listing statistic IDs: %s", e)
        return {"content": [{"type": "text", "text": f"Error listing statistic IDs: {str(e)}"}]}


@register_tool(
    name="validate_statistics",
    description=(
        "Report statistics issues the recorder has detected — the same list the "
        "Developer Tools > Statistics page surfaces for 'Fix issues'. Flags problems like a "
        "changed unit of measurement, a state class that no longer supports statistics, or an "
        "entity that no longer exists. Use this to diagnose why a statistic looks wrong before "
        "adjusting or clearing it. Returns only the statistic IDs that have issues; an empty "
        "result means none were found."
    ),
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_READ_ONLY,
)
async def validate_statistics(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Report detected statistics issues."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        validate_statistics as recorder_validate_statistics,
    )

    try:
        issues = await get_instance(hass).async_add_executor_job(recorder_validate_statistics, hass)
        result = {
            statistic_id: [issue.as_dict() for issue in issue_list]
            for statistic_id, issue_list in issues.items()
        }
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, cls=_HAJSONEncoder)}]
        }
    except Exception as e:
        _LOGGER.error("Error validating statistics: %s", e)
        return {"content": [{"type": "text", "text": f"Error validating statistics: {str(e)}"}]}


@register_tool(
    name="adjust_statistics",
    description=(
        "Correct the running sum of a statistic from a point in time onward — the equivalent "
        "of Developer Tools > Statistics > 'Adjust a statistic'. Use this to fix a spike or a "
        "bad reset in an accumulating sensor (energy, water, gas) without wiping its history. "
        "Only works on statistics that carry a sum. The adjustment is expressed in the "
        "statistic's own unit unless adjustment_unit is given, and it must match that unit. "
        "The correction is queued on the recorder and applied asynchronously, so a malformed "
        "request is reported here but the write itself is not confirmed back."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "statistic_id": {
                "type": "string",
                "description": "The statistic ID to adjust (e.g. sensor.energy)",
            },
            "start_time": {
                "type": "string",
                "description": (
                    "ISO timestamp; the sum is adjusted for this period and every later one"
                ),
            },
            "adjustment": {
                "type": "number",
                "description": "Amount to add to the sum (negative to subtract)",
            },
            "adjustment_unit": {
                "type": "string",
                "description": (
                    "Unit of the adjustment; defaults to the statistic's own unit and must "
                    "match it"
                ),
            },
        },
        "required": ["statistic_id", "start_time", "adjustment"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def adjust_statistics(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Adjust the sum of a statistic from a point in time onward."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        list_statistic_ids as recorder_list_statistic_ids,
    )

    statistic_id = arguments["statistic_id"]
    adjustment = arguments["adjustment"]

    start_time = dt_util.parse_datetime(arguments["start_time"])
    if start_time is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Invalid start_time '{arguments['start_time']}'. Use ISO format.",
                }
            ]
        }
    start_time = dt_util.as_utc(start_time)

    try:
        instance = get_instance(hass)
        metadatas = await instance.async_add_executor_job(
            recorder_list_statistic_ids, hass, {statistic_id}, None
        )
    except Exception as e:
        _LOGGER.error("Error adjusting statistics: %s", e)
        return {"content": [{"type": "text", "text": f"Error adjusting statistics: {str(e)}"}]}

    if not metadatas:
        return {"content": [{"type": "text", "text": f"Unknown statistic ID '{statistic_id}'."}]}
    metadata = metadatas[0]

    if not metadata.get("has_sum"):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Statistic '{statistic_id}' has no sum to adjust. "
                        "adjust_statistics only applies to accumulating statistics."
                    ),
                }
            ]
        }

    stat_unit = metadata.get("statistics_unit_of_measurement")
    adjustment_unit = arguments.get("adjustment_unit", stat_unit)
    if adjustment_unit != stat_unit:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"adjustment_unit '{adjustment_unit}' must match the statistic's unit "
                        f"'{stat_unit}'. Unit conversion is not supported; express the "
                        "adjustment in the statistic's own unit."
                    ),
                }
            ]
        }

    try:
        instance.async_adjust_statistics(statistic_id, start_time, adjustment, adjustment_unit)
    except Exception as e:
        _LOGGER.error("Error adjusting statistics: %s", e)
        return {"content": [{"type": "text", "text": f"Error adjusting statistics: {str(e)}"}]}

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Queued an adjustment of {adjustment} {adjustment_unit} to '{statistic_id}' "
                    f"from {start_time.isoformat()}. The recorder applies it asynchronously."
                ),
            }
        ]
    }


@register_tool(
    name="clear_statistics",
    description=(
        "Permanently delete all long-term and short-term statistics for the given statistic "
        "IDs. Use this to remove a corrupted or orphaned statistic so it can start clean. This "
        "is irreversible and there is no backup — the history is gone. Requires confirm=true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "statistic_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Statistic IDs whose statistics should be deleted",
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true to confirm this irreversible deletion",
            },
        },
        "required": ["statistic_ids", "confirm"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def clear_statistics(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete all statistics for the given statistic IDs."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        list_statistic_ids as recorder_list_statistic_ids,
    )

    if arguments.get("confirm") is not True:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "clear_statistics requires confirm=true. This permanently deletes the "
                        "statistics for the given IDs and cannot be undone."
                    ),
                }
            ]
        }

    statistic_ids = arguments["statistic_ids"]
    if not isinstance(statistic_ids, list) or not all(isinstance(s, str) for s in statistic_ids):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "statistic_ids must be an array of statistic ID strings.",
                }
            ]
        }
    if not statistic_ids:
        return {"content": [{"type": "text", "text": "statistic_ids must not be empty."}]}

    # Verify every ID exists before deleting anything, so a typo can't be reported as a
    # successful clear of history that never existed. This is irreversible.
    try:
        instance = get_instance(hass)
        metadatas = await instance.async_add_executor_job(
            recorder_list_statistic_ids, hass, set(statistic_ids), None
        )
    except Exception as e:
        _LOGGER.error("Error clearing statistics: %s", e)
        return {"content": [{"type": "text", "text": f"Error clearing statistics: {str(e)}"}]}

    known = {metadata.get("statistic_id") for metadata in metadatas}
    unknown = [statistic_id for statistic_id in statistic_ids if statistic_id not in known]
    if unknown:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Unknown statistic ID(s): {', '.join(unknown)}. Nothing was cleared."
                    ),
                }
            ]
        }

    done = asyncio.Event()

    def _on_done() -> None:
        hass.loop.call_soon_threadsafe(done.set)

    try:
        instance.async_clear_statistics(statistic_ids, on_done=_on_done)
        async with asyncio.timeout(_CLEAR_STATISTICS_TIMEOUT):
            await done.wait()
    except TimeoutError:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "clear_statistics timed out waiting for the recorder. The deletion may "
                        "still complete in the background."
                    ),
                }
            ]
        }
    except Exception as e:
        _LOGGER.error("Error clearing statistics: %s", e)
        return {"content": [{"type": "text", "text": f"Error clearing statistics: {str(e)}"}]}

    return {
        "content": [
            {
                "type": "text",
                "text": f"Cleared statistics for: {', '.join(statistic_ids)}",
            }
        ]
    }

"""Automation and script trace tools (read-only execution-path inspection).

Traces are Home Assistant's record of *why* an automation or script did (or did
not) do something on a given run: which trigger fired, which conditions passed or
failed, and the variable values at each step. They are the primary tool for
diagnosing "this automation didn't behave the way I expected" — far more precise
than inferring cause from the logbook.

Home Assistant exposes traces over its websocket API. This module reaches the same
data in-process through `homeassistant.components.trace.util`, so no websocket
round-trip is involved.

Two facts about how HA keys traces shape these tools:

  * A trace is stored under ``f"{domain}.{item_id}"`` where ``item_id`` is the
    entity's *registry unique_id*, not its entity-id slug. For a UI automation
    that unique_id is the numeric ``id:`` from the config; for scripts it is
    usually the object-id. Callers should not have to know this, so both tools
    accept the natural ``entity_id`` (``automation.morning``) and resolve it to
    the trace key via the entity registry.
  * Only automations that have an ``id:`` are traced at all, and only a bounded
    number of recent runs are retained (``stored_traces``, default 5), cleared on
    restart. So "no traces found" is a normal, expected answer, and its message
    says why rather than reading as an error.
"""

import json
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import ExtendedJSONEncoder
from homeassistant.util import dt as dt_util

from . import (
    ANNOTATION_READ_ONLY,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)

_TRACE_DOMAINS = ("automation", "script")

# Traces carry Context objects and per-step TraceElement payloads that the plain
# datetime encoder can't handle. ExtendedJSONEncoder is the same encoder HA's own
# trace/get websocket handler uses for exactly this data.
_TRACE_ENCODER = ExtendedJSONEncoder

# Sorts before any real trace, so missing/unparseable timestamps land last under
# newest-first ordering. Timezone-aware so it stays comparable with real timestamps.
_MIN_TS = datetime.min.replace(tzinfo=dt_util.UTC)


def _resolve_trace_key(hass: HomeAssistant, entity_id: str) -> tuple[str, str]:
    """Map an entity_id to its (domain, trace_key), or raise ValueError.

    The trace key is ``f"{domain}.{unique_id}"``. We look the unique_id up in the
    entity registry because that is what HA stores the trace under. If the entity
    is not registered (a legacy YAML item), we fall back to the object-id — which
    is correct for most scripts and harmless for automations, since an untraced
    automation simply yields "no traces found" whatever key we try.
    """
    if "." not in entity_id:
        raise ValueError(
            f"'{entity_id}' is not a valid entity_id — expected e.g. "
            "'automation.morning' or 'script.bedtime'"
        )
    domain, object_id = entity_id.split(".", 1)
    if domain not in _TRACE_DOMAINS:
        raise ValueError(
            f"Traces exist only for automations and scripts, not '{domain}'. "
            f"Pass an automation.* or script.* entity_id"
        )
    entry = er.async_get(hass).async_get(entity_id)
    item_id = entry.unique_id if entry and entry.unique_id else object_id
    return domain, f"{domain}.{item_id}"


def _sort_key(summary: dict[str, Any]) -> datetime:
    """Return a comparable start timestamp for sorting traces.

    Live traces carry a tz-aware ``datetime``; traces restored from the store
    after a restart carry an ISO ``str``. A single key can hold both at once, so
    every start is normalized to a tz-aware datetime — otherwise ``sorted`` would
    raise on a mixed str/datetime list the moment an item runs both before and
    after a restart.
    """
    start = (summary.get("timestamp") or {}).get("start")
    if isinstance(start, str):
        start = dt_util.parse_datetime(start)
    if not isinstance(start, datetime):
        return _MIN_TS
    return start if start.tzinfo is not None else start.replace(tzinfo=dt_util.UTC)


def _summarize_step(element: Any) -> Any:
    """Trim one trace step to its diagnostic skeleton.

    Keeps path, timestamp, error, result and child_id — the trail that shows what
    ran and what a condition evaluated to — but replaces the (potentially huge)
    per-step variable snapshot with just the names of the variables that changed,
    so the caller can decide whether the full trace is worth fetching.
    """
    if not isinstance(element, dict):
        return element
    trimmed = {k: v for k, v in element.items() if k != "changed_variables"}
    changed = element.get("changed_variables")
    if isinstance(changed, dict) and changed:
        trimmed["changed_variables_keys"] = sorted(changed)
    return trimmed


def _attach_entity_ids(
    hass: HomeAssistant, traces: list[dict[str, Any]], known_entity_id: str | None
) -> list[dict[str, Any]]:
    """Add an ``entity_id`` to each summary so a domain-wide listing is actionable.

    A summary carries ``item_id`` (the registry unique_id), but get_trace takes an
    entity_id, so without this a caller who lists a whole domain can't drill into a
    result. We reverse-resolve item_id -> entity_id via the registry (``entity_id``
    is None for the rare unregistered YAML item). When the caller already named an
    entity_id, every summary is for it, so we skip the lookup.

    The dicts are copied first: for a stopped trace ``as_short_dict`` returns HA's
    own cached dict, and mutating it would corrupt the trace store.
    """
    if not traces:
        return []
    registry = er.async_get(hass)
    cache: dict[tuple[str, str], str | None] = {}
    enriched: list[dict[str, Any]] = []
    for summary in traces:
        item = dict(summary)
        if known_entity_id is not None:
            item["entity_id"] = known_entity_id
        else:
            domain = item.get("domain")
            item_id = item.get("item_id")
            if domain and item_id is not None:
                ck = (domain, item_id)
                if ck not in cache:
                    cache[ck] = registry.async_get_entity_id(domain, domain, item_id)
                item["entity_id"] = cache[ck]
            else:
                item["entity_id"] = None
        enriched.append(item)
    return enriched


def _summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Return an outline of an extended trace: the step skeleton without the bulk.

    Drops the top-level ``config`` and ``blueprint_inputs`` (available in full via
    get_automation_config / get_script_config) and each step's ``changed_variables``
    body, which together are the dominant size of a large trace.
    """
    summary = {k: v for k, v in trace.items() if k not in ("config", "blueprint_inputs")}
    steps = trace.get("trace")
    if isinstance(steps, dict):
        summary["trace"] = {
            path: [_summarize_step(el) for el in elements] for path, elements in steps.items()
        }
    return summary


@register_tool(
    name="list_traces",
    description=(
        "List recent execution traces for automations or scripts, newest first. "
        "Each trace summary shows the run's state (e.g. stopped/error), start/finish "
        "timestamps, the last step reached, why the script execution ended, any error, "
        "and both the entity_id and a run_id to pass to get_trace for the full "
        "step-by-step detail. "
        "Provide entity_id (e.g. 'automation.morning') to trace one item, or domain "
        "('automation' or 'script') to see recent runs across all items in that domain. "
        "Note: only automations that have an id are traced, and only the last few runs "
        "are kept (cleared on restart), so an empty list is normal."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": (
                    "Automation or script to list traces for, e.g. 'automation.morning' "
                    "or 'script.bedtime'. Omit to list across a whole domain instead."
                ),
            },
            "domain": {
                "type": "string",
                "enum": list(_TRACE_DOMAINS),
                "description": (
                    "List recent traces across all items in this domain. Used when "
                    "entity_id is not given (e.g. 'what automations errored recently?')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum traces to return, newest first (default: 20, minimum: 1)",
            },
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_traces(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List trace summaries for an entity or a whole domain, newest first."""
    from homeassistant.components.trace.util import async_list_traces

    entity_id = arguments.get("entity_id")
    domain = arguments.get("domain")
    limit = arguments.get("limit", 20)

    if not isinstance(limit, int) or limit < 1:
        return {"content": [{"type": "text", "text": "Error: limit must be an integer >= 1"}]}

    try:
        if entity_id:
            resolved_domain, key = _resolve_trace_key(hass, entity_id)
            if domain and domain != resolved_domain:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Error: domain '{domain}' does not match entity_id "
                                f"'{entity_id}' (domain '{resolved_domain}')"
                            ),
                        }
                    ]
                }
            domain = resolved_domain
        elif domain:
            if domain not in _TRACE_DOMAINS:
                return {
                    "content": [
                        {"type": "text", "text": f"Error: domain must be one of {_TRACE_DOMAINS}"}
                    ]
                }
            key = None
        else:
            return {
                "content": [
                    {"type": "text", "text": "Error: provide either 'entity_id' or 'domain'"}
                ]
            }

        traces = await async_list_traces(hass, domain, key)
        traces = sorted(traces, key=_sort_key, reverse=True)[:limit]
        traces = _attach_entity_ids(hass, traces, entity_id)
        return {
            "content": [{"type": "text", "text": json.dumps(traces, indent=2, cls=_TRACE_ENCODER)}]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}
    except Exception as e:
        _LOGGER.error("Error listing traces: %s", e)
        return {"content": [{"type": "text", "text": f"Error listing traces: {e}"}]}


@register_tool(
    name="get_trace",
    description=(
        "Get the full execution trace of a single automation or script run — the "
        "step-by-step path (trigger, each condition, each action) with the variable "
        "values, results, and any error captured at each step, plus the config and "
        "trigger context. This is the tool for diagnosing why an automation fired, "
        "didn't fire, or took the wrong branch. "
        "Pass entity_id (e.g. 'automation.morning'); omit run_id to get the most recent "
        "run, or pass a run_id from list_traces for a specific one. "
        "For a large or deeply-nested automation, start with summary=true to get the "
        "step skeleton (paths, results, errors) without the bulky per-step variable "
        "snapshots and config, then call again in full for the step you care about."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": (
                    "Automation or script, e.g. 'automation.morning' or 'script.bedtime'"
                ),
            },
            "run_id": {
                "type": "string",
                "description": (
                    "Specific run to fetch (from list_traces). Omit for the most recent run."
                ),
            },
            "summary": {
                "type": "boolean",
                "description": (
                    "Return an outline instead of the full trace (default: false): the "
                    "per-step path, timestamp, result and error, and the names of the "
                    "variables that changed at each step, but without their full values, "
                    "the config, or blueprint inputs. Use for large traces."
                ),
            },
        },
        "required": ["entity_id"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_trace(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the extended trace for a run, defaulting to the most recent."""
    from homeassistant.components.trace.util import async_get_trace, async_list_traces

    entity_id = arguments["entity_id"]
    run_id = arguments.get("run_id")

    _no_traces = (
        f"No traces found for '{entity_id}'. Traces are only kept for recent runs "
        "(cleared on restart), and automations are only traced if they have an id — "
        "so this is expected if it hasn't run recently."
    )

    try:
        domain, key = _resolve_trace_key(hass, entity_id)

        # List first — it validates that the run exists (so an unknown run_id gives a
        # clear message instead of a KeyError) and lets a genuinely broken trace store
        # surface as an error rather than being mistaken for "no traces".
        summaries = await async_list_traces(hass, domain, key)
        if not summaries:
            return {"content": [{"type": "text", "text": _no_traces}]}

        if run_id:
            if run_id not in {s.get("run_id") for s in summaries}:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"No trace found for '{entity_id}' with run_id '{run_id}'. "
                                "Use list_traces to see available run_ids."
                            ),
                        }
                    ]
                }
        else:
            run_id = max(summaries, key=_sort_key)["run_id"]

        trace = await async_get_trace(hass, key, run_id)
        if arguments.get("summary", False):
            trace = _summarize_trace(trace)
        return {
            "content": [{"type": "text", "text": json.dumps(trace, indent=2, cls=_TRACE_ENCODER)}]
        }
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}
    except Exception as e:
        _LOGGER.error("Error getting trace: %s", e)
        return {"content": [{"type": "text", "text": f"Error getting trace: {e}"}]}

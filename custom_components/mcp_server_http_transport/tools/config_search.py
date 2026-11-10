"""Search the stored configuration of automations, scripts, scenes and dashboards.

Each kind is read the way its own read tools read it: automations.yaml, scripts.yaml
and scenes.yaml through config_manager, dashboards through dashboard_manager. A kind
is searched only while every tool that returns its config is available, so a search
never shows what read-only mode or the disabled tools withhold from those tools.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant

from ..json_patch import format_pointer
from . import ANNOTATION_READ_ONLY, dumps, register_tool, tool_unavailable_reason

# The tools that return each kind's stored config, in the order results are listed.
KIND_TOOLS: dict[str, tuple[str, ...]] = {
    "automation": ("list_automations", "get_automation_config"),
    "script": ("list_scripts", "get_script_config"),
    "scene": ("list_scenes", "get_scene_config"),
    "dashboard": ("get_dashboard_config",),
}

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_MATCHES_PER_ITEM = 5
MAX_MATCHES_PER_ITEM = 100

# A matched value longer than this is cut to a window around the match.
SNIPPET_LENGTH = 80

# (id, name, config) of one automation, script, scene or dashboard.
_Item = tuple[Any, Any, Any]
# The items of one kind, and the ones among them that could not be read with the error.
_Read = tuple[list[_Item], dict[str, str]]


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


async def _automations(hass: HomeAssistant) -> _Read:
    from ..config_manager import read_list_entries

    entries = await read_list_entries(hass, "automations.yaml")
    return [(e.get("id"), e.get("alias"), e) for e in entries if isinstance(e, dict)], {}


async def _scripts(hass: HomeAssistant) -> _Read:
    from ..config_manager import read_dict_entries

    entries = await read_dict_entries(hass, "scripts.yaml")
    return [
        (key, config.get("alias") if isinstance(config, dict) else None, config)
        for key, config in entries.items()
    ], {}


async def _scenes(hass: HomeAssistant) -> _Read:
    from ..config_manager import read_list_entries

    entries = await read_list_entries(hass, "scenes.yaml")
    return [(e.get("id"), e.get("name"), e) for e in entries if isinstance(e, dict)], {}


async def _dashboards(hass: HomeAssistant) -> _Read:
    from ..dashboard_manager import read_storage_dashboards

    dashboards, failed = await read_storage_dashboards(hass)
    return dashboards, {f"dashboard {url_path}": error for url_path, error in failed.items()}


_READERS: dict[str, Callable[[HomeAssistant], Awaitable[_Read]]] = {
    "automation": _automations,
    "script": _scripts,
    "scene": _scenes,
    "dashboard": _dashboards,
}


class _Matches:
    """Every match in one item counted, and the first few kept."""

    def __init__(self, needle: str, cap: int) -> None:
        self.needle = needle
        self.cap = cap
        self.count = 0
        self.shown: list[dict[str, str]] = []

    def add(self, tokens: list[str], field: str, value: str) -> None:
        self.count += 1
        if len(self.shown) < self.cap:
            self.shown.append({"path": format_pointer(tokens), field: value})


def _snippet(value: str, needle: str) -> str:
    """Return the value whole if it is short, else a window around the first match."""
    if len(value) <= SNIPPET_LENGTH:
        return value
    text = " ".join(value.split())
    if len(text) <= SNIPPET_LENGTH:
        return text
    at = max(text.lower().find(needle), 0)
    lead = max(0, (SNIPPET_LENGTH - len(needle)) // 2)
    start = max(0, min(at - lead, len(text) - SNIPPET_LENGTH))
    end = start + SNIPPET_LENGTH
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _walk(node: Any, tokens: list[str], ancestors: set[int], matches: _Matches) -> None:
    """Record every string value and mapping key under node that contains the needle.

    A YAML anchor can make a structure contain itself, so a container already on
    the path being walked is not entered again.
    """
    if isinstance(node, str):
        if matches.needle in node.lower():
            matches.add(tokens, "value", _snippet(node, matches.needle))
        return
    if isinstance(node, dict):
        children: Any = node.items()
    elif isinstance(node, list):
        children = enumerate(node)
    else:
        return
    if id(node) in ancestors:
        return
    ancestors.add(id(node))
    for key, child in children:
        path = [*tokens, str(key)]
        if isinstance(node, dict) and matches.needle in str(key).lower():
            matches.add(path, "key", str(key))
        _walk(child, path, ancestors, matches)
    ancestors.discard(id(node))


def _bounded_int(arguments: dict[str, Any], field: str, default: int, maximum: int) -> int:
    value = arguments.get(field)
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be an integer between 1 and {maximum}")
    return value


def _kinds(arguments: dict[str, Any]) -> list[str]:
    value = arguments.get("types")
    if value is None:
        return list(KIND_TOOLS)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(kind, str) and kind in KIND_TOOLS for kind in value)
    ):
        raise ValueError(f"types must be a non-empty list of: {', '.join(KIND_TOOLS)}")
    return [kind for kind in KIND_TOOLS if kind in value]


@register_tool(
    name="search_config",
    description=(
        "Find where something is referenced before changing it: searches the stored config "
        "of automations, scripts and scenes (automations.yaml, scripts.yaml, scenes.yaml) and "
        "storage-mode dashboards for an entity ID, service, device or area ID, label, helper "
        "or any other text. Case-insensitive substring match on every string value and key at "
        "any depth. Each matching item comes with its type, ID (url_path for a dashboard) and "
        "name, and each match with a JSON Pointer into that item's config and a snippet; a "
        "dashboard pointer is the one get_dashboard_config and patch_dashboard_config take. "
        "Items are listed automations first, then scripts, scenes and dashboards. A truncated "
        "item has more matches than it lists and a truncated result more items; 'counts' says "
        "how many items of each type matched, and 'skipped' names what was not searched and why"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text to find, e.g. 'light.kitchen' or 'notify.mobile_app'",
            },
            "types": {
                "type": "array",
                "items": {"type": "string", "enum": list(KIND_TOOLS)},
                "description": "Kinds of config to search (default: all)",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "description": f"Most items to return (default {DEFAULT_LIMIT})",
            },
            "matches_per_item": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_MATCHES_PER_ITEM,
                "description": (
                    f"Most matches to list for one item (default {DEFAULT_MATCHES_PER_ITEM}); "
                    "match_count still counts them all"
                ),
            },
        },
        "required": ["query"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def search_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Search automation, script, scene and dashboard configs for a substring."""
    query = arguments["query"]
    try:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        kinds = _kinds(arguments)
        limit = _bounded_int(arguments, "limit", DEFAULT_LIMIT, MAX_LIMIT)
        per_item = _bounded_int(
            arguments, "matches_per_item", DEFAULT_MATCHES_PER_ITEM, MAX_MATCHES_PER_ITEM
        )
    except ValueError as e:
        return _text(f"Error searching config: {e}")

    needle = query.lower()
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    skipped: dict[str, str] = {}
    for kind in kinds:
        withheld = next(
            (r for name in KIND_TOOLS[kind] if (r := tool_unavailable_reason(hass, name))), None
        )
        if withheld:
            skipped[kind] = withheld
            continue
        try:
            entries, failed = await _READERS[kind](hass)
        except Exception as e:  # noqa: BLE001 - one unreadable kind must not end the search
            skipped[kind] = f"could not be read: {e}"
            continue
        skipped.update(failed)
        for item_id, name, config in entries:
            matches = _Matches(needle, per_item)
            _walk(config, [], set(), matches)
            if not matches.count:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            if len(items) >= limit:
                continue
            item: dict[str, Any] = {"type": kind, "id": item_id}
            if isinstance(name, str) and name:
                item["name"] = name
            item["match_count"] = matches.count
            item["matches"] = matches.shown
            if matches.count > len(matches.shown):
                item["truncated"] = True
            items.append(item)

    result: dict[str, Any] = {"query": query, "counts": counts, "items": items}
    if sum(counts.values()) > len(items):
        result["truncated"] = True
    if skipped:
        result["skipped"] = skipped
    return _text(dumps(result))

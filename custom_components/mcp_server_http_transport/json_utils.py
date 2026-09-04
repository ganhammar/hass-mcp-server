"""Shared JSON helpers for Home Assistant MCP serialization."""

import json
from datetime import date, datetime
from typing import Any


class _HAJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime/date objects in HA state attributes."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return sorted(o) if all(isinstance(x, str) for x in o) else list(o)
        return super().default(o)


def dumps(obj: Any, *, cls: type[json.JSONEncoder] = _HAJSONEncoder) -> str:
    """Serialize ``obj`` as compact JSON for an LLM to read.

    The consumer of every tool, resource, and prompt payload is a language model, so
    indentation and the spaces after separators are token overhead with nothing to
    show for it: compact output is roughly a third smaller than ``indent=2``. Only
    ``cls`` is configurable, for callers whose payload needs a wider encoder.
    """
    return json.dumps(obj, separators=(",", ":"), cls=cls)

"""Shared JSON helpers for Home Assistant MCP serialization."""

import dataclasses
import json
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from homeassistant.helpers.json import json_encoder_default


class _HAJSONEncoder(json.JSONEncoder):
    """JSON encoder for the values Home Assistant puts in state attributes.

    Home Assistant serves its own API through orjson plus ``json_encoder_default``,
    so every value a client of that API tolerates has to serialize here as well:
    dates and times, enums, dataclasses, paths, and anything with ``as_dict``. Sets
    of strings are sorted so output is stable. Anything still unknown becomes
    ``str(value)`` rather than raising, because the tool wrapper reports a raised
    TypeError as ``isError`` and one exotic attribute would then blank a whole
    response.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return sorted(o) if all(isinstance(x, str) for x in o) else list(o)
        if isinstance(o, Enum):
            return o.value
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if hasattr(o, "as_dict"):
            return o.as_dict()
        try:
            return json_encoder_default(o)
        except TypeError:
            return str(o)


def dumps(obj: Any, *, cls: type[json.JSONEncoder] = _HAJSONEncoder) -> str:
    """Serialize ``obj`` as compact JSON for a language model to read.

    Indentation, the spaces after separators, and ``\\uXXXX`` escapes for
    non-ASCII text are all tokens the model pays for with nothing to show for it, so
    none are emitted. The JSON-RPC envelope escapes the text again on the wire, so
    the HTTP response stays ASCII either way. Only ``cls`` is configurable, for a
    caller whose payload needs a wider encoder.
    """
    return json.dumps(  # noqa: TID251 - the one place output is serialized
        obj, separators=(",", ":"), ensure_ascii=False, cls=cls
    )

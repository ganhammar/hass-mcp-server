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
        return super().default(o)

"""Build RFC 5545 RRULE strings for Home Assistant local calendars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from dateutil.rrule import rrulestr

# homeassistant/components/calendar/__init__.py VALID_FREQS
VALID_FREQS = frozenset({"DAILY", "WEEKLY", "MONTHLY", "YEARLY"})
WEEKDAYS = frozenset({"MO", "TU", "WE", "TH", "FR", "SA", "SU"})


@dataclass(frozen=True)
class RecurrenceSpec:
    freq: str
    interval: int = 1
    count: int | None = None
    until: date | None = None
    byday: tuple[str, ...] = ()
    bymonthday: int | None = None
    raw_rrule: str | None = None

    def rrule(self) -> str:
        if self.raw_rrule:
            return validate_rrule(self.raw_rrule)
        freq = self.freq.upper()
        if freq not in VALID_FREQS:
            raise ValueError(f"freq must be one of {sorted(VALID_FREQS)}; got {freq!r}")
        if self.interval < 1:
            raise ValueError("interval must be >= 1")
        if self.count is not None and self.count < 1:
            raise ValueError("count must be >= 1")
        if self.count is not None and self.until is not None:
            raise ValueError("Use count or until, not both")

        parts = [f"FREQ={freq}", f"INTERVAL={self.interval}"]
        if self.byday:
            if freq != "WEEKLY":
                raise ValueError("byday only applies to weekly recurrence")
            days = []
            for token in self.byday:
                key = token.strip().upper()[:2]
                if key not in WEEKDAYS:
                    raise ValueError(f"Invalid weekday {token!r}; use MO,TU,...,SU")
                days.append(key)
            parts.append(f"BYDAY={','.join(days)}")
        if self.bymonthday is not None:
            if freq != "MONTHLY":
                raise ValueError("bymonthday only applies to monthly recurrence")
            if not 1 <= self.bymonthday <= 31:
                raise ValueError("bymonthday must be 1-31")
            parts.append(f"BYMONTHDAY={self.bymonthday}")
        if self.count is not None:
            parts.append(f"COUNT={self.count}")
        if self.until is not None:
            parts.append(f"UNTIL={self.until.strftime('%Y%m%d')}")
        return validate_rrule(";".join(parts))


def validate_rrule(value: str) -> str:
    """Validate RRULE syntax (RFC 5545, no RRULE: prefix)."""
    raw = value.strip()
    if raw.upper().startswith("RRULE:"):
        raw = raw.split(":", 1)[1].strip()
    try:
        rrulestr(raw)
    except ValueError as err:
        raise ValueError(f"Invalid rrule '{value}': {err}") from err
    rule_parts = dict(part.split("=", 1) for part in raw.split(";") if "=" in part)
    freq = rule_parts.get("FREQ")
    if freq not in VALID_FREQS:
        raise ValueError(f"Invalid or missing FREQ in rrule (allowed: {sorted(VALID_FREQS)})")
    return raw


def parse_byday(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def recurrence_from_arguments(arguments: dict[str, Any]) -> str:
    """Resolve rrule from tool arguments (raw rrule or structured freq fields)."""
    raw = (arguments.get("rrule") or "").strip()
    if raw:
        return validate_rrule(raw)

    freq = (arguments.get("freq") or "").strip()
    if not freq:
        raise ValueError("Provide rrule or freq (DAILY, WEEKLY, MONTHLY, YEARLY)")

    until_raw = (arguments.get("until") or "").strip()
    until: date | None = None
    if until_raw:
        try:
            until = date.fromisoformat(until_raw)
        except ValueError as err:
            raise ValueError(f"until must be YYYY-MM-DD; got {until_raw!r}") from err

    count = arguments.get("count")
    if count is not None and int(count) < 1:
        raise ValueError("count must be >= 1")

    spec = RecurrenceSpec(
        freq=freq,
        interval=int(arguments.get("interval") or 1),
        count=int(count) if count is not None else None,
        until=until,
        byday=parse_byday(arguments.get("byday")),
        bymonthday=(
            int(arguments["bymonthday"]) if arguments.get("bymonthday") is not None else None
        ),
    )
    return spec.rrule()

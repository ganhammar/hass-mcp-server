"""Tests for calendar recurrence helpers."""

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "mcp_calendar_recurrence",
    _ROOT / "custom_components/mcp_server_http_transport/calendar_recurrence.py",
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

RecurrenceSpec = _mod.RecurrenceSpec
recurrence_from_arguments = _mod.recurrence_from_arguments
validate_rrule = _mod.validate_rrule


def test_validate_rrule_strips_prefix() -> None:
    assert validate_rrule("RRULE:FREQ=WEEKLY;BYDAY=MO") == "FREQ=WEEKLY;BYDAY=MO"


def test_recurrence_spec_weekly_open_ended() -> None:
    spec = RecurrenceSpec(freq="WEEKLY", byday=("MO",))
    assert spec.rrule() == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO"


def test_recurrence_from_arguments_raw_rrule() -> None:
    assert recurrence_from_arguments({"rrule": "FREQ=DAILY;COUNT=3"}) == "FREQ=DAILY;COUNT=3"


def test_recurrence_from_arguments_structured() -> None:
    rrule = recurrence_from_arguments(
        {"freq": "weekly", "byday": "MO,WE", "interval": 2, "count": 10}
    )
    assert rrule == "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=10"


def test_recurrence_requires_freq_or_rrule() -> None:
    with pytest.raises(ValueError, match="Provide rrule or freq"):
        recurrence_from_arguments({})

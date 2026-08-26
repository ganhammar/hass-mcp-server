"""Contracts against Home Assistant internals the tools still depend on.

Home Assistant's supported boundary between integrations is the service layer
and the state machine. Where a tool reaches past it, the call carries no
compatibility promise and can change without a deprecation cycle, which would
break on a user's HA upgrade rather than here. These tests pin each remaining
assumption so that upgrade fails CI instead.

Every contract below is either unavoidable or cheaper than its public
alternative; the docstrings say which, and what to do when one breaks.
"""

import dataclasses
import inspect

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT, LIST_EVENT_FIELDS
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    validate_statistics,
)
from homeassistant.helpers.entity_component import EntityComponent


def _params(func) -> list[str]:
    return list(inspect.signature(func).parameters)


class TestCalendarContracts:
    """Contracts for tools/calendar.py."""

    def test_async_get_events_signature(self):
        """The calendar tools call entity.async_get_events(hass, start, end).

        This is the CalendarEntity platform contract, not another integration's
        internals: the base class declares it and every calendar integration
        implements it. Home Assistant's own REST view and its own
        calendar.get_events service both call it exactly this way.
        """
        assert _params(CalendarEntity.async_get_events) == [
            "self",
            "hass",
            "start_date",
            "end_date",
        ]

    def test_calendar_event_carries_serialized_fields(self):
        """_serialize_event reads these off CalendarEvent."""
        fields = {f.name for f in dataclasses.fields(CalendarEvent)}
        assert {"start", "end", "summary", "description", "uid", "recurrence_id", "rrule"} <= fields
        for prop in ("start_datetime_local", "end_datetime_local", "all_day"):
            assert isinstance(getattr(CalendarEvent, prop), property)

    def test_entity_lookup_surface(self):
        """_get_calendar_entity resolves the entity via hass.data[DATA_COMPONENT].

        The genuinely unsupported step, and the one to replace first if a
        supported entity lookup ever appears. Home Assistant's own
        CalendarEventView resolves entities the same way.
        """
        assert DATA_COMPONENT is not None
        assert hasattr(EntityComponent, "get_entity")

    def test_get_events_service_still_drops_the_fields_we_need(self):
        """calendar.get_events is not a substitute for async_get_events.

        The service filters every event through LIST_EVENT_FIELDS, which drops
        uid, rrule and recurrence_id. list_calendar_events dedupes on uid and
        reports rrule/recurrence_id; delete_calendar_events matches on uid and
        passes it to async_delete_event, so it cannot work without one.

        If this test fails because the set grew, the swap in issue #78 has
        become possible: move both tools onto the service and delete the
        DATA_COMPONENT lookup above.
        """
        assert LIST_EVENT_FIELDS == {"start", "end", "summary", "description", "location"}
        assert not {"uid", "rrule", "recurrence_id"} & LIST_EVENT_FIELDS


class TestRecorderContracts:
    """Contracts for tools/statistics.py.

    get_statistics needs nothing here: it goes through the public
    recorder.get_statistics service. The recorder exposes no service
    equivalent for the four below.
    """

    def test_list_statistic_ids_signature(self):
        assert _params(list_statistic_ids) == ["hass", "statistic_ids", "statistic_type"]

    def test_validate_statistics_signature(self):
        assert _params(validate_statistics) == ["hass"]

    def test_async_adjust_statistics_signature(self):
        assert _params(Recorder.async_adjust_statistics) == [
            "self",
            "statistic_id",
            "start_time",
            "sum_adjustment",
            "adjustment_unit",
        ]

    def test_async_clear_statistics_signature(self):
        assert _params(Recorder.async_clear_statistics) == ["self", "statistic_ids", "on_done"]

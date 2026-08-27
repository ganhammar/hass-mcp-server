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
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT, LIST_EVENT_FIELDS
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.services import (
    SERVICE_GET_STATISTICS,
    _async_handle_get_statistics_service,
)
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    validate_statistics,
)
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.recorder import DATA_INSTANCE


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


class TestRecorderGetStatisticsService:
    """Contracts for the public service get_statistics reads through.

    A service is supported API, but its response shape is still an assumption
    this repo makes, and mocking the service in a tool test only asserts the
    shape the tool already believes in. These drive the real handler.
    """

    def test_service_exists(self):
        """The service get_statistics calls.

        Added in HA 2025.6, which is why hacs.json declares that as the minimum
        and the CI matrix starts there. On anything older this import fails and
        the tool would answer ServiceNotFound for every query.
        """
        assert SERVICE_GET_STATISTICS == "get_statistics"

    async def test_response_wraps_rows_under_a_statistics_key(self):
        """The response is {"statistics": {statistic_id: [rows]}}.

        get_statistics reads through that envelope. Without it the lookup
        misses silently and the tool reports no data rather than failing.
        """
        rows = {"sensor.energy": [{"start": 1704067200.0, "end": 1704070800.0, "mean": 1.5}]}
        hass = Mock()
        hass.data = {DATA_INSTANCE: Mock(async_add_executor_job=AsyncMock(return_value=rows))}
        call = Mock(
            hass=hass,
            data={
                "start_time": datetime(2024, 1, 1, tzinfo=UTC),
                "statistic_ids": ["sensor.energy"],
                "period": "hour",
                "types": ["mean"],
            },
        )

        response = await _async_handle_get_statistics_service(call)

        assert set(response) == {"statistics"}
        assert list(response["statistics"]) == ["sensor.energy"]
        assert response["statistics"]["sensor.energy"][0]["mean"] == 1.5

    async def test_response_renders_start_and_end_as_iso_strings(self):
        """get_statistics passes start and end through to the caller verbatim."""
        rows = {"sensor.energy": [{"start": 1704067200.0, "end": 1704070800.0, "mean": 1.5}]}
        hass = Mock()
        hass.data = {DATA_INSTANCE: Mock(async_add_executor_job=AsyncMock(return_value=rows))}
        call = Mock(
            hass=hass,
            data={
                "start_time": datetime(2024, 1, 1, tzinfo=UTC),
                "statistic_ids": ["sensor.energy"],
                "period": "hour",
                "types": ["mean"],
            },
        )

        row = (await _async_handle_get_statistics_service(call))["statistics"]["sensor.energy"][0]

        assert row["start"] == "2024-01-01T00:00:00+00:00"
        assert row["end"] == "2024-01-01T01:00:00+00:00"


class TestRecorderContracts:
    """Contracts for tools/statistics.py.

    The recorder exposes no service equivalent for the four below.
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

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

import pytest
from aiohttp import web
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT, LIST_EVENT_FIELDS
from homeassistant.components.http import HomeAssistantView
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

from custom_components.mcp_server_http_transport.const import MCP_PATH

# Core's built-in MCP server is what claims /api/mcp (#81). Importing it needs
# aiohttp_sse, which only its own manifest pulls in, and the streamable endpoint
# it collides on only exists from Home Assistant 2025.11; both absences raise
# ImportError here and skip the contract rather than failing it.
try:
    from homeassistant.components.mcp_server.http import STREAMABLE_API
except ImportError:
    STREAMABLE_API = None

# The KNX contracts below need the integration and its telegram store importable,
# which takes KNX's own requirements (xknx, knx-frontend, knx-telegram-store) on
# top of Home Assistant. They are installed on the KNX leg in CI and skipped
# everywhere else, including on Home Assistant versions predating the store.
try:
    from homeassistant.components.knx.const import (
        CONF_KNX_TELEGRAM_DB_LOAD_HOURS,
        KNX_MODULE_KEY,
        KNX_TELEGRAM_LOAD_HOURS_DEFAULT,
    )
    from homeassistant.components.knx.telegrams import TelegramDict, Telegrams
    from homeassistant.components.knx.websocket import ws_group_monitor_info
    from knx_telegram_store import (
        BufferedSqliteStore,
        KnxTelegramStoreException,
        TelegramQuery,
        TelegramQueryResult,
    )

    KNX_TELEGRAM_STORE_AVAILABLE = True
except Exception:  # pragma: no cover - KNX or its telegram store not installed
    KNX_TELEGRAM_STORE_AVAILABLE = False


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


@pytest.mark.skipif(
    not KNX_TELEGRAM_STORE_AVAILABLE, reason="KNX integration or its telegram store not installed"
)
class TestKnxTelegramStoreContracts:
    """Contracts for the telegram history read in tools/knx.py.

    The KNX integration exposes no service for telegram history, so the tool
    queries the store behind `hass.data[KNX_MODULE_KEY].telegrams`, which is the
    access path HA's own group-monitor websocket handler uses. Nothing here
    carries a compatibility promise, so each assumption is pinned.
    """

    def test_module_key_resolves_the_runtime_module(self):
        """_get_knx_module reads hass.data[KNX_MODULE_KEY]."""
        assert KNX_MODULE_KEY == "knx"

    def test_group_monitor_handler_still_reads_the_same_path(self):
        """The tool copies ws_group_monitor_info: same store, same options key.

        When this breaks, read that handler again before changing the tool: it
        is the closest thing to supported API this history has.
        """
        source = inspect.getsource(ws_group_monitor_info)
        assert "knx.telegrams.store.query(" in source
        assert "CONF_KNX_TELEGRAM_DB_LOAD_HOURS" in source

    def test_telegrams_holds_the_store_on_a_store_attribute(self):
        assert "self.store" in inspect.getsource(Telegrams.__init__)

    def test_query_signature(self):
        """_load_recent_telegrams calls store.query(query, flush_first=True)."""
        assert _params(BufferedSqliteStore.query) == ["self", "query", "flush_first"]

    def test_query_raises_knx_telegram_store_exception(self):
        """The tool reports a database error off this exception and nothing wider."""
        assert issubclass(KnxTelegramStoreException, Exception)
        assert KnxTelegramStoreException is not Exception

    def test_telegram_query_takes_the_window_and_ordering_the_tool_sets(self):
        fields = {f.name for f in dataclasses.fields(TelegramQuery)}
        assert {"start_time", "order_descending", "limit"} <= fields

    def test_telegram_query_caps_rows_by_default(self):
        """A capped query is why the tool asks for newest first and reverses.

        Were the default unbounded, ordering would only decide the order of the
        answer; with a cap it decides which end of the window survives it.
        """
        limit = next(f for f in dataclasses.fields(TelegramQuery) if f.name == "limit")
        assert isinstance(limit.default, int)
        assert limit.default > 0

    def test_query_result_carries_telegrams(self):
        fields = {f.name for f in dataclasses.fields(TelegramQueryResult)}
        assert "telegrams" in fields

    def test_model_to_dict_converts_one_stored_telegram(self):
        assert _params(Telegrams.model_to_dict) == ["self", "m"]

    def test_telegram_dict_carries_the_filtered_and_spanned_fields(self):
        """The tool filters on destination and destination_name, spans on timestamp."""
        assert {"destination", "destination_name", "timestamp"} <= set(TelegramDict.__annotations__)

    def test_load_hours_option_and_default(self):
        """The window the tool reads off the config entry, and its fallback."""
        assert CONF_KNX_TELEGRAM_DB_LOAD_HOURS == "telegram_db_load_hours"
        assert KNX_TELEGRAM_LOAD_HOURS_DEFAULT == 24


class _ContractView(HomeAssistantView):
    """A view registered on two paths, standing in for the MCP endpoint."""

    url = "/api/contract_test"
    extra_urls = ["/api/contract_test_alt"]
    name = "test:contract"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Serve nothing; only the registration is under test."""
        return web.Response()


def _register(view: HomeAssistantView, app: web.Application) -> None:
    view.register(Mock(), app, app.router)


class TestViewRegistrationContracts:
    """Contracts for http.py, which reads the router to detect a path conflict.

    Home Assistant exposes no way to ask who serves a path, or to unregister a
    view, so register_mcp_views inspects hass.http.app.router directly. Nothing
    promises that stays readable, and the conflict with core's mcp_server on
    /api/mcp (#81) is invisible without it.
    """

    def test_register_serves_url_and_extra_urls(self):
        """The endpoint reaches its second path through extra_urls."""
        app = web.Application()

        _register(_ContractView(), app)

        assert [route.resource.canonical for route in app.router.routes()] == [
            "/api/contract_test",
            "/api/contract_test_alt",
        ]

    def test_routes_expose_method_and_canonical_path(self):
        """The conflict check reads exactly these two attributes off a route."""
        app = web.Application()

        _register(_ContractView(), app)

        route = next(iter(app.router.routes()))
        assert route.method == "POST"
        assert route.resource.canonical == "/api/contract_test"

    def test_a_second_view_on_one_path_registers_rather_than_raising(self):
        """Two integrations can hold one path, which is why #81 is silent.

        HomeAssistantView.register adds routes without a name, so aiohttp raises
        nothing on the duplicate. Were this to start raising, registering
        MCP_PATH while core's mcp_server holds it would fail setup instead.
        """
        app = web.Application()

        _register(_ContractView(), app)
        _register(_ContractView(), app)

        assert len(list(app.router.routes())) == 4

    @pytest.mark.skipif(
        STREAMABLE_API is None,
        reason="core's streamable MCP endpoint is not importable on this install",
    )
    def test_core_streamable_endpoint_is_the_path_this_integration_serves(self):
        """The conflict this integration works around is core serving MCP_PATH.

        If a release moves core's streamable endpoint, MCP_PATH stops being
        contested and the repair, and the second path it points at, can go.
        """
        assert MCP_PATH == STREAMABLE_API

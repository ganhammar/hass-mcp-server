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
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import attr
import pytest
import voluptuous as vol
from aiohttp import web
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT, LIST_EVENT_FIELDS
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.ban import process_success_login, process_wrong_login
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.history import (
    get_significant_states,
    get_significant_states_with_session,
)
from homeassistant.components.recorder.services import (
    SERVICE_GET_STATISTICS,
    _async_handle_get_statistics_service,
)
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    validate_statistics,
)
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.category_registry import CategoryEntry, CategoryRegistry
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.entity_registry import EntityRegistry, RegistryEntry
from homeassistant.helpers.json import json_encoder_default
from homeassistant.helpers.label_registry import LabelEntry, LabelRegistry
from homeassistant.helpers.recorder import DATA_INSTANCE
from homeassistant.loader import Integration, async_get_integration
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.mcp_server_http_transport.const import MCP_PATH
from custom_components.mcp_server_http_transport.tools.labels import THEME_COLORS

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


# The KNX write tools validate entity data the way HA's own websocket commands do.
# Skip only when the KNX integration's own requirement is missing: once xknx is
# importable, a renamed or removed HA name must fail collection here rather than
# quietly skip the contracts that would have caught it.
try:
    import xknx  # noqa: F401

    KNX_ENTITY_STORE_AVAILABLE = True
except ImportError:  # pragma: no cover - KNX integration requirements not installed
    KNX_ENTITY_STORE_AVAILABLE = False

if KNX_ENTITY_STORE_AVAILABLE:
    from homeassistant.components.knx.storage.config_store import KNXConfigStore
    from homeassistant.components.knx.storage.entity_store_validation import (
        EntityStoreValidationException,
        validate_entity_data,
    )
    from homeassistant.components.knx.websocket import ws_create_entity, ws_update_entity


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

        The set grows between releases (2026.10 added status). If this test
        fails because it took in the three fields, the swap in issue #78 has
        become possible: move both tools onto the service and delete the
        DATA_COMPONENT lookup above.
        """
        assert {"start", "end", "summary", "description", "location"} <= LIST_EVENT_FIELDS
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


class TestLabelContracts:
    """Contracts for tools/labels.py and list_labels.

    Labels have no service; the frontend manages them over the websocket API,
    which writes the same registry these call. The signatures are pinned as
    keyword-only options after the name or ID, which is how the tools pass them.
    """

    def test_label_registry_signatures(self):
        assert _params(LabelRegistry.async_create) == [
            "self",
            "name",
            "color",
            "icon",
            "description",
        ]
        assert _params(LabelRegistry.async_update) == [
            "self",
            "label_id",
            "color",
            "description",
            "icon",
            "name",
        ]
        assert _params(LabelRegistry.async_delete) == ["self", "label_id"]
        assert _params(LabelRegistry.async_get_label) == ["self", "label_id"]
        assert _params(LabelRegistry.async_get_label_by_name) == ["self", "name"]
        assert _params(LabelRegistry.async_list_labels) == ["self"]

    def test_options_are_keyword_only(self):
        """A positional color would silently land in the wrong field."""
        for func in (LabelRegistry.async_create, LabelRegistry.async_update):
            kinds = {
                name: p.kind
                for name, p in inspect.signature(func).parameters.items()
                if name not in ("self", "name", "label_id")
            }
            assert set(kinds.values()) == {inspect.Parameter.KEYWORD_ONLY}

    def test_label_entry_carries_the_listed_fields(self):
        fields = {f.name for f in dataclasses.fields(LabelEntry)}
        assert {"label_id", "name", "color", "icon", "description"} <= fields

    def test_core_accepts_every_theme_color_the_tools_accept(self):
        """labels.py keeps its own copy of the color names core's websocket command allows.

        The registry stores any string, so the tools enforce the names themselves. If
        core drops or renames one, labels created with it would no longer round-trip
        through the frontend: update THEME_COLORS to match.
        """
        from homeassistant.components.config.label_registry import (
            SUPPORTED_LABEL_THEME_COLORS,
        )

        assert THEME_COLORS <= SUPPORTED_LABEL_THEME_COLORS
        accepts = vol.Any(cv.color_hex, vol.In(SUPPORTED_LABEL_THEME_COLORS), None)
        assert accepts("#ff9800") == "#ff9800"
        with pytest.raises(vol.Invalid):
            accepts("Red")

    async def test_duplicate_name_on_create_raises_value_error(self, hass):
        """create_label reports the registry's refusal rather than checking for itself."""
        registry = lr.async_get(hass)
        registry.async_create("Outdoor")
        with pytest.raises(ValueError):
            registry.async_create("OUTDOOR")

    async def test_unknown_label_on_update_raises_key_error(self, hass):
        """update_label looks the label up first so that this never reaches a caller."""
        with pytest.raises(KeyError):
            lr.async_get(hass).async_update("nope", name="X")

    async def test_deleting_a_label_strips_it_from_entities_devices_and_areas(self, hass):
        """delete_label does no cleanup of its own; it relies on the registries doing it."""
        label = lr.async_get(hass).async_create("Doomed")
        entities = er.async_get(hass)
        entity_id = entities.async_get_or_create("light", "hue", "l1").entity_id
        entities.async_update_entity(entity_id, labels={label.label_id})
        config_entry = MockConfigEntry(domain="hue")
        config_entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        device = devices.async_get_or_create(
            config_entry_id=config_entry.entry_id, identifiers={("hue", "d1")}
        )
        devices.async_update_device(device.id, labels={label.label_id})
        area = ar.async_get(hass).async_create("Garden", labels={label.label_id})

        lr.async_get(hass).async_delete(label.label_id)
        await hass.async_block_till_done()

        assert entities.async_get(entity_id).labels == set()
        assert devices.async_get(device.id).labels == set()
        assert ar.async_get(hass).async_get_area(area.id).labels == set()


class TestRegistryWriteContracts:
    """Contracts for tools/registries.py and update_entity_registry.

    Areas, floors and devices have no service for these writes; the frontend makes
    them over the websocket API, which calls the same registry methods. The options
    the tools pass are pinned as keyword-only, since each signature grows between
    releases and a positional argument would land in the wrong field.
    """

    @pytest.mark.parametrize(
        ("func", "positional", "options"),
        [
            (
                ar.AreaRegistry.async_create,
                ["name"],
                {"aliases", "floor_id", "icon", "labels"},
            ),
            (
                ar.AreaRegistry.async_update,
                ["area_id"],
                {"aliases", "floor_id", "icon", "labels", "name"},
            ),
            (fr.FloorRegistry.async_create, ["name"], {"aliases", "icon", "level"}),
            (fr.FloorRegistry.async_update, ["floor_id"], {"aliases", "icon", "level", "name"}),
            (
                dr.DeviceRegistry.async_update_device,
                ["device_id"],
                {"area_id", "disabled_by", "labels", "name_by_user"},
            ),
            (
                EntityRegistry.async_update_entity,
                ["entity_id"],
                {"area_id", "disabled_by", "hidden_by", "labels", "name", "new_entity_id"},
            ),
        ],
    )
    def test_writes_take_the_options_the_tools_pass(self, func, positional, options):
        parameters = inspect.signature(func).parameters

        assert list(parameters)[: len(positional) + 1] == ["self", *positional]
        assert options <= set(parameters)
        assert {parameters[name].kind for name in options} == {inspect.Parameter.KEYWORD_ONLY}

    def test_a_registry_with_child_devices_can_tell_the_kinds_of_device_apart(self):
        """update_device takes the split-aware path whenever the child update exists.

        It then needs the lookup to leave kinds out by keyword, to refuse the old ID
        of a split device and to send a child device to its own update method.
        """
        update_child = getattr(dr.DeviceRegistry, "async_update_child_device", None)
        if update_child is None:
            assert _params(dr.DeviceRegistry.async_get) == ["self", "device_id"]
            return

        lookup = inspect.signature(dr.DeviceRegistry.async_get).parameters
        kinds = {"include_child_devices", "include_main_devices", "include_composite_devices"}
        assert kinds <= set(lookup)
        assert {lookup[name].default for name in kinds} == {True}
        assert {"area_id", "disabled_by", "labels", "name_by_user"} <= set(
            inspect.signature(update_child).parameters
        )
        assert issubclass(dr.ChildDeviceEntry, dr.BaseDeviceEntry)
        assert {"id", "name", "name_by_user", "area_id", "labels", "disabled_by"} <= {
            f.name for f in attr.fields(dr.BaseDeviceEntry)
        }

    def test_devices_in_an_area_are_counted_through_the_helper(self):
        assert _params(dr.async_entries_for_area) == ["registry", "area_id"]

    def test_enabling_an_entity_reloads_its_entry_after_a_delay(self):
        from homeassistant.config_entries import RELOAD_AFTER_UPDATE_DELAY

        assert isinstance(RELOAD_AFTER_UPDATE_DELAY, int)
        assert MockConfigEntry(domain="demo").supports_unload in (None, False)

    def test_a_device_names_the_config_entries_it_belongs_to(self):
        """delete_device asks each one's integration.

        A core with DeviceEntry.config_entry_id has one per device. The ones before
        share a device between several, and detach them one at a time.
        """
        fields = {f.name for f in attr.fields(dr.DeviceEntry)}

        if "config_entry_id" not in fields:
            assert "config_entries" in fields
            assert "remove_config_entry_id" in _params(dr.DeviceRegistry.async_update_device)

    def test_a_child_device_names_its_parent(self):
        """delete_device counts the child devices that go with a parent, where they exist."""
        if not hasattr(dr, "ChildDeviceEntry"):
            assert not hasattr(dr.DeviceRegistry, "async_update_child_device")
            return

        assert {"id", "parent_device_id"} <= {f.name for f in attr.fields(dr.ChildDeviceEntry)}
        assert "child_devices" in dr.DeviceRegistry.__annotations__

    async def test_core_decides_device_removal_by_the_integrations_hook(self, hass):
        """delete_device reads supports_remove_device and calls the hook core looks for."""
        from homeassistant.config_entries import support_remove_from_device

        mock_integration(
            hass, MockModule("removable", async_remove_config_entry_device=AsyncMock())
        )
        mock_integration(hass, MockModule("fixed"))

        assert await support_remove_from_device(hass, "removable") is True
        assert await support_remove_from_device(hass, "fixed") is False
        assert MockConfigEntry(domain="fixed").supports_remove_device is None

    def test_removal_takes_an_id(self):
        assert _params(dr.DeviceRegistry.async_remove_device) == ["self", "device_id"]
        assert _params(EntityRegistry.async_remove) == ["self", "entity_id"]
        assert _params(er.async_entries_for_device)[:3] == [
            "registry",
            "device_id",
            "include_disabled_entities",
        ]

    def test_lookups_and_deletes_take_an_id(self):
        assert _params(ar.AreaRegistry.async_get_area) == ["self", "area_id"]
        assert _params(ar.AreaRegistry.async_delete) == ["self", "area_id"]
        assert _params(fr.FloorRegistry.async_get_floor) == ["self", "floor_id"]
        assert _params(fr.FloorRegistry.async_delete) == ["self", "floor_id"]
        assert _params(fr.FloorRegistry.async_list_floors) == ["self"]

    def test_entries_carry_the_reported_fields(self):
        assert {"id", "name", "floor_id", "icon", "labels", "aliases"} <= {
            f.name for f in dataclasses.fields(ar.AreaEntry)
        }
        assert {"floor_id", "name", "icon", "level", "aliases"} <= {
            f.name for f in dataclasses.fields(fr.FloorEntry)
        }
        assert dr.DeviceEntryDisabler.USER.value == "user"
        assert er.RegistryEntryDisabler.USER.value == "user"
        assert er.RegistryEntryHider.USER.value == "user"


class TestCategoryContracts:
    """Contracts for tools/categories.py.

    Categories have no service; the frontend sets them over the websocket API,
    which writes the same registries these call. The entity registry keys an
    entity's categories by scope ("automation", "script", "scene", "helpers"),
    one category per scope.
    """

    def test_category_registry_lookup_and_create_signatures(self):
        assert _params(CategoryRegistry.async_get_category) == ["self", "scope", "category_id"]
        assert _params(CategoryRegistry.async_list_categories) == ["self", "scope"]
        assert _params(CategoryRegistry.async_create) == ["self", "name", "scope", "icon"]

    def test_category_entry_carries_the_listed_fields(self):
        fields = {f.name for f in dataclasses.fields(CategoryEntry)}
        assert {"category_id", "name", "icon"} <= fields

    def test_entity_categories_are_a_scope_mapping_set_through_update_entity(self):
        assert "categories" in _params(EntityRegistry.async_update_entity)
        assert "categories" in {a.name for a in RegistryEntry.__attrs_attrs__}

    def test_entity_icon_override_is_set_through_update_entity(self):
        """tools/icons.py writes the icon override the entity settings dialog writes.

        Automations have no icon in their config, so this field is the only place
        the create and update tools can put one.
        """
        assert "icon" in _params(EntityRegistry.async_update_entity)
        assert "icon" in {a.name for a in RegistryEntry.__attrs_attrs__}

    def test_helpers_are_recognized_by_their_integration_type(self):
        """A helper is put in the "helpers" scope because its integration is a helper.

        That covers config entry helpers such as template sensors, whose entity
        domain says nothing about being a helper.
        """
        assert _params(async_get_integration) == ["hass", "domain"]
        assert "integration_type" in dir(Integration)


class TestHistoryContracts:
    """Contracts for the state history the prompts in prompts/reporting.py read.

    get_significant_states is the function behind the history REST view and
    websocket command, both of which are HTTP surfaces this integration cannot
    call from inside the process, so the prompts reach into the module.
    """

    def test_get_significant_states_signature(self):
        """The prompts pass hass, start, end and entity_ids positionally.

        daily_summary also names the two options that keep its every-connect
        query cheap; a rename would turn them into a TypeError inside the
        executor and the prompt would report the recorder as unavailable.
        """
        params = _params(get_significant_states)
        assert params[:4] == ["hass", "start_time", "end_time", "entity_ids"]
        assert {"include_start_time_state", "no_attributes"} <= set(params)

    def test_a_query_across_every_entity_is_refused(self):
        """Regression for #95: entity_ids=None raises rather than meaning "all".

        daily_summary therefore supplies the state machine's entity list. The
        check runs before the session is touched, which is why a Mock session
        gets far enough to trip it.
        """
        hass = Mock()
        hass.data = {DATA_INSTANCE: Mock(states_meta_manager=Mock(active=True))}

        with pytest.raises(ValueError, match="entity_ids must be provided"):
            get_significant_states_with_session(
                hass, Mock(), datetime(2024, 1, 1, tzinfo=UTC), None, None
            )


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


@pytest.mark.skipif(not KNX_ENTITY_STORE_AVAILABLE, reason="KNX requirements (xknx) not installed")
class TestKnxEntityStoreContracts:
    """Contracts for knx_create_entity / knx_update_entity in tools/knx.py.

    The tools write through `knx.config_store`, which has no compatibility promise.
    They copy HA's own `knx/create_entity` and `knx/update_entity` websocket
    handlers: validate first, then pass the validated platform, entity_id and data.
    Since HA 2026.10 the store reads typed fields off the validated data, so
    skipping validation breaks it - after the entity was already removed.
    """

    _SWITCH = {
        "entity": {"name": "x", "device_info": None, "entity_category": None},
        "knx": {
            "ga_switch": {"write": "1/1/1", "state": None, "passive": []},
            "invert": False,
            "respond_to_read": False,
            "sync_state": True,
        },
    }

    def test_websocket_handlers_validate_before_the_store(self):
        """When this breaks, read the handlers again before changing the tools."""
        for handler in (ws_create_entity, ws_update_entity):
            source = inspect.getsource(handler)
            assert "validate_entity_data(" in source
            assert "validated_data[CONF_DATA]" in source

    def test_store_signatures(self):
        assert _params(KNXConfigStore.create_entity) == ["self", "platform", "data"]
        assert _params(KNXConfigStore.update_entity) == ["self", "platform", "entity_id", "data"]

    def test_validation_keeps_the_keys_the_tools_read(self):
        validated = validate_entity_data(
            {"platform": "switch", "entity_id": "switch.x", "data": self._SWITCH}
        )
        assert validated["platform"] == "switch"
        assert validated["entity_id"] == "switch.x"
        assert "data" in validated

    def test_validation_error_carries_errors(self):
        """The tools return exc.validation_error as the result text."""
        with pytest.raises(EntityStoreValidationException) as exc:
            validate_entity_data(
                {"platform": "switch", "data": {"entity": {"name": "x"}, "knx": {}}}
            )
        error = exc.value.validation_error
        assert error["success"] is False
        assert error["errors"]
        assert {"path", "message"} <= set(error["errors"][0])


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


class TestJsonEncoderDefaultContract:
    """``_HAJSONEncoder`` delegates to ``json_encoder_default`` for the value types
    Home Assistant's own API accepts, then falls back to ``str()``.

    The public alternative is ``homeassistant.helpers.json.json_dumps``, which is
    orjson: no encoder hook for the trace tools' ``ExtendedJSONEncoder``, and it
    neither sorts sets nor accepts frozensets. Were this to break, copy the Path
    and ``as_dict`` branches into ``_HAJSONEncoder.default`` and drop the import.
    """

    def test_takes_one_value_and_maps_the_types_relied_on(self):
        assert list(inspect.signature(json_encoder_default).parameters) == ["obj"]
        assert json_encoder_default(Path("/config/www")) == "/config/www"
        assert json_encoder_default({"a"}) == ["a"]

    def test_raises_type_error_for_an_unknown_value(self):
        # The str() fallback in _HAJSONEncoder only runs if HA raises TypeError,
        # not some other exception, for a value it does not know.
        with pytest.raises(TypeError):
            json_encoder_default(object())


class TestProcessWrongLoginContract:
    """``process_wrong_login`` is how a failed bearer token reaches Home Assistant's
    login attempt counter and IP ban (#107).

    The view validates tokens itself, so Home Assistant's own auth middleware never
    sees a rejection there. The public alternative is the ``ip_bans.yaml`` file and
    the ban manager's own methods, which carry no counter or threshold. Were this
    to break, check the new signature and what it reads off the request before
    adapting ``MCPEndpointView._record_failed_login``.
    """

    def test_is_a_coroutine_taking_only_the_request(self):
        assert inspect.iscoroutinefunction(process_wrong_login)
        assert _params(process_wrong_login) == ["request"]


class TestProcessSuccessLoginContract:
    """``process_success_login`` clears an address's failed login count (#107).

    The view calls it after every authorized request, since Home Assistant only
    calls it from the login flow and the websocket handler, never for a bearer
    request. It is synchronous, unlike ``process_wrong_login``.
    """

    def test_is_a_plain_function_taking_only_the_request(self):
        assert not inspect.iscoroutinefunction(process_success_login)
        assert _params(process_success_login) == ["request"]


class TestFlowResultSerializationContract:
    """tools/integrations.py serializes a step's schema through core's flow view.

    ``_prepare_result_json`` is private, but it is the only place that knows
    which library turns a voluptuous schema into the field list on a given Home
    Assistant version: voluptuous_serialize up to 2026.4, probatio after. If it
    is renamed or stops returning ``data_schema`` as a list, serialize the schema
    in the tool the way the view then does. test_e2e.py drives the same call
    through a real flow on every version in the matrix.
    """

    def test_view_is_built_from_a_flow_manager_alone(self):
        assert _params(FlowManagerResourceView.__init__) == ["self", "flow_mgr"]

    def test_prepare_result_json_takes_only_the_result(self):
        assert _params(FlowManagerResourceView._prepare_result_json) == ["self", "result"]

    def test_schema_becomes_a_field_list_and_none_an_empty_one(self):
        view = FlowManagerResourceView(Mock())
        result = {"type": "form", "flow_id": "f", "handler": "demo", "step_id": "user"}

        fields = view._prepare_result_json(
            {**result, "data_schema": vol.Schema({vol.Required("host"): str})}
        )["data_schema"]

        assert fields == [{"name": "host", "required": True, "type": "string"}]
        assert view._prepare_result_json({**result, "data_schema": None})["data_schema"] == []


class TestFlowProgressContract:
    """continue_config_flow reads a flow's current step to vet a menu answer.

    ``async_get`` and ``async_progress`` return a partial result without the
    step's type or menu options, so the tool reads ``cur_step`` off the flow
    object in the manager's private ``_progress`` mapping. The lookup is guarded
    and skips the check when it finds nothing, which means a rename here would
    silently bring back the behavior it prevents: Home Assistant dropping a flow
    over a mistyped menu option.
    """

    async def test_progress_maps_flow_id_to_a_flow_carrying_its_current_step(
        self, hass: HomeAssistant
    ):
        class _MenuFlow(ConfigFlow):
            async def async_step_user(self, user_input=None):
                return self.async_show_menu(step_id="user", menu_options=["a", "b"])

        mock_integration(hass, MockModule("mcp_contract"))
        mock_platform(hass, "mcp_contract.config_flow", None)
        with mock_config_flow("mcp_contract", _MenuFlow):
            result = await hass.config_entries.flow.async_init(
                "mcp_contract", context={"source": "user"}
            )

            step = hass.config_entries.flow._progress[result["flow_id"]].cur_step

        assert step["type"] == FlowResultType.MENU
        assert list(step["menu_options"]) == ["a", "b"]

    async def test_a_flow_is_found_by_id_and_names_what_it_configures(self, hass: HomeAssistant):
        """continue_config_flow looks a flow up in the config and the options manager.

        ``async_get`` raises UnknownFlow in the one that does not hold it, and its
        result's ``handler`` is the domain for a config flow and the config entry
        ID for an options flow. Both managers keep flows in ``_progress``.
        """

        class _Options(OptionsFlow):
            async def async_step_init(self, user_input=None):
                return self.async_show_menu(step_id="init", menu_options=["a"])

        class _Flow(ConfigFlow):
            async def async_step_user(self, user_input=None):
                return self.async_show_menu(step_id="user", menu_options=["a"])

            @staticmethod
            def async_get_options_flow(config_entry):
                return _Options()

        mock_integration(hass, MockModule("mcp_contract"))
        mock_platform(hass, "mcp_contract.config_flow", None)
        entry = MockConfigEntry(domain="mcp_contract")
        entry.add_to_hass(hass)
        flows, options = hass.config_entries.flow, hass.config_entries.options
        with mock_config_flow("mcp_contract", _Flow):
            config_id = (await flows.async_init("mcp_contract", context={"source": "user"}))[
                "flow_id"
            ]
            options_id = (await options.async_init(entry.entry_id))["flow_id"]

            assert flows.async_get(config_id)["handler"] == "mcp_contract"
            assert options.async_get(options_id)["handler"] == entry.entry_id
            assert options._progress[options_id].cur_step["type"] == FlowResultType.MENU
            with pytest.raises(UnknownFlow):
                flows.async_get(options_id)
            with pytest.raises(UnknownFlow):
                options.async_get(config_id)

"""Tests for KNX telegram-history tools."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.mcp_server_http_transport.tools import knx as knx_mod

_KEY = "knx_test_module_key"

_TELEGRAMS = [
    {
        "destination": "0/1/1",
        "destination_name": "Licht Wohnzimmer",
        "source": "1.1.5",
        "source_name": "MDT Aktor",
        "value": False,
        "timestamp": "2026-05-29T21:00:00+02:00",
        "telegramtype": "GroupValueWrite",
    },
    {
        "destination": "0/0/249",
        "destination_name": "GT TagNacht",
        "source": "1.1.99",
        "source_name": "MDT Logic Module",
        "value": True,
        "timestamp": "2026-05-29T21:05:00+02:00",
        "telegramtype": "GroupValueWrite",
    },
    {
        "destination": "0/0/249",
        "destination_name": "GT TagNacht",
        "source": "1.1.99",
        "source_name": "MDT Logic Module",
        "value": False,
        "timestamp": "2026-05-29T21:06:00+02:00",
        "telegramtype": "GroupValueWrite",
    },
]


def _hass_with_knx(telegrams):
    """Mock hass with a KNX module exposing recent_telegrams."""
    module = Mock()
    module.telegrams.recent_telegrams = telegrams
    hass = Mock()
    hass.data = {_KEY: module}
    return hass


def _unpack(result: dict) -> dict:
    """Unpack the MCP content envelope into the payload dict."""
    assert "content" in result, f"Expected envelope with 'content', got: {result.keys()}"
    return json.loads(result["content"][0]["text"])


class TestKnxRecentTelegrams:
    """Test knx_recent_telegrams against the in-memory telegram list.

    TelegramQuery is None on installs whose telegram history is not DB-backed,
    which is what sends knx_recent_telegrams down this path.
    """

    @pytest.fixture(autouse=True)
    def _patch_key(self):
        with (
            patch.object(knx_mod, "KNX_MODULE_KEY", _KEY),
            patch.object(knx_mod, "TelegramQuery", None),
        ):
            yield

    async def test_returns_not_setup_when_knx_missing(self):
        hass = Mock()
        hass.data = {}
        result = await knx_mod.knx_recent_telegrams(hass, {})
        assert "content" in result
        assert "not set up" in result["content"][0]["text"]

    async def test_returns_all_with_buffer_span(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {})
        data = _unpack(result)
        assert data["buffer_size"] == 3
        assert data["matched"] == 3
        assert data["buffer_span"]["oldest"] == "2026-05-29T21:00:00+02:00"
        assert data["buffer_span"]["newest"] == "2026-05-29T21:06:00+02:00"

    async def test_filter_ga(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"filter_ga": "^0/0/249$"})
        data = _unpack(result)
        assert data["matched"] == 2
        assert all(t["destination"] == "0/0/249" for t in data["telegrams"])
        # source device of the flapping GA is surfaced
        assert data["telegrams"][0]["source_name"] == "MDT Logic Module"

    async def test_filter_name_case_insensitive(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"filter_name": "licht"})
        data = _unpack(result)
        assert data["matched"] == 1
        assert data["telegrams"][0]["destination"] == "0/1/1"

    async def test_limit_keeps_most_recent(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"limit": 1})
        data = _unpack(result)
        assert data["returned"] == 1
        assert data["telegrams"][0]["timestamp"] == "2026-05-29T21:06:00+02:00"

    async def test_limit_zero_is_clamped_to_one(self):
        """limit=0 must not silently become 200."""
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"limit": 0})
        data = _unpack(result)
        assert data["returned"] == 1

    async def test_limit_negative_is_clamped_to_one(self):
        """Negative limit must not slice from the wrong end."""
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"limit": -5})
        data = _unpack(result)
        assert data["returned"] == 1

    async def test_invalid_regex_returns_error(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"filter_ga": "("})
        assert "content" in result
        assert "Invalid regex" in result["content"][0]["text"]

    async def test_history_unavailable_attribute_error(self):
        class _NoHistory:
            @property
            def recent_telegrams(self):
                raise AttributeError("no telegram history on this HA version")

        module = Mock()
        module.telegrams = _NoHistory()
        hass = Mock()
        hass.data = {_KEY: module}
        result = await knx_mod.knx_recent_telegrams(hass, {})
        assert "content" in result
        assert "unavailable" in result["content"][0]["text"]


class TestKnxRecentTelegramsDbStore:
    """Test knx_recent_telegrams against the DB-backed telegram store.

    The store answers newest first over a window taken from the KNX config
    entry, which is the same query HA's own `knx/group_monitor_info` websocket
    handler issues. TestKnxRecentTelegrams above covers the in-memory list read
    where history is not DB-backed.
    """

    @pytest.fixture(autouse=True)
    def _patch_key(self):
        with patch.object(knx_mod, "KNX_MODULE_KEY", _KEY):
            yield

    def _hass_with_store(self, telegrams, query_side_effect=None):
        """Mock hass whose KNX module answers from the store.

        `telegrams` is passed oldest first and handed back newest first, the
        order the query the tool builds asks for.
        """
        module = Mock()
        module.entry.options = {}
        query_result = Mock()
        query_result.telegrams = list(reversed(telegrams))
        module.telegrams.store = Mock()
        if query_side_effect is not None:
            module.telegrams.store.query = AsyncMock(side_effect=query_side_effect)
        else:
            module.telegrams.store.query = AsyncMock(return_value=query_result)
        module.telegrams.model_to_dict = lambda t: t
        hass = Mock()
        hass.data = {_KEY: module}
        return hass

    @staticmethod
    def _query_kwargs(hass):
        """The kwargs the tool built its TelegramQuery from."""
        return hass.data[_KEY].telegrams.store.query.await_args.args[0]

    async def test_uses_store_when_available(self):
        hass = self._hass_with_store(_TELEGRAMS)
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            result = await knx_mod.knx_recent_telegrams(hass, {})
        data = _unpack(result)
        assert data["buffer_size"] == 3
        assert data["matched"] == 3
        hass.data[_KEY].telegrams.store.query.assert_awaited_once()

    async def test_returns_oldest_first(self):
        """The store answers newest first; the payload reads oldest first."""
        hass = self._hass_with_store(_TELEGRAMS)
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            result = await knx_mod.knx_recent_telegrams(hass, {})
        data = _unpack(result)
        assert [t["timestamp"] for t in data["telegrams"]] == [
            "2026-05-29T21:00:00+02:00",
            "2026-05-29T21:05:00+02:00",
            "2026-05-29T21:06:00+02:00",
        ]

    async def test_limit_keeps_most_recent(self):
        """A limit takes the newest telegrams, not the oldest in the window."""
        hass = self._hass_with_store(_TELEGRAMS)
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            result = await knx_mod.knx_recent_telegrams(hass, {"limit": 1})
        data = _unpack(result)
        assert data["returned"] == 1
        assert data["telegrams"][0]["timestamp"] == "2026-05-29T21:06:00+02:00"

    async def test_query_asks_the_store_for_newest_first(self):
        """Descending order is what keeps the newest rows under the store's cap."""
        hass = self._hass_with_store(_TELEGRAMS)
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            await knx_mod.knx_recent_telegrams(hass, {})
        assert self._query_kwargs(hass)["order_descending"] is True

    async def test_window_defaults_to_the_group_monitor_default(self):
        """Without the option set, the window matches HA's own default of 24h."""
        hass = self._hass_with_store(_TELEGRAMS)
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            await knx_mod.knx_recent_telegrams(hass, {})
        window = dt_util.now() - self._query_kwargs(hass)["start_time"]
        assert timedelta(hours=23, minutes=59) < window < timedelta(hours=24, minutes=1)

    async def test_window_comes_from_entry_options(self):
        hass = self._hass_with_store(_TELEGRAMS)
        hass.data[_KEY].entry.options = {"telegram_db_load_hours": 3}
        with (
            patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)),
            patch.object(knx_mod, "CONF_KNX_TELEGRAM_DB_LOAD_HOURS", "telegram_db_load_hours"),
        ):
            await knx_mod.knx_recent_telegrams(hass, {})
        window = dt_util.now() - self._query_kwargs(hass)["start_time"]
        assert timedelta(hours=2, minutes=59) < window < timedelta(hours=3, minutes=1)

    async def test_filter_ga_via_store(self):
        hass = self._hass_with_store(_TELEGRAMS)
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            result = await knx_mod.knx_recent_telegrams(hass, {"filter_ga": "^0/0/249$"})
        data = _unpack(result)
        assert data["matched"] == 2
        assert all(t["destination"] == "0/0/249" for t in data["telegrams"])

    async def test_uninitialized_store_reports_initialization(self):
        """Store init can fail; that is not the Home Assistant version being too old."""
        hass = self._hass_with_store(_TELEGRAMS)
        hass.data[_KEY].telegrams.store = None
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            result = await knx_mod.knx_recent_telegrams(hass, {})
        text = result["content"][0]["text"]
        assert "not initialized" in text
        assert "version" not in text

    async def test_store_query_error_reports_the_database_error(self):
        hass = self._hass_with_store(
            _TELEGRAMS, query_side_effect=knx_mod.KnxTelegramStoreException("db locked")
        )
        with patch.object(knx_mod, "TelegramQuery", Mock(side_effect=lambda **kw: kw)):
            result = await knx_mod.knx_recent_telegrams(hass, {})
        text = result["content"][0]["text"]
        assert "database error" in text
        assert "db locked" in text
        assert "version" not in text

    async def test_falls_back_to_in_memory_list_when_store_library_missing(self):
        """Without the store library the in-memory list is the history, store or not."""
        hass = self._hass_with_store(_TELEGRAMS)
        hass.data[_KEY].telegrams.recent_telegrams = _TELEGRAMS
        with patch.object(knx_mod, "TelegramQuery", None):
            result = await knx_mod.knx_recent_telegrams(hass, {})
        data = _unpack(result)
        assert data["buffer_size"] == 3
        hass.data[_KEY].telegrams.store.query.assert_not_awaited()


class TestKnxEntityTools:
    """Test the KNX base-data / entity read+write tools."""

    @pytest.fixture(autouse=True)
    def _patch_key(self):
        with patch.object(knx_mod, "KNX_MODULE_KEY", _KEY):
            yield

    def _hass(self, module):
        hass = Mock()
        hass.data = {_KEY: module}
        return hass

    async def test_get_base_data_fields(self):
        module = Mock()
        module.xknx.version = "3.0.0"
        module.xknx.connection_manager.connected.is_set = Mock(return_value=True)
        module.xknx.current_address = "1.0.255"
        module.project.info = {"name": "Haus"}
        result = await knx_mod.knx_get_base_data(self._hass(module), {})
        data = _unpack(result)
        assert data["connection"]["connected"] is True
        assert data["connection"]["current_address"] == "1.0.255"
        assert data["xknx_version"] == "3.0.0"
        assert data["project_info"] == {"name": "Haus"}

    async def test_get_entities_filter(self):
        module = Mock()
        module.group_address_entities = {
            "0/0/249": ["light.gt_taster"],
            "0/1/1": ["light.wz"],
        }
        result = await knx_mod.knx_get_entities(self._hass(module), {"filter_ga": "^0/0/249$"})
        data = _unpack(result)
        assert data["count"] == 1
        assert data["entities_by_group"][0]["group_address"] == "0/0/249"
        assert data["entities_by_group"][0]["entities"] == ["light.gt_taster"]

    async def test_get_entities_limit_zero_clamped(self):
        """limit=0 on knx_get_entities must not silently become 200."""
        module = Mock()
        module.group_address_entities = {f"0/0/{i}": [f"light.x{i}"] for i in range(5)}
        result = await knx_mod.knx_get_entities(self._hass(module), {"limit": 0})
        data = _unpack(result)
        assert len(data["entities_by_group"]) == 1

    async def test_get_entities_not_setup(self):
        hass = Mock()
        hass.data = {}
        result = await knx_mod.knx_get_entities(hass, {})
        assert "content" in result

    async def test_create_entity_calls_config_store(self):
        module = Mock()
        module.config_store.create_entity = AsyncMock(return_value="light.knx_new")
        result = await knx_mod.knx_create_entity(
            self._hass(module), {"platform": "light", "data": {"name": "x"}}
        )
        data = _unpack(result)
        assert data["created"] is True
        assert data["entity_id"] == "light.knx_new"
        module.config_store.create_entity.assert_awaited_once_with("light", {"name": "x"})

    async def test_create_entity_requires_args(self):
        result = await knx_mod.knx_create_entity(self._hass(Mock()), {"platform": "light"})
        assert "content" in result
        assert "required" in result["content"][0]["text"]

    async def test_update_entity_calls_config_store(self):
        module = Mock()
        module.config_store.update_entity = AsyncMock(return_value=None)
        result = await knx_mod.knx_update_entity(
            self._hass(module), {"entity_id": "light.x", "platform": "light", "data": {"a": 1}}
        )
        data = _unpack(result)
        assert data["updated"] is True
        module.config_store.update_entity.assert_awaited_once_with("light", "light.x", {"a": 1})

    async def test_delete_entity_calls_config_store(self):
        module = Mock()
        module.config_store.delete_entity = AsyncMock(return_value=None)
        result = await knx_mod.knx_delete_entity(self._hass(module), {"entity_id": "light.x"})
        data = _unpack(result)
        assert data["deleted"] is True
        module.config_store.delete_entity.assert_awaited_once_with("light.x")

    async def test_delete_entity_requires_id(self):
        result = await knx_mod.knx_delete_entity(self._hass(Mock()), {})
        assert "content" in result

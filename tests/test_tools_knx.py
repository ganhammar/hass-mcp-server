"""Tests for KNX telegram-history tools."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

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
    """Test knx_recent_telegrams."""

    @pytest.fixture(autouse=True)
    def _patch_key(self):
        with patch.object(knx_mod, "KNX_MODULE_KEY", _KEY):
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

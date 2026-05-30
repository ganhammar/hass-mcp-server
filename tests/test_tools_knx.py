"""Tests for KNX telegram-history tools."""

from unittest.mock import Mock, patch

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
        assert result["buffer_size"] == 3
        assert result["matched"] == 3
        assert result["buffer_span"]["oldest"] == "2026-05-29T21:00:00+02:00"
        assert result["buffer_span"]["newest"] == "2026-05-29T21:06:00+02:00"

    async def test_filter_ga(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"filter_ga": "^0/0/249$"})
        assert result["matched"] == 2
        assert all(t["destination"] == "0/0/249" for t in result["telegrams"])
        # source device of the flapping GA is surfaced
        assert result["telegrams"][0]["source_name"] == "MDT Logic Module"

    async def test_filter_name_case_insensitive(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"filter_name": "licht"})
        assert result["matched"] == 1
        assert result["telegrams"][0]["destination"] == "0/1/1"

    async def test_limit_keeps_most_recent(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"limit": 1})
        assert result["returned"] == 1
        assert result["telegrams"][0]["timestamp"] == "2026-05-29T21:06:00+02:00"

    async def test_invalid_regex_returns_error(self):
        hass = _hass_with_knx(_TELEGRAMS)
        result = await knx_mod.knx_recent_telegrams(hass, {"filter_ga": "("})
        assert "content" in result
        assert "Invalid regex" in result["content"][0]["text"]

    async def test_history_unavailable_attribute_error(self):
        module = Mock()
        # recent_telegrams raises AttributeError on access
        type(module.telegrams).recent_telegrams = property(
            lambda self: (_ for _ in ()).throw(AttributeError())
        )
        hass = Mock()
        hass.data = {_KEY: module}
        result = await knx_mod.knx_recent_telegrams(hass, {})
        assert "content" in result
        assert "unavailable" in result["content"][0]["text"]

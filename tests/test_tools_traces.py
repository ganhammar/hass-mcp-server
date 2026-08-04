"""Tests for automation/script trace tools."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import Context
from homeassistant.util import dt as dt_util

from custom_components.mcp_server_http_transport.tools.traces import (
    get_trace,
    list_traces,
)

_TRACE_UTIL = "homeassistant.components.trace.util"
_ER = "custom_components.mcp_server_http_transport.tools.traces.er"


def _make_hass() -> Mock:
    return Mock()


def _registry(unique_id: str | None):
    """Mock entity registry whose async_get returns an entry (or None)."""
    registry = Mock()
    if unique_id is None:
        registry.async_get.return_value = None
    else:
        entry = Mock()
        entry.unique_id = unique_id
        registry.async_get.return_value = entry
    return registry


def _summary(run_id: str, start: datetime, *, state: str = "stopped", **extra):
    return {
        "run_id": run_id,
        "state": state,
        "timestamp": {"start": start, "finish": start + timedelta(seconds=1)},
        "domain": "automation",
        "item_id": "1718",
        **extra,
    }


_T0 = datetime(2026, 1, 1, 8, 0, 0)


class TestListTraces:
    async def test_by_entity_id_resolves_unique_id_key(self):
        """automation.morning must resolve to its registry unique_id, not the slug."""
        hass = _make_hass()
        captured = {}

        async def fake_list(hass_arg, domain, key):
            captured["domain"] = domain
            captured["key"] = key
            return [_summary("a", _T0)]

        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_list_traces", side_effect=fake_list),
        ):
            result = await list_traces(hass, {"entity_id": "automation.morning"})

        assert captured == {"domain": "automation", "key": "automation.1718"}
        assert "a" in result["content"][0]["text"]

    async def test_newest_first_and_limit(self):
        hass = _make_hass()
        summaries = [
            _summary("old", _T0),
            _summary("new", _T0 + timedelta(hours=2)),
            _summary("mid", _T0 + timedelta(hours=1)),
        ]
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_list_traces", AsyncMock(return_value=summaries)),
        ):
            result = await list_traces(hass, {"entity_id": "automation.morning", "limit": 2})

        run_ids = [t["run_id"] for t in json.loads(result["content"][0]["text"])]
        assert run_ids == ["new", "mid"]

    async def test_orders_restored_and_live_timestamps_together(self):
        """After a restart one key holds restored traces (ISO-string start) and live
        ones (tz-aware datetime). Sorting must not raise on the mixed types."""
        restored = {
            "run_id": "restored",
            "state": "stopped",
            "timestamp": {"start": "2026-01-01T08:00:00+00:00", "finish": None},
            "domain": "automation",
            "item_id": "1718",
        }
        live = {
            "run_id": "live",
            "state": "stopped",
            "timestamp": {"start": datetime(2026, 1, 1, 9, 0, tzinfo=dt_util.UTC), "finish": None},
            "domain": "automation",
            "item_id": "1718",
        }
        # A still-running trace can have no start yet — it must sort last, not raise.
        no_ts = {"run_id": "nostart", "state": "running", "timestamp": {}, "item_id": "1718"}
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(
                f"{_TRACE_UTIL}.async_list_traces",
                AsyncMock(return_value=[restored, no_ts, live]),
            ),
        ):
            result = await list_traces(_make_hass(), {"entity_id": "automation.morning"})

        run_ids = [t["run_id"] for t in json.loads(result["content"][0]["text"])]
        assert run_ids == ["live", "restored", "nostart"]

    async def test_by_domain_lists_all(self):
        hass = _make_hass()
        captured = {}

        async def fake_list(hass_arg, domain, key):
            captured["domain"] = domain
            captured["key"] = key
            return []

        with patch(f"{_TRACE_UTIL}.async_list_traces", side_effect=fake_list):
            result = await list_traces(hass, {"domain": "script"})

        assert captured == {"domain": "script", "key": None}
        assert json.loads(result["content"][0]["text"]) == []

    async def test_requires_entity_or_domain(self):
        result = await list_traces(_make_hass(), {})
        assert "provide either" in result["content"][0]["text"]

    async def test_domain_mismatch_is_rejected(self):
        with patch(f"{_ER}.async_get", return_value=_registry("1718")):
            result = await list_traces(
                _make_hass(), {"entity_id": "automation.morning", "domain": "script"}
            )
        assert "does not match" in result["content"][0]["text"]

    async def test_invalid_limit_rejected(self):
        with patch(f"{_ER}.async_get", return_value=_registry("1718")):
            result = await list_traces(
                _make_hass(), {"entity_id": "automation.morning", "limit": 0}
            )
        assert "limit must be an integer >= 1" in result["content"][0]["text"]

    async def test_bad_entity_id_rejected(self):
        result = await list_traces(_make_hass(), {"entity_id": "notanentity"})
        assert "not a valid entity_id" in result["content"][0]["text"]

    async def test_non_trace_domain_rejected(self):
        result = await list_traces(_make_hass(), {"entity_id": "light.kitchen"})
        assert "only for automations and scripts" in result["content"][0]["text"]

    async def test_invalid_domain_only_rejected(self):
        """domain given without entity_id must still be a real trace domain."""
        result = await list_traces(_make_hass(), {"domain": "light"})
        assert "domain must be one of" in result["content"][0]["text"]

    async def test_unexpected_error_is_reported(self):
        """A failure from the trace store (e.g. not set up) degrades to a message."""
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(
                f"{_TRACE_UTIL}.async_list_traces",
                AsyncMock(side_effect=RuntimeError("trace store missing")),
            ),
        ):
            result = await list_traces(_make_hass(), {"entity_id": "automation.morning"})
        assert "Error listing traces" in result["content"][0]["text"]


class TestGetTrace:
    async def test_defaults_to_most_recent_run(self):
        """run_id omitted must fetch the newest run's extended trace."""
        hass = _make_hass()
        summaries = [
            _summary("old", _T0),
            _summary("new", _T0 + timedelta(hours=1)),
        ]
        captured = {}

        async def fake_get(hass_arg, key, run_id):
            captured["key"] = key
            captured["run_id"] = run_id
            return {"run_id": run_id, "trace": {"trigger": []}, "config": {}}

        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_list_traces", AsyncMock(return_value=summaries)),
            patch(f"{_TRACE_UTIL}.async_get_trace", side_effect=fake_get),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning"})

        assert captured == {"key": "automation.1718", "run_id": "new"}
        assert json.loads(result["content"][0]["text"])["run_id"] == "new"

    async def test_serializes_real_extended_trace_with_context(self):
        """A real extended trace carries a Context object and datetimes that the plain
        datetime encoder can't handle — get_trace must serialize them, not error."""
        hass = _make_hass()
        extended = {
            "run_id": "r1",
            "timestamp": {
                "start": datetime(2026, 1, 1, 9, 0, tzinfo=dt_util.UTC),
                "finish": datetime(2026, 1, 1, 9, 0, 1, tzinfo=dt_util.UTC),
            },
            "trace": {"trigger/0": [{"path": "trigger/0", "timestamp": dt_util.utcnow()}]},
            "config": {"alias": "Morning"},
            "context": Context(user_id="u1"),
        }
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_get_trace", AsyncMock(return_value=extended)),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning", "run_id": "r1"})

        text = result["content"][0]["text"]
        assert "Error" not in text
        payload = json.loads(text)
        # Context serialized to a dict carrying its id, not stringified or dropped.
        assert payload["context"]["id"] == extended["context"].id
        assert payload["config"]["alias"] == "Morning"

    async def test_summary_outlines_steps_without_bulk(self):
        """summary=true keeps the step skeleton but drops config and variable bodies."""
        hass = _make_hass()
        extended = {
            "run_id": "r1",
            "state": "stopped",
            "trace": {
                "trigger/0": [{"path": "trigger/0", "result": {"result": True}}],
                "condition/0": [
                    {
                        "path": "condition/0",
                        "result": {"result": False},
                        "changed_variables": {"trigger": {"big": "payload"}, "this": {}},
                    }
                ],
            },
            "config": {"alias": "Morning", "action": ["...lots..."]},
            "blueprint_inputs": {"x": 1},
            "context": Context(user_id="u1"),
        }
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_get_trace", AsyncMock(return_value=extended)),
        ):
            result = await get_trace(
                hass, {"entity_id": "automation.morning", "run_id": "r1", "summary": True}
            )

        payload = json.loads(result["content"][0]["text"])
        # Bulk dropped.
        assert "config" not in payload
        assert "blueprint_inputs" not in payload
        cond = payload["trace"]["condition/0"][0]
        assert "changed_variables" not in cond
        # Skeleton kept: result (why the condition failed) and the changed var names.
        assert cond["result"] == {"result": False}
        assert cond["changed_variables_keys"] == ["this", "trigger"]
        # Top-level context and state survive.
        assert payload["context"]["id"] == extended["context"].id
        assert payload["state"] == "stopped"

    async def test_summary_passes_through_unexpected_step_shape(self):
        """A step element that isn't a dict must not crash the outline."""
        hass = _make_hass()
        extended = {
            "run_id": "r1",
            "trace": {"weird/0": ["not-a-dict"]},
            "config": {"alias": "x"},
            "context": Context(user_id="u1"),
        }
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_get_trace", AsyncMock(return_value=extended)),
        ):
            result = await get_trace(
                hass, {"entity_id": "automation.morning", "run_id": "r1", "summary": True}
            )

        payload = json.loads(result["content"][0]["text"])
        assert payload["trace"]["weird/0"] == ["not-a-dict"]

    async def test_full_is_the_default(self):
        """Without summary, the config and variable bodies are present."""
        hass = _make_hass()
        extended = {
            "run_id": "r1",
            "trace": {"condition/0": [{"path": "condition/0", "changed_variables": {"a": 1}}]},
            "config": {"alias": "Morning"},
            "context": Context(user_id="u1"),
        }
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_get_trace", AsyncMock(return_value=extended)),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning", "run_id": "r1"})

        payload = json.loads(result["content"][0]["text"])
        assert payload["config"] == {"alias": "Morning"}
        assert payload["trace"]["condition/0"][0]["changed_variables"] == {"a": 1}

    async def test_explicit_run_id_is_used(self):
        hass = _make_hass()
        list_mock = AsyncMock(return_value=[])

        async def fake_get(hass_arg, key, run_id):
            return {"run_id": run_id, "trace": {}}

        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_list_traces", list_mock),
            patch(f"{_TRACE_UTIL}.async_get_trace", side_effect=fake_get),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning", "run_id": "abc123"})

        # With an explicit run_id we must not need to list first.
        list_mock.assert_not_called()
        assert json.loads(result["content"][0]["text"])["run_id"] == "abc123"

    async def test_no_traces_gives_friendly_message(self):
        hass = _make_hass()
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_list_traces", AsyncMock(return_value=[])),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning"})

        text = result["content"][0]["text"]
        assert "No traces found" in text
        assert "hasn't run recently" in text

    async def test_unknown_run_id_reports_that_run_id(self):
        hass = _make_hass()
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(f"{_TRACE_UTIL}.async_get_trace", AsyncMock(side_effect=KeyError("nope"))),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning", "run_id": "ghost"})

        text = result["content"][0]["text"]
        assert "run_id 'ghost'" in text
        assert "list_traces" in text

    async def test_derived_run_vanishing_falls_back_to_no_traces(self):
        """If the newest run disappears between list and get, report 'no traces', not a run_id."""
        hass = _make_hass()
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(
                f"{_TRACE_UTIL}.async_list_traces",
                AsyncMock(return_value=[_summary("gone", _T0)]),
            ),
            patch(f"{_TRACE_UTIL}.async_get_trace", AsyncMock(side_effect=KeyError("race"))),
        ):
            result = await get_trace(hass, {"entity_id": "automation.morning"})

        text = result["content"][0]["text"]
        assert "No traces found" in text
        assert "run_id" not in text

    async def test_unregistered_entity_falls_back_to_object_id(self):
        """A YAML script not in the registry should still resolve via its object-id."""
        hass = _make_hass()
        captured = {}

        async def fake_get(hass_arg, key, run_id):
            captured["key"] = key
            return {"run_id": run_id}

        with (
            patch(f"{_ER}.async_get", return_value=_registry(None)),
            patch(f"{_TRACE_UTIL}.async_get_trace", side_effect=fake_get),
        ):
            result = await get_trace(hass, {"entity_id": "script.bedtime", "run_id": "r1"})

        assert captured["key"] == "script.bedtime"
        assert json.loads(result["content"][0]["text"])["run_id"] == "r1"

    async def test_bad_entity_id_rejected(self):
        result = await get_trace(_make_hass(), {"entity_id": "light.kitchen"})
        assert "only for automations and scripts" in result["content"][0]["text"]

    async def test_unexpected_error_is_reported(self):
        with (
            patch(f"{_ER}.async_get", return_value=_registry("1718")),
            patch(
                f"{_TRACE_UTIL}.async_get_trace",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = await get_trace(
                _make_hass(), {"entity_id": "automation.morning", "run_id": "r1"}
            )
        assert "Error getting trace" in result["content"][0]["text"]

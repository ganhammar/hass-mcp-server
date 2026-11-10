"""Tests for the read-only diagnostics tools.

Repairs run against the real issue registry and this integration's own
translations, system health against the real component, and backups against the
real backup manager, so the shapes read here are Home Assistant's own.
"""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools import TOOLS, diagnostics, tool_is_read_only
from custom_components.mcp_server_http_transport.tools.diagnostics import (
    get_system_health,
    list_backups,
    list_repairs,
)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _json(result: dict):
    return json.loads(_text(result))


def test_the_tools_are_read_only():
    tools = {"list_repairs", "get_system_health", "list_backups"}

    assert tools <= set(TOOLS)
    assert all(tool_is_read_only(name) for name in tools)


class TestListRepairs:
    def _raise(self, hass: HomeAssistant, issue_id: str = "endpoint_conflict", **overrides) -> None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            **{
                "is_fixable": False,
                "severity": ir.IssueSeverity.WARNING,
                "translation_key": "endpoint_conflict",
                "translation_placeholders": {"path": "/api/mcp", "dedicated_path": "/api/x"},
                "learn_more_url": "https://example.com/more",
                **overrides,
            },
        )

    async def test_an_issue_is_listed_with_its_translated_text(
        self, hass: HomeAssistant, enable_custom_integrations: None
    ):
        self._raise(hass)

        (issue,) = _json(await list_repairs(hass, {}))

        assert issue["title"] == "Another integration also serves /api/mcp"
        assert "Point your MCP client at `/api/x`" in issue["description"]
        assert {key: issue[key] for key in ("domain", "issue_id", "severity", "is_fixable")} == {
            "domain": DOMAIN,
            "issue_id": "endpoint_conflict",
            "severity": "warning",
            "is_fixable": False,
        }
        assert issue["ignored"] is False
        assert issue["learn_more_url"] == "https://example.com/more"
        assert datetime.fromisoformat(issue["created"])

    async def test_an_issue_without_a_translation_has_no_text(
        self, hass: HomeAssistant, enable_custom_integrations: None
    ):
        self._raise(hass, "mystery", translation_key="no_such_key")

        (issue,) = _json(await list_repairs(hass, {}))

        assert (issue["title"], issue["description"]) == (None, None)

    async def test_the_text_comes_from_the_integration_that_raised_the_issue(
        self, hass: HomeAssistant
    ):
        """Core raises issues about other integrations, named in issue_domain."""
        ir.async_create_issue(
            hass,
            "homeassistant",
            "config_entry_reauth_gadget_1",
            issue_domain="gadget",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="config_entry_reauth",
            translation_placeholders={"name": "My gadget"},
        )

        (issue,) = _json(await list_repairs(hass, {}))

        assert (issue["domain"], issue["issue_domain"]) == ("homeassistant", "gadget")
        assert issue["title"] == "Authentication expired for My gadget"
        assert issue["description"]

    async def test_a_fixable_issue_takes_its_text_from_its_fix_flow(self, hass: HomeAssistant):
        self._raise(hass, "fixable", translation_key="fixable", is_fixable=True)
        texts = {
            f"component.{DOMAIN}.issues.fixable.title": "Old setting for {path}",
            f"component.{DOMAIN}.issues.fixable.fix_flow.step.zeta.description": "Later step",
            f"component.{DOMAIN}.issues.fixable.fix_flow.step.confirm.description": "Fix {path}?",
            f"component.{DOMAIN}.issues.fixable.fix_flow.step.confirm.title": "Unused",
        }

        with patch(
            "homeassistant.helpers.translation.async_get_translations",
            AsyncMock(return_value=texts),
        ):
            (issue,) = _json(await list_repairs(hass, {}))

        assert issue["title"] == "Old setting for /api/mcp"
        assert issue["description"] == "Fix /api/mcp?"
        assert issue["is_fixable"] is True

    async def test_a_fix_flow_without_the_usual_first_step_gives_its_first_by_name(
        self, hass: HomeAssistant
    ):
        self._raise(hass, "fixable", translation_key="fixable", is_fixable=True)
        texts = {
            f"component.{DOMAIN}.issues.fixable.fix_flow.step.b.description": "B",
            f"component.{DOMAIN}.issues.fixable.fix_flow.step.a.description": "A",
        }

        with patch(
            "homeassistant.helpers.translation.async_get_translations",
            AsyncMock(return_value=texts),
        ):
            (issue,) = _json(await list_repairs(hass, {}))

        assert (issue["title"], issue["description"]) == (None, "A")

    async def test_ignored_issues_are_left_out_unless_asked_for(
        self, hass: HomeAssistant, enable_custom_integrations: None
    ):
        self._raise(hass)
        ir.async_ignore_issue(hass, DOMAIN, "endpoint_conflict", True)

        assert _json(await list_repairs(hass, {})) == []
        (issue,) = _json(await list_repairs(hass, {"include_ignored": True}))
        assert issue["ignored"] is True

    async def test_no_issues_is_an_empty_list(self, hass: HomeAssistant):
        assert _json(await list_repairs(hass, {})) == []


class TestSystemHealth:
    async def test_reports_what_integrations_say_about_themselves(self, hass: HomeAssistant):
        assert await async_setup_component(hass, "system_health", {})

        async def reachable():
            return "ok"

        async def info(hass):
            return {"version": "1.2.3", "checked": datetime(2026, 1, 2, 3, 4), "api": reachable()}

        from homeassistant.components import system_health

        registration = system_health.SystemHealthRegistration(hass, "gadget")
        registration.async_register_info(info)
        hass.data[system_health.DOMAIN]["gadget"] = registration

        report = _json(await get_system_health(hass, {}))

        assert report["gadget"] == {
            "version": "1.2.3",
            "checked": "2026-01-02T03:04:00",
            "api": "ok",
        }

    async def test_credentials_in_what_an_integration_reports_are_masked(self, hass: HomeAssistant):
        assert await async_setup_component(hass, "system_health", {})
        report = {
            "gadget": {
                "api": "Exception: GET https://user:pw@example.com failed",
                "n": 1,
                "servers": ["https://a:b@one.example.com", "two.example.com"],
            }
        }

        with patch(
            "homeassistant.components.system_health.get_info", AsyncMock(return_value=report)
        ):
            result = _json(await get_system_health(hass, {}))

        assert "pw@" not in result["gadget"]["api"]
        assert result["gadget"]["api"].endswith("example.com failed")
        assert result["gadget"]["n"] == 1
        assert "a:b@" not in result["gadget"]["servers"][0]
        assert result["gadget"]["servers"][1] == "two.example.com"

    async def test_gives_up_on_a_report_that_does_not_come(self, hass: HomeAssistant):
        assert await async_setup_component(hass, "system_health", {})

        async def never(hass):
            await asyncio.sleep(3600)

        with (
            patch("homeassistant.components.system_health.get_info", never),
            patch.object(diagnostics, "SYSTEM_HEALTH_TIMEOUT", 0.01),
        ):
            text = _text(await get_system_health(hass, {}))

        assert text.startswith("System health did not answer within")

    async def test_says_so_when_the_component_is_not_loaded(self, hass: HomeAssistant):
        text = _text(await get_system_health(hass, {}))

        assert text.startswith("System health is not available")


class TestListBackups:
    @pytest.fixture
    def manager(self, backup_manager):
        return backup_manager

    def _backup(self, backup_id: str, date: str, **overrides):
        from homeassistant.components.backup import AddonInfo, Folder
        from homeassistant.components.backup.manager import AgentBackupStatus, ManagerBackup

        return ManagerBackup(
            **{
                "addons": [AddonInfo(name="Mosquitto", slug="mqtt", version="1")],
                "backup_id": backup_id,
                "date": date,
                "database_included": True,
                "extra_metadata": {},
                "folders": [Folder.MEDIA],
                "homeassistant_included": True,
                "homeassistant_version": "2026.1.0",
                "name": f"Backup {backup_id}",
                "agents": {"backup.local": AgentBackupStatus(protected=True, size=1234)},
                "failed_addons": [],
                "failed_agent_ids": ["cloud.cloud"],
                "failed_folders": [],
                "with_automatic_settings": True,
                **overrides,
            }
        )

    async def test_an_instance_without_backups_reports_none(self, hass, manager):
        result = _json(await list_backups(hass, {}))

        assert result["backups"] == []
        assert result["total"] == 0
        assert result["state"] == "idle"
        assert result["location_errors"] == {}
        assert {
            "last_attempted_automatic_backup",
            "last_completed_automatic_backup",
            "next_automatic_backup",
        } <= set(result)

    async def test_backups_are_listed_newest_first_up_to_the_limit(self, hass, manager):
        backups = {
            "old": self._backup("old", "2026-01-01T00:00:00+00:00"),
            "new": self._backup("new", "2026-03-01T00:00:00+00:00"),
            "mid": self._backup("mid", "2026-02-01T00:00:00+00:00"),
        }
        errors = {"cloud.cloud": RuntimeError("GET https://user:pw@nas.local/list: not signed in")}

        with patch.object(
            type(manager), "async_get_backups", AsyncMock(return_value=(backups, errors))
        ):
            result = _json(await list_backups(hass, {"limit": 2}))

        assert [backup["backup_id"] for backup in result["backups"]] == ["new", "mid"]
        assert result["total"] == 3
        (error,) = result["location_errors"].values()
        assert "pw@" not in error
        assert error.endswith("nas.local/list: not signed in")
        assert result["backups"][0] == {
            "backup_id": "new",
            "name": "Backup new",
            "date": "2026-03-01T00:00:00+00:00",
            "automatic": True,
            "homeassistant_version": "2026.1.0",
            "database_included": True,
            "addons": ["Mosquitto"],
            "folders": ["media"],
            "locations": {"backup.local": {"size": 1234, "protected": True}},
            "failed_locations": ["cloud.cloud"],
        }

    async def test_backups_are_ordered_by_time_not_by_how_the_date_is_written(self, hass, manager):
        backups = {
            # 01:00+02:00 is 23:00 UTC the day before, so it is the older of the two.
            "offset": self._backup("offset", "2026-03-02T01:00:00+02:00"),
            "utc": self._backup("utc", "2026-03-01T23:30:00+00:00"),
            "naive": self._backup("naive", "2020-01-01T00:00:00"),
            "broken": self._backup("broken", "not a date"),
        }

        with patch.object(
            type(manager), "async_get_backups", AsyncMock(return_value=(backups, {}))
        ):
            result = _json(await list_backups(hass, {}))

        assert [backup["backup_id"] for backup in result["backups"]] == [
            "utc",
            "offset",
            "naive",
            "broken",
        ]

    @pytest.mark.parametrize("limit", [0, 201, "5", True])
    async def test_an_invalid_limit_is_refused(self, hass, manager, limit):
        text = _text(await list_backups(hass, {"limit": limit}))

        assert text == "limit must be an integer between 1 and 200."

    async def test_says_so_when_the_component_is_not_loaded(self, hass: HomeAssistant):
        text = _text(await list_backups(hass, {}))

        assert text.startswith("Backups are not available")

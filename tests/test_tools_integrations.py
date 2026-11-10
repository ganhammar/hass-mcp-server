"""Tests for the config flow and config entry tools.

These run against the real config entries flow manager, with a stand-in
integration whose flow walks through every result type the tools report.
"""

import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState, ConfigFlow, OperationNotAllowed
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownStep
from homeassistant.exceptions import DependencyError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools import call_tool

DEMO = "mcp_demo"
_TOOLS = "custom_components.mcp_server_http_transport.tools.integrations"

_FORM = vol.Schema({vol.Required("host"): str, vol.Optional("port", default=80): int})


class _DemoFlow(ConfigFlow):
    """A flow whose first step is a menu leading to each kind of step."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["manual", "oauth", "taken"])

    async def async_step_manual(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="manual", data_schema=_FORM, last_step=True)
        if user_input["host"] == "unreachable":
            return self.async_show_form(
                step_id="manual", data_schema=_FORM, errors={"base": "cannot_connect"}
            )
        return self.async_create_entry(title=user_input["host"], data=user_input)

    async def async_step_oauth(self, user_input=None):
        return self.async_external_step(step_id="oauth", url="https://example.com/authorize")

    async def async_step_taken(self, user_input=None):
        return self.async_abort(reason="already_configured")


@pytest.fixture
def demo(hass: HomeAssistant) -> Iterator[None]:
    """Enable the tools and register the stand-in integration."""
    hass.data[DOMAIN] = {"integration_management": True}
    mock_integration(
        hass,
        MockModule(
            DEMO,
            async_setup_entry=AsyncMock(return_value=True),
            async_unload_entry=AsyncMock(return_value=True),
        ),
    )
    mock_platform(hass, f"{DEMO}.config_flow", None)
    with mock_config_flow(DEMO, _DemoFlow):
        yield


async def _call(hass: HomeAssistant, name: str, **arguments) -> str:
    result = await call_tool(hass, name, arguments)
    assert "isError" not in result, result
    return result["content"][0]["text"]


async def _manual_step(hass: HomeAssistant) -> str:
    """Start a flow and take the menu to the form, returning the flow ID."""
    flow_id = json.loads(await _call(hass, "start_config_flow", domain=DEMO))["flow_id"]
    await _call(
        hass, "continue_config_flow", flow_id=flow_id, user_input={"next_step_id": "manual"}
    )
    return flow_id


class TestDisabled:
    @pytest.mark.parametrize(
        "data", [{}, {DOMAIN: {}}, {DOMAIN: {"integration_management": False}}]
    )
    @pytest.mark.parametrize(
        "name, arguments",
        [
            ("start_config_flow", {"domain": DEMO}),
            ("continue_config_flow", {"flow_id": "x", "user_input": {}}),
            ("abort_config_flow", {"flow_id": "x"}),
            ("reload_integration", {"entry_id": "x"}),
            ("delete_integration", {"entry_id": "x", "confirm": True}),
        ],
    )
    async def test_every_tool_is_off_until_the_option_is_on(
        self, hass: HomeAssistant, data, name, arguments
    ):
        hass.data.update(data)

        text = await _call(hass, name, **arguments)

        assert text.startswith("Integration management is disabled")
        assert hass.config_entries.flow.async_progress() == []


class TestStartConfigFlow:
    async def test_returns_the_first_step(self, hass: HomeAssistant, demo: None):
        result = json.loads(await _call(hass, "start_config_flow", domain=DEMO))

        (flow,) = hass.config_entries.flow.async_progress()
        assert result["flow_id"] == flow["flow_id"]
        assert result["type"] == "menu"
        assert result["domain"] == DEMO
        assert result["step_id"] == "user"
        assert result["menu_options"] == ["manual", "oauth", "taken"]
        assert "next_step_id" in result["next"]

    async def test_unknown_domain_is_reported(self, hass: HomeAssistant, demo: None):
        text = await _call(hass, "start_config_flow", domain="no_such_integration")

        assert "No integration with a config flow found for domain 'no_such_integration'" in text

    async def test_failed_dependencies_are_reported(self, hass: HomeAssistant, demo: None):
        with patch.object(
            hass.config_entries.flow, "async_init", side_effect=DependencyError(["http", "usb"])
        ):
            text = await _call(hass, "start_config_flow", domain=DEMO)

        assert "its dependencies failed to set up (http, usb)" in text

    async def test_a_flow_whose_first_step_cannot_be_reported_is_not_left_behind(
        self, hass: HomeAssistant, demo: None
    ):
        with patch(f"{_TOOLS}._flow_result_json", side_effect=ValueError("no serializer")):
            result = await call_tool(hass, "start_config_flow", {"domain": DEMO})

        assert result["isError"] is True
        assert hass.config_entries.flow.async_progress() == []

    async def test_single_instance_integration_already_set_up_aborts(self, hass: HomeAssistant):
        hass.data[DOMAIN] = {"integration_management": True}
        MockConfigEntry(domain="sun").add_to_hass(hass)

        result = json.loads(await _call(hass, "start_config_flow", domain="sun"))

        assert result["type"] == "abort"
        assert result["reason"] == "single_instance_allowed"
        assert "next" not in result


class TestContinueConfigFlow:
    async def test_menu_choice_leads_to_the_form_with_its_schema(
        self, hass: HomeAssistant, demo: None
    ):
        flow_id = json.loads(await _call(hass, "start_config_flow", domain=DEMO))["flow_id"]

        result = json.loads(
            await _call(
                hass,
                "continue_config_flow",
                flow_id=flow_id,
                user_input={"next_step_id": "manual"},
            )
        )

        assert result["type"] == "form"
        assert result["step_id"] == "manual"
        assert result["data_schema"] == [
            {"name": "host", "required": True, "type": "string"},
            {"name": "port", "required": False, "optional": True, "default": 80, "type": "integer"},
        ]
        assert result["last_step"] is True
        assert "errors" not in result

    async def test_submitting_the_form_creates_the_entry_without_echoing_its_data(
        self, hass: HomeAssistant, demo: None
    ):
        flow_id = await _manual_step(hass)

        text = await _call(
            hass, "continue_config_flow", flow_id=flow_id, user_input={"host": "s3cret.local"}
        )

        (entry,) = hass.config_entries.async_entries(DEMO)
        assert entry.data == {"host": "s3cret.local", "port": 80}
        assert json.loads(text) == {
            "type": "create_entry",
            "flow_id": flow_id,
            "domain": DEMO,
            "entry": {
                "entry_id": entry.entry_id,
                "domain": DEMO,
                "title": "s3cret.local",
                "state": str(ConfigEntryState.LOADED),
            },
        }
        assert hass.config_entries.flow.async_progress() == []

    async def test_step_errors_come_back_with_the_form(self, hass: HomeAssistant, demo: None):
        flow_id = await _manual_step(hass)

        result = json.loads(
            await _call(
                hass, "continue_config_flow", flow_id=flow_id, user_input={"host": "unreachable"}
            )
        )

        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_input_the_schema_rejects_leaves_the_flow_on_its_step(
        self, hass: HomeAssistant, demo: None
    ):
        flow_id = await _manual_step(hass)

        text = await _call(
            hass, "continue_config_flow", flow_id=flow_id, user_input={"port": "eighty"}
        )

        assert "still on the same step" in text
        assert '"host"' in text and '"port"' in text
        (flow,) = hass.config_entries.flow.async_progress()
        assert flow["step_id"] == "manual"

    async def test_omitting_user_input_re_reads_the_step(self, hass: HomeAssistant, demo: None):
        flow_id = await _manual_step(hass)

        result = json.loads(await _call(hass, "continue_config_flow", flow_id=flow_id))

        assert result["type"] == "form"
        assert result["step_id"] == "manual"

    async def test_external_step_reports_its_url(self, hass: HomeAssistant, demo: None):
        flow_id = json.loads(await _call(hass, "start_config_flow", domain=DEMO))["flow_id"]

        result = json.loads(
            await _call(
                hass, "continue_config_flow", flow_id=flow_id, user_input={"next_step_id": "oauth"}
            )
        )

        assert result["type"] == "external"
        assert result["url"] == "https://example.com/authorize"
        assert "browser" in result["next"]

    async def test_abort_reports_its_reason(self, hass: HomeAssistant, demo: None):
        flow_id = json.loads(await _call(hass, "start_config_flow", domain=DEMO))["flow_id"]

        result = json.loads(
            await _call(
                hass, "continue_config_flow", flow_id=flow_id, user_input={"next_step_id": "taken"}
            )
        )

        assert result == {
            "type": "abort",
            "flow_id": flow_id,
            "domain": DEMO,
            "reason": "already_configured",
        }
        assert hass.config_entries.flow.async_progress() == []

    @pytest.mark.parametrize("user_input", [{"next_step_id": "nowhere"}, {"host": "example"}])
    async def test_a_wrong_menu_answer_is_turned_away_and_keeps_the_flow(
        self, hass: HomeAssistant, demo: None, user_input
    ):
        flow_id = json.loads(await _call(hass, "start_config_flow", domain=DEMO))["flow_id"]

        text = await _call(hass, "continue_config_flow", flow_id=flow_id, user_input=user_input)

        assert text.startswith("This step is a menu")
        assert '["manual","oauth","taken"]' in text
        (flow,) = hass.config_entries.flow.async_progress()
        assert flow["step_id"] == "user"

    async def test_a_flow_dropped_for_an_unknown_step_is_reported(
        self, hass: HomeAssistant, demo: None
    ):
        flow_id = await _manual_step(hass)

        with patch.object(
            hass.config_entries.flow, "async_configure", side_effect=UnknownStep("no step 'x'")
        ):
            text = await _call(hass, "continue_config_flow", flow_id=flow_id, user_input={})

        assert "Home Assistant dropped it" in text
        assert "no step 'x'" in text

    async def test_unknown_flow_is_reported(self, hass: HomeAssistant, demo: None):
        text = await _call(hass, "continue_config_flow", flow_id="gone", user_input={})

        assert "No config flow in progress with ID 'gone'" in text

    async def test_non_object_user_input_is_refused(self, hass: HomeAssistant, demo: None):
        flow_id = await _manual_step(hass)

        text = await _call(hass, "continue_config_flow", flow_id=flow_id, user_input="host")

        assert text == "user_input must be an object keyed by field name."


class TestAbortConfigFlow:
    async def test_removes_the_flow(self, hass: HomeAssistant, demo: None):
        flow_id = await _manual_step(hass)

        text = await _call(hass, "abort_config_flow", flow_id=flow_id)

        assert text == f"Aborted config flow '{flow_id}'"
        assert hass.config_entries.flow.async_progress() == []

    async def test_unknown_flow_is_reported(self, hass: HomeAssistant, demo: None):
        text = await _call(hass, "abort_config_flow", flow_id="gone")

        assert "No config flow in progress with ID 'gone'" in text


@pytest.fixture
async def demo_entry(hass: HomeAssistant, demo: None) -> MockConfigEntry:
    """A loaded config entry of the stand-in integration."""
    entry = MockConfigEntry(domain=DEMO, title="Demo")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


@pytest.fixture
def own_entry(hass: HomeAssistant, demo: None) -> MockConfigEntry:
    """The config entry of the MCP server itself."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    return entry


class TestReloadIntegration:
    async def test_reloads_the_entry(self, hass: HomeAssistant, demo_entry: MockConfigEntry):
        module = hass.data["integrations"][DEMO].get_component()
        module.async_setup_entry.reset_mock()

        result = json.loads(await _call(hass, "reload_integration", entry_id=demo_entry.entry_id))

        module.async_unload_entry.assert_awaited_once()
        module.async_setup_entry.assert_awaited_once()
        assert result == {
            "entry_id": demo_entry.entry_id,
            "domain": DEMO,
            "title": "Demo",
            "state": str(ConfigEntryState.LOADED),
            "require_restart": False,
        }

    async def test_unknown_entry_is_reported(self, hass: HomeAssistant, demo: None):
        text = await _call(hass, "reload_integration", entry_id="gone")

        assert "No config entry with ID 'gone'" in text

    async def test_an_entry_that_may_not_be_reloaded_is_reported(
        self, hass: HomeAssistant, demo_entry: MockConfigEntry
    ):
        with patch.object(
            hass.config_entries,
            "async_reload",
            side_effect=OperationNotAllowed("setup in progress"),
        ):
            text = await _call(hass, "reload_integration", entry_id=demo_entry.entry_id)

        assert text == f"Config entry '{demo_entry.entry_id}' cannot be reloaded: setup in progress"

    async def test_own_entry_is_refused(self, hass: HomeAssistant, own_entry: MockConfigEntry):
        text = await _call(hass, "reload_integration", entry_id=own_entry.entry_id)

        assert text.startswith("Refusing to reload the MCP Server's own config entry")


class TestDeleteIntegration:
    async def test_removes_the_entry(self, hass: HomeAssistant, demo_entry: MockConfigEntry):
        result = json.loads(
            await _call(hass, "delete_integration", entry_id=demo_entry.entry_id, confirm=True)
        )

        assert hass.config_entries.async_entries(DEMO) == []
        assert result == {
            "removed": {"entry_id": demo_entry.entry_id, "domain": DEMO, "title": "Demo"},
            "require_restart": False,
        }

    async def test_requires_confirmation(self, hass: HomeAssistant, demo_entry: MockConfigEntry):
        text = await _call(hass, "delete_integration", entry_id=demo_entry.entry_id, confirm=False)

        assert "requires confirm=true" in text
        assert hass.config_entries.async_entries(DEMO) == [demo_entry]

    async def test_unknown_entry_is_reported(self, hass: HomeAssistant, demo: None):
        text = await _call(hass, "delete_integration", entry_id="gone", confirm=True)

        assert "No config entry with ID 'gone'" in text

    async def test_own_entry_is_refused(self, hass: HomeAssistant, own_entry: MockConfigEntry):
        text = await _call(hass, "delete_integration", entry_id=own_entry.entry_id, confirm=True)

        assert text.startswith("Refusing to remove the MCP Server's own config entry")
        assert hass.config_entries.async_entries(DOMAIN) == [own_entry]

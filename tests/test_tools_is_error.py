"""Every tool module marks a call that did not do what was asked with isError.

A handler reports a failure it anticipated through error_result, and anything
else it returns is a success, including an empty listing, a preview and a no-op.
One failing call per module pins the first half, so a module whose failures
drift back to success-shaped text fails here; a few successes pin the second.
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools import TOOLS, call_tool, error_result

# One call per tool module that cannot do what it asks, by module name. Every
# option is off, as on a default setup, so the option-gated modules answer that
# they are disabled.
FAILING_CALLS = {
    "appdaemon_files": ("list_appdaemon_files", {}),
    "calendar": ("list_calendar_events", {"entity_id": "light.kitchen"}),
    "categories": ("set_category", {"entity_id": "automation.nope", "category": "Lights"}),
    "config": ("get_automation_config", {"automation_id": "nope"}),
    "config_files": ("list_config_files", {}),
    "config_search": ("search_config", {"query": "  "}),
    "dashboards": ("get_dashboard_config", {"url_path": "nope"}),
    "diagnostics": ("list_backups", {"limit": 0}),
    "entities": ("get_state", {"entity_id": "light.nope"}),
    "entity_registry": ("get_entity_registry", {"entity_id": "light.nope"}),
    "helpers": ("list_helpers", {"domain": "nope"}),
    "icons": ("set_icon", {"entity_id": "automation.nope", "icon": "mdi:lightbulb"}),
    "images": ("get_camera_image", {"entity_id": "camera.door"}),
    "integrations": ("start_config_flow", {"domain": "hue"}),
    "knx": ("knx_get_base_data", {}),
    "labels": ("update_label", {"label_id": "nope", "name": "Kitchen"}),
    "registries": ("delete_area", {"area_id": "nope"}),
    "statistics": (
        "adjust_statistics",
        {"statistic_id": "sensor.energy", "start_time": "nope", "adjustment": 1},
    ),
    "system": ("render_template", {"template": "{{ 1 }}"}),
    "system_admin": ("restart_ha", {"confirm": False}),
    "traces": ("list_traces", {}),
}


@pytest.fixture
def options(hass: HomeAssistant) -> dict:
    """Load the integration's options with every optional tool group switched off."""
    hass.data[DOMAIN] = {"read_only": False}
    return hass.data[DOMAIN]


def test_every_tool_module_has_a_failing_call():
    modules = {tool["handler"].__module__.rpartition(".")[2] for tool in TOOLS.values()}
    assert modules == set(FAILING_CALLS)


@pytest.mark.parametrize(("name", "arguments"), FAILING_CALLS.values(), ids=FAILING_CALLS)
async def test_a_failed_call_carries_is_error(
    hass: HomeAssistant, options: dict, name: str, arguments: dict
):
    result = await call_tool(hass, name, arguments)

    assert result["isError"] is True, result
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"]


def test_error_result_is_a_text_result_with_is_error():
    assert error_result("No such thing") == {
        "content": [{"type": "text", "text": "No such thing"}],
        "isError": True,
    }


async def test_an_empty_listing_is_not_an_error(hass: HomeAssistant, options: dict):
    for name, arguments in (("list_floors", {}), ("search_entities", {"query": "nothing"})):
        result = await call_tool(hass, name, arguments)
        assert "isError" not in result, (name, result)
        assert result["content"][0]["text"] == "[]"


async def test_a_preview_is_not_an_error(hass: HomeAssistant, options: dict):
    entity_id = er.async_get(hass).async_get_or_create("light", "test", "kitchen").entity_id

    result = await call_tool(hass, "update_entity_registry", {"entity_id": entity_id, "name": "K"})

    assert "isError" not in result, result
    assert '"mode":"preview"' in result["content"][0]["text"]


async def test_a_no_op_is_not_an_error(hass: HomeAssistant, options: dict):
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "plug")},
        disabled_by=dr.DeviceEntryDisabler.USER,
    )

    result = await call_tool(hass, "update_device", {"device_id": device.id, "disabled": True})

    assert "isError" not in result, result
    assert result["content"][0]["text"].startswith("Device is already disabled")


async def test_a_trace_that_has_not_run_yet_is_not_an_error(hass: HomeAssistant, options: dict):
    assert await async_setup_component(hass, "trace", {})
    hass.states.async_set("automation.porch", "on")

    result = await call_tool(hass, "get_trace", {"entity_id": "automation.porch"})

    assert "isError" not in result, result
    assert result["content"][0]["text"].startswith("No traces found")


async def test_a_trace_of_something_that_does_not_exist_is_an_error(
    hass: HomeAssistant, options: dict
):
    assert await async_setup_component(hass, "trace", {})

    result = await call_tool(hass, "get_trace", {"entity_id": "automation.porch"})

    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("No automation or script with entity_id")

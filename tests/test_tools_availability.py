"""Tests for read-only mode and per-tool disabling.

Both are enforced where tools are listed and where they are dispatched, so a
client neither sees a withheld tool nor reaches it by calling it anyway.
"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.mcp_server_http_transport import tools
from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools import (
    TOOLS,
    ToolNotAvailable,
    call_tool,
    get_tool_schemas,
    knx,
    tool_is_read_only,
)

EVERY_TOOL_OPTION = {
    "appdaemon_file_access": True,
    "camera_image_access": True,
    "config_file_access": True,
    "image_file_access": True,
    "integration_management": True,
    "template_rendering": True,
}


def _hass(**options) -> Mock:
    """A set-up server with every group of tools switched on, limited by the options."""
    hass = Mock()
    hass.data = {DOMAIN: {"read_only": False, **EVERY_TOOL_OPTION, **options}}
    hass.states.get.return_value = None
    hass.services.async_call = AsyncMock()
    return hass


def _listed(hass: Mock) -> set[str]:
    return {schema["name"] for schema in get_tool_schemas(hass)}


_BY_OPTION = {
    "appdaemon_file_access": "list_appdaemon_files",
    "camera_image_access": "get_camera_image",
    "config_file_access": "get_config_file",
    "image_file_access": "get_image_file",
    "integration_management": "start_config_flow",
    "template_rendering": "render_template",
}


@pytest.fixture(autouse=True)
def knx_set_up(monkeypatch) -> dict:
    """Stand in for the KNX integration being set up; clear "module" to take it away."""
    state = {"module": object()}
    monkeypatch.setattr(knx, "_get_knx_module", lambda hass: state["module"])
    return state


class TestListing:
    def test_every_tool_is_listed_with_every_group_switched_on(self):
        assert _listed(_hass()) == set(TOOLS)

    def test_a_default_setup_lists_no_tool_that_is_switched_off(self, knx_set_up):
        """Such a tool could only answer that it is off, at the cost of its schema."""
        hass = _hass(**dict.fromkeys(EVERY_TOOL_OPTION, False))
        knx_set_up["module"] = None

        hidden = set(TOOLS) - _listed(hass)

        assert set(_BY_OPTION.values()) <= hidden
        assert {"delete_device", "delete_integration", "knx_recent_telegrams"} <= hidden
        assert not hidden & {"get_state", "call_service", "update_device", "list_backups"}
        assert len(hidden) == 35

    @pytest.mark.parametrize(("option", "tool"), sorted(_BY_OPTION.items()))
    def test_a_group_is_listed_once_its_option_is_on(self, option, tool):
        off = _hass(**{option: False})
        on = _hass(**{option: True})

        assert tool not in _listed(off)
        assert tool in _listed(on)

    def test_knx_tools_are_listed_only_while_knx_is_set_up(self, knx_set_up):
        """The listing asks what the KNX tools themselves ask when called."""
        hass = _hass()
        assert "knx_get_entities" in _listed(hass)

        knx_set_up["module"] = None
        assert "knx_get_entities" not in _listed(hass)

    def test_every_tool_behind_an_option_is_in_the_listing_rule(self):
        """A module that reads an option must have its tools mapped to that option.

        Otherwise a tool added behind an option would stay listed while it is off.
        """
        mapped: dict[str, set[str]] = {}
        for module, option in tools._OPTION_BY_MODULE.items():
            mapped.setdefault(module, set()).add(option)
        for name, option in tools._OPTION_BY_TOOL.items():
            module = TOOLS[name]["handler"].__module__.rpartition(".")[2]
            mapped.setdefault(module, set()).add(option)
        # The flow tools read template rendering to refuse template helpers; no
        # tool of theirs is switched on by it.
        mapped.setdefault("integrations", set()).add("template_rendering")

        unmapped = {}
        for path in Path(tools.__file__).parent.glob("*.py"):
            read = {
                option
                for option in EVERY_TOOL_OPTION
                if f'"{option}"' in path.read_text() and path.stem != "__init__"
            }
            if missing := read - mapped.get(path.stem, set()):
                unmapped[path.stem] = missing

        assert unmapped == {}

    async def test_a_tool_left_out_for_being_off_still_says_how_to_switch_it_on(self):
        hass = _hass(config_file_access=False)

        result = await call_tool(hass, "list_config_files", {})

        assert result["isError"] is True
        assert "disabled" in result["content"][0]["text"]

    @pytest.mark.parametrize(
        "data",
        [{}, {DOMAIN: None}, {DOMAIN: {}}],
        ids=["never set up", "none", "unloaded"],
    )
    async def test_no_tool_is_available_while_the_entry_is_not_loaded(self, data):
        """Unloading empties hass.data[DOMAIN], which then allows nothing."""
        hass = Mock()
        hass.data = data

        assert _listed(hass) == set()
        with pytest.raises(ToolNotAvailable, match="not loaded"):
            await call_tool(hass, "get_state", {"entity_id": "sun.sun"})

    def test_read_only_mode_lists_only_the_tools_annotated_read_only(self):
        listed = _listed(_hass(read_only=True))

        assert listed == {name for name in TOOLS if tool_is_read_only(name)}
        assert "get_state" in listed
        assert not listed & {"call_service", "restart_ha", "delete_automation", "fire_event"}

    def test_a_disabled_tool_is_left_out(self):
        listed = _listed(_hass(disabled_tools=frozenset({"restart_ha", "get_error_log"})))

        assert listed == set(TOOLS) - {"restart_ha", "get_error_log"}

    def test_both_limits_apply_together(self):
        listed = _listed(_hass(read_only=True, disabled_tools=frozenset({"get_state"})))

        assert "get_state" not in listed
        assert "call_service" not in listed
        assert "list_entities" in listed


class TestDispatch:
    async def test_a_write_tool_is_refused_in_read_only_mode_without_running(self):
        hass = _hass(read_only=True)

        with pytest.raises(ToolNotAvailable, match="'call_service' can change things"):
            await call_tool(hass, "call_service", {"domain": "light", "service": "turn_on"})

        hass.services.async_call.assert_not_called()

    async def test_a_read_tool_still_runs_in_read_only_mode(self):
        result = await call_tool(_hass(read_only=True), "get_state", {"entity_id": "sun.sun"})

        # The test double holds no state, so the handler's own answer shows it ran.
        assert result["content"][0]["text"] == "Entity sun.sun not found"

    async def test_a_disabled_tool_is_refused_even_when_it_only_reads(self):
        hass = _hass(disabled_tools=frozenset({"get_state"}))

        with pytest.raises(ToolNotAvailable, match="'get_state' is turned off"):
            await call_tool(hass, "get_state", {"entity_id": "sun.sun"})

    async def test_refusal_comes_before_argument_checks(self):
        """A withheld tool says so, not which of its arguments are missing."""
        with pytest.raises(ToolNotAvailable):
            await call_tool(_hass(read_only=True), "call_service", {})


class TestReadOnlyAnnotation:
    def test_unknown_and_non_string_names_count_as_writes(self):
        assert tool_is_read_only("no_such_tool") is False
        assert tool_is_read_only(["get_state"]) is False

    def test_every_tool_is_annotated(self):
        """Read-only mode goes by readOnlyHint, so no tool may leave it unsaid."""
        missing = [
            name
            for name, tool in TOOLS.items()
            if not isinstance(tool["schema"].get("annotations", {}).get("readOnlyHint"), bool)
        ]

        assert missing == []

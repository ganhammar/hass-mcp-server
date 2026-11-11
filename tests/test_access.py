"""The access table, what scopes expand to, and how a caller's access limits what it is offered.

Every tool, resource and prompt has to be classified: a new one without an area fails
here, as a tool without a failing-call test fails test_tools_is_error.py.
"""

import json
from unittest.mock import Mock

import pytest

from custom_components.mcp_server_http_transport import access
from custom_components.mcp_server_http_transport import resources as resources_mod
from custom_components.mcp_server_http_transport.access import (
    AREAS,
    COMPLETION_AREAS,
    DIAGNOSTICS,
    ENTITIES,
    FULL,
    NONE,
    PRESETS,
    PROMPT_AREAS,
    READ,
    RESOURCE_AREAS,
    SCOPE_GRANTS,
    TOOL_AREAS,
    Grant,
    grant_for,
    grant_from_scopes,
    is_system_service,
    service_refusal,
)
from custom_components.mcp_server_http_transport.caller import Caller, acting_as
from custom_components.mcp_server_http_transport.completions import complete
from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.prompts import (
    PROMPTS,
    PromptNotAvailable,
    get_prompt,
    get_prompts,
    prompt_unavailable_reason,
)
from custom_components.mcp_server_http_transport.resources import (
    RESOURCE_TEMPLATES,
    RESOURCES,
    ResourceNotAvailable,
    get_resources,
    read_resource,
    resource_key,
)
from custom_components.mcp_server_http_transport.tools import (
    TOOLS,
    ToolNotAvailable,
    call_tool,
    get_tool_schemas,
    tool_access,
    tool_is_read_only,
    tool_unavailable_reason,
)

PRESET = {preset.id: preset for preset in PRESETS}


def _caller(*scopes: str, limited: bool = True, auth_method: str = "oidc") -> Caller:
    return Caller(
        user_id="anna",
        auth_method=auth_method,
        user=Mock(id="anna", is_admin=True),
        client_id="app" if auth_method == "oidc" else None,
        scopes=frozenset(scopes),
        limited_by_scopes=limited,
    )


@pytest.fixture
def every_tool_hass(list_every_tool):
    """A stand-in hass with every option on and KNX set up, so every tool is offered."""
    hass = Mock()
    hass.data = {DOMAIN: {"entry_id": Mock()}}
    list_every_tool(hass)
    return hass


def _listed(hass, caller) -> set[str]:
    with acting_as(caller):
        return {schema["name"] for schema in get_tool_schemas(hass)}


class TestClassification:
    """Every tool, resource, prompt and completion is in the table, and nothing else is."""

    def test_every_tool_belongs_to_exactly_one_area(self):
        listed = [tool for tools in access._TOOLS_BY_AREA.values() for tool in tools]

        assert len(listed) == len(set(listed)), "a tool is listed in two areas"
        assert set(listed) == set(TOOLS), (
            "every registered tool needs an area in access._TOOLS_BY_AREA, and every "
            "tool listed there must exist"
        )

    def test_every_resource_has_an_area(self):
        served = {r["uri"] for r in RESOURCES} | {t["uriTemplate"] for t in RESOURCE_TEMPLATES}

        assert set(RESOURCE_AREAS) == served

    def test_every_prompt_has_its_areas(self):
        assert set(PROMPT_AREAS) == set(PROMPTS)
        assert all(PROMPT_AREAS.values()), "a prompt that reads nothing still names an area"

    def test_every_area_named_in_the_tables_exists(self):
        named = {
            *TOOL_AREAS.values(),
            *RESOURCE_AREAS.values(),
            *COMPLETION_AREAS.values(),
            *(area for areas in PROMPT_AREAS.values() for area in areas),
        }

        assert named <= set(AREAS)

    def test_every_area_has_a_tool(self):
        assert set(TOOL_AREAS.values()) == set(AREAS)

    def test_a_tool_needs_read_when_read_only_and_full_otherwise(self):
        for name in TOOLS:
            area, level = tool_access(name)
            assert area == TOOL_AREAS[name]
            assert level == (READ if tool_is_read_only(name) else FULL), name

    def test_no_tool_needs_more_than_its_area_offers(self):
        for name in TOOLS:
            area, level = tool_access(name)
            assert level in AREAS[area].options, f"{name} needs {level} in {area}"

    @pytest.mark.parametrize(
        ("tool", "needed"),
        [
            ("delete_device", (access.INTEGRATIONS, FULL)),
            ("restart_ha", (DIAGNOSTICS, FULL)),
            ("search_config", (access.AUTOMATIONS, READ)),
            ("call_service", (ENTITIES, FULL)),
            ("fire_event", (DIAGNOSTICS, FULL)),
            ("clear_statistics", (DIAGNOSTICS, FULL)),
        ],
    )
    def test_the_decided_placements(self, tool, needed):
        assert tool_access(tool) == needed

    def test_a_resource_uri_finds_its_template(self):
        assert resource_key("hass://entity/light.kitchen") == "hass://entity/{entity_id}"
        assert resource_key("hass://entities/domain/light") == "hass://entities/domain/{domain}"
        assert resource_key("hass://dashboard/energy") == "hass://dashboard/{url_path}"
        assert resource_key("hass://entities") == "hass://entities"
        assert resource_key("hass://nothing") is None
        assert resource_key(7) is None


class TestPresets:
    """The four levels, as the design and its decisions set them."""

    def test_each_level_includes_the_one_before(self):
        for lower, higher in zip(PRESETS, PRESETS[1:], strict=False):
            for area in AREAS:
                assert access._rank(lower.levels[area]) <= access._rank(higher.levels[area])

    def test_administer_is_every_area_at_its_highest(self):
        assert dict(PRESET["admin"].levels) == {a.id: a.highest for a in AREAS.values()}

    def test_files_and_appdaemon_are_none_below_administer(self):
        for preset in PRESETS[:-1]:
            assert preset.levels[access.FILES] == NONE
            assert preset.levels[access.APPDAEMON] == NONE

    def test_every_level_sets_every_area_to_an_option_it_has(self):
        for preset in PRESETS:
            assert set(preset.levels) == set(AREAS)
            for area, level in preset.levels.items():
                assert level in AREAS[area].options

    @pytest.mark.parametrize(
        ("scope", "count"),
        [("mcp:read", 48), ("mcp:control", 52), ("mcp:configure", 87), ("mcp:admin", 117)],
    )
    def test_how_many_tools_each_level_offers(self, every_tool_hass, scope, count):
        assert len(_listed(every_tool_hass, _caller(scope))) == count

    def test_administer_offers_what_an_unlimited_token_does(self, every_tool_hass):
        assert _listed(every_tool_hass, _caller("mcp:admin")) == set(TOOLS)


class TestScopeExpansion:
    """A token's scopes are looked up whole; a scope this server does not define grants nothing."""

    def test_a_level_expands_to_its_settings(self):
        assert dict(grant_from_scopes(frozenset({"mcp:control"})).levels) == dict(
            PRESET["control"].levels
        )

    def test_area_scopes_set_their_area(self):
        grant = grant_from_scopes(frozenset({"mcp:dashboards:read", "mcp:entities:full"}))

        assert grant.levels[access.DASHBOARDS] == READ
        assert grant.levels[ENTITIES] == FULL
        assert grant.levels[access.CAMERAS] == NONE

    def test_the_highest_setting_any_scope_gives_wins(self):
        grant = grant_from_scopes(
            frozenset({"mcp:read", "mcp:dashboards:full", "mcp:dashboards:read"})
        )

        assert grant.levels[access.DASHBOARDS] == FULL
        assert grant.levels[ENTITIES] == READ

    def test_the_design_example(self):
        """Every area at its highest except dashboards at Read, and no cameras."""
        scopes = {
            AREAS[area].scope(level)
            for area, level in PRESET["admin"].levels.items()
            if area != access.CAMERAS
        } - {"mcp:dashboards:full"} | {"mcp:dashboards:read"}

        grant = grant_from_scopes(frozenset(scopes))

        assert len(scopes) == 12
        assert grant.levels[access.CAMERAS] == NONE
        assert grant.levels[access.DASHBOARDS] == READ
        assert grant.levels[access.FILES] == FULL

    @pytest.mark.parametrize(
        "scope",
        [
            "mcp:",
            "mcp:entities",
            "mcp:entities:",
            "mcp:entities:none",
            "mcp:entities:fullx",
            "mcp:entities:full:extra",
            "mcp:admin:",
            "mcp:adminx",
            "MCP:ADMIN",
            "mcp:Admin",
            " mcp:admin",
            "mcp:cameras:full",
            "mcp:templates:full",
            "mcp:unknown:full",
            "mcp:*",
            "openid",
            "admin",
        ],
    )
    def test_a_scope_the_table_does_not_define_grants_nothing(self, scope):
        assert not grant_from_scopes(frozenset({scope})).grants_anything

    def test_every_scope_the_table_defines_grants_something(self):
        for scope in SCOPE_GRANTS:
            assert grant_from_scopes(frozenset({scope})).grants_anything, scope


class TestWhoIsLimited:
    def test_outside_a_request_nothing_is_limited(self):
        assert grant_for(None) is None

    def test_a_long_lived_access_token_is_not_limited(self):
        assert grant_for(_caller(limited=False, auth_method="native")) is None

    def test_an_oauth_token_the_transport_did_not_limit_is_not(self):
        assert grant_for(_caller("openid", "mcp:read", limited=False)) is None

    def test_a_limited_token_is_held_to_its_scopes(self):
        grant = grant_for(_caller("openid", "mcp:read"))

        assert grant.allows(ENTITIES, READ)
        assert not grant.allows(ENTITIES, FULL)

    def test_a_grant_with_no_entry_for_an_area_allows_nothing_there(self):
        assert not Grant({}).allows(ENTITIES, READ)
        assert Grant({}).allows(ENTITIES, NONE)


class TestToolLimits:
    """tools/list and tools/call agree, and the options keep working as before."""

    def test_an_unlimited_caller_is_offered_what_it_was_before(self, every_tool_hass):
        before = {schema["name"] for schema in get_tool_schemas(every_tool_hass)}

        assert _listed(every_tool_hass, _caller(limited=False, auth_method="native")) == before
        assert _listed(every_tool_hass, _caller("openid", limited=False)) == before
        assert before == set(TOOLS)

    def test_a_limited_caller_is_offered_only_what_it_was_granted(self, every_tool_hass):
        listed = _listed(every_tool_hass, _caller("mcp:dashboards:read"))

        assert listed == {"list_dashboards", "get_dashboard_config"}

    async def test_a_tool_the_access_does_not_include_is_refused_when_called(self, every_tool_hass):
        with acting_as(_caller("mcp:read")), pytest.raises(ToolNotAvailable) as raised:
            await call_tool(every_tool_hass, "delete_dashboard", {"url_path": "x", "confirm": True})

        message = str(raised.value)
        assert "access granted to this application does not include tool 'delete_dashboard'" in (
            message
        )
        assert "Dashboards at Full" in message

    def test_a_scope_never_switches_on_a_disabled_tool(self, every_tool_hass):
        every_tool_hass.data[DOMAIN]["disabled_tools"] = frozenset({"get_state"})

        assert "get_state" not in _listed(every_tool_hass, _caller("mcp:admin"))

    def test_read_only_mode_holds_against_any_scope(self, every_tool_hass):
        every_tool_hass.data[DOMAIN]["read_only"] = True

        listed = _listed(every_tool_hass, _caller("mcp:admin"))

        assert listed and all(tool_is_read_only(name) for name in listed)

    def test_a_scope_never_offers_a_tool_whose_option_is_off(self, every_tool_hass):
        every_tool_hass.data[DOMAIN]["config_file_access"] = False

        assert "get_config_file" not in _listed(every_tool_hass, _caller("mcp:admin"))

    def test_the_options_reason_comes_first(self, every_tool_hass):
        every_tool_hass.data[DOMAIN]["disabled_tools"] = frozenset({"restart_ha"})

        with acting_as(_caller("mcp:read")):
            reason = tool_unavailable_reason(every_tool_hass, "restart_ha")

        assert "turned off in the MCP Server integration options" in reason

    def test_an_unclassified_tool_is_refused_to_a_limited_caller(self, every_tool_hass):
        from unittest.mock import patch

        tool = {"schema": {"name": "new_tool", "annotations": {"readOnlyHint": True}}}
        with patch.dict(TOOLS, {"new_tool": tool}):
            with acting_as(_caller("mcp:admin")):
                assert "belongs to no area" in tool_unavailable_reason(every_tool_hass, "new_tool")
            assert tool_unavailable_reason(every_tool_hass, "new_tool") is None


class TestSearchConfig:
    """search_config reads across areas and only returns what the caller may read."""

    async def test_kinds_the_access_leaves_out_are_skipped(self, every_tool_hass):
        from unittest.mock import AsyncMock, patch

        from custom_components.mcp_server_http_transport.tools import config_search

        readers = {
            kind: AsyncMock(return_value=([("1", "Kitchen", {"x": "light.kitchen"}, {})], {}))
            for kind in config_search.KIND_TOOLS
        }
        with (
            patch.dict(config_search._READERS, readers),
            acting_as(_caller("mcp:automations:read")),
        ):
            result = await call_tool(every_tool_hass, "search_config", {"query": "kitchen"})

        body = json.loads(result["content"][0]["text"])
        assert body["counts"] == {"automation": 1, "script": 1, "scene": 1}
        assert "Dashboards at Read" in body["skipped"]["dashboard"]
        readers["dashboard"].assert_not_awaited()


class TestResourceLimits:
    async def test_the_listing_and_the_read_agree(self):
        with acting_as(_caller("mcp:entities:read")):
            listed = get_resources()
            uris = {r["uri"] for r in listed["resources"]}
            templates = {t["uriTemplate"] for t in listed["resourceTemplates"]}
            with pytest.raises(ResourceNotAvailable, match="Dashboards at Read"):
                await read_resource(Mock(), "hass://dashboard/energy")
            with pytest.raises(ResourceNotAvailable, match="Areas, floors, devices"):
                await read_resource(Mock(), "hass://areas")

        assert uris == {"hass://entities", "hass://services"}
        assert templates == {"hass://entity/{entity_id}", "hass://entities/domain/{domain}"}

    def test_an_unlimited_caller_lists_every_resource(self):
        with acting_as(_caller(limited=False, auth_method="native")):
            listed = get_resources()

        assert listed == {"resources": RESOURCES, "resourceTemplates": RESOURCE_TEMPLATES}
        assert get_resources() == listed


class TestPromptLimits:
    def test_a_prompt_needs_read_in_every_area_it_reads(self):
        with acting_as(_caller("mcp:automations:read")):
            names = {p["name"] for p in get_prompts()}

        assert names == {"automation_review"}

    async def test_a_prompt_the_access_does_not_include_is_refused(self):
        with (
            acting_as(_caller("mcp:automations:read")),
            pytest.raises(PromptNotAvailable, match="Entities and their states at Read"),
        ):
            await get_prompt(Mock(), "automation_debugger", {"automation_id": "x"})

    def test_an_unlimited_caller_gets_every_prompt(self):
        with acting_as(_caller("openid", limited=False)):
            assert len(get_prompts()) == len(PROMPTS)


class TestCompletionLimits:
    async def test_a_completion_that_lists_an_area_needs_read_there(self):
        hass = Mock()
        hass.states.async_all.return_value = [Mock(entity_id="light.kitchen")]

        with acting_as(_caller("mcp:dashboards:read")):
            refused = await complete(hass, {}, {"name": "entity_id", "value": "light"})
            fixed = await complete(hass, {}, {"name": "trigger_type", "value": "st"})
            helper = await complete(
                hass, {"name": "create_helper"}, {"name": "domain", "value": "input_b"}
            )
        with acting_as(_caller("mcp:read")):
            allowed = await complete(hass, {}, {"name": "entity_id", "value": "light"})

        assert refused == {"values": [], "hasMore": False}
        assert fixed["values"] == ["state"]
        assert helper["values"] == ["input_boolean", "input_button"]
        assert allowed["values"] == ["light.kitchen"]


class TestServiceFilter:
    """With a limited token, a service that reaches the system needs Diagnostics at Full."""

    @staticmethod
    def _hass(registered: dict | None = None) -> Mock:
        hass = Mock()
        hass.services.async_services_internal = Mock(return_value=registered or {})
        return hass

    @pytest.mark.parametrize(
        ("domain", "service"),
        [
            ("homeassistant", "restart"),
            ("homeassistant", "stop"),
            ("homeassistant", "reload_all"),
            ("homeassistant", "set_location"),
            ("HomeAssistant", "Restart"),
            ("hassio", "host_reboot"),
            ("shell_command", "anything"),
            ("python_script", "x"),
            ("pyscript", "x"),
            ("recorder", "purge"),
            ("backup", "create"),
            ("update", "install"),
            ("cloud", "remote_connect"),
            ("system_log", "clear"),
            ("logger", "set_level"),
            ("profiler", "start"),
            ("debugpy", "start"),
            ("mqtt", "publish"),
            ("MQTT", "Dump"),
            ("synology_dsm", "reboot"),
            ("homematic", "set_install_mode"),
            ("script", "reload"),
            ("group", "RELOAD"),
        ],
    )
    def test_system_services(self, domain, service):
        assert is_system_service(self._hass(), domain, service)

    @pytest.mark.parametrize(
        ("domain", "service"),
        [
            ("homeassistant", "turn_on"),
            ("homeassistant", "turn_off"),
            ("homeassistant", "toggle"),
            ("homeassistant", "update_entity"),
            ("HOMEASSISTANT", "TURN_ON"),
            ("light", "turn_on"),
            ("script", "turn_on"),
            ("automation", "turn_off"),
            ("homematic", "virtualkey"),
            ("modbus", "write_register"),
            (None, "restart"),
            ("homeassistant", 7),
        ],
    )
    def test_services_that_act_on_entities(self, domain, service):
        assert not is_system_service(self._hass(), domain, service)

    def test_a_service_registered_for_administrators_is_a_system_service(self):
        from functools import partial

        from homeassistant.helpers import service as service_helper

        admin = Mock()
        admin.job.target = partial(service_helper._async_admin_handler, Mock(), Mock())
        plain = Mock()
        plain.job.target = lambda call: None
        hass = self._hass({"zha": {"permit": admin, "squawk": plain}})

        assert is_system_service(hass, "zha", "permit")
        assert is_system_service(hass, "ZHA", "Permit")
        assert not is_system_service(hass, "zha", "squawk")
        assert not is_system_service(hass, "zha", "unknown")

    def test_a_registry_that_cannot_be_read_leaves_the_fixed_lists_in_charge(self):
        hass = Mock()
        hass.services.async_services_internal = Mock(side_effect=AttributeError("gone"))

        assert not is_system_service(hass, "zha", "permit")
        assert is_system_service(hass, "homeassistant", "restart")

    def test_a_limited_caller_without_diagnostics_at_full_is_refused(self):
        with acting_as(_caller("mcp:configure")):
            refusal = service_refusal(Mock(), "homeassistant", "restart")
            allowed = service_refusal(self._hass(), "light", "turn_on")

        assert "homeassistant.restart" in refusal
        assert "Diagnostics at Full" in refusal
        assert allowed is None

    @pytest.mark.parametrize(
        "caller",
        [
            _caller("mcp:admin"),
            _caller("mcp:entities:full", "mcp:diagnostics:full"),
            _caller(limited=False, auth_method="native"),
            _caller("openid", limited=False),
        ],
    )
    def test_who_the_filter_lets_through(self, caller):
        with acting_as(caller):
            assert service_refusal(Mock(), "homeassistant", "restart") is None

    async def test_call_service_answers_a_refusal_as_an_error(self, every_tool_hass):
        every_tool_hass.services.async_call = Mock()

        with acting_as(_caller("mcp:control")):
            result = await call_tool(
                every_tool_hass,
                "call_service",
                {"domain": "homeassistant", "service": "restart"},
            )

        assert result["isError"] is True
        assert "Diagnostics at Full" in result["content"][0]["text"]
        every_tool_hass.services.async_call.assert_not_called()


class TestDeviceDetails:
    async def test_states_are_left_out_without_entities_at_read(self, hass):
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        entry = MockConfigEntry(domain="test")
        entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("test", "1")}, name="Lamp"
        )
        er.async_get(hass).async_get_or_create(
            "light", "test", "1", device_id=device.id, suggested_object_id="lamp"
        )
        hass.states.async_set("light.lamp", "on")
        hass.data[DOMAIN] = {"entry_id": "x"}

        with acting_as(_caller("mcp:registries:read")):
            limited = await call_tool(hass, "get_device_details", {"device_id": device.id})
        with acting_as(_caller("mcp:read")):
            full = await call_tool(hass, "get_device_details", {"device_id": device.id})

        limited_body = json.loads(limited["content"][0]["text"])
        assert "state" not in limited_body["entities"][0]
        assert "states_omitted" in limited_body
        assert json.loads(full["content"][0]["text"])["entities"][0]["state"] == "on"


class TestFailsClosed:
    """What is not classified is never given to a limited caller, and a core change leaves
    the fixed service list in charge."""

    def test_a_prompt_without_an_area_is_withheld_from_a_limited_caller(self):
        with acting_as(_caller("mcp:admin")):
            reason = prompt_unavailable_reason("not_a_classified_prompt")

        assert "belongs to no area" in reason

    def test_a_resource_without_an_area_is_withheld_from_a_limited_caller(self):
        with acting_as(_caller("mcp:admin")):
            reason = resources_mod._withheld("hass://not-classified")

        assert reason == (
            "The access granted to this application does not include resource "
            "hass://not-classified"
        )

    def test_admin_detection_answers_no_when_core_drops_its_handler(self, monkeypatch):
        from homeassistant.helpers import service as service_helper

        monkeypatch.delattr(service_helper, "_async_admin_handler")

        assert access.is_admin_service(Mock(), "homeassistant", "restart") is False
        # The fixed list still holds the service back.
        assert access.is_system_service(Mock(), "homeassistant", "restart") is True

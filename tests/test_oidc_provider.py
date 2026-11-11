"""The description of this server's access that the OIDC provider collects.

It is built from the same table enforcement uses, so these check that what the
consent screen offers is what the server enforces. When the paired provider is
vendored, the description is also checked with the provider's own validator.
"""

from unittest.mock import Mock

import pytest

from custom_components.mcp_server_http_transport import oidc_provider
from custom_components.mcp_server_http_transport.access import (
    AREAS,
    FULL,
    PRESETS,
    READ,
    SCOPE_GRANTS,
    SECTIONS,
)
from custom_components.mcp_server_http_transport.const import DOMAIN, MCP_HTTP_PATH, MCP_PATH

try:
    from custom_components.oidc_provider import resource_description as _provider_validator

    _PROVIDER_AVAILABLE = not isinstance(_provider_validator, Mock)
except ImportError:
    _PROVIDER_AVAILABLE = False

needs_provider = pytest.mark.skipif(
    not _PROVIDER_AVAILABLE,
    reason="paired oidc_provider integration not vendored into the test environment",
)

ALL_ON = {
    "config_file_access": True,
    "appdaemon_file_access": True,
    "template_rendering": True,
    "integration_management": True,
    "camera_image_access": True,
    "image_file_access": True,
}


def _groups(description):
    return [group for section in description["sections"] for group in section["groups"]]


def _hass(**options):
    hass = Mock()
    hass.data = {DOMAIN: {"entry_id": Mock(), **options}}
    return hass


@pytest.fixture
def knx_set_up(monkeypatch):
    from custom_components.mcp_server_http_transport.tools import knx

    monkeypatch.setattr(knx, "_get_knx_module", lambda hass: object())


@pytest.fixture
def knx_missing(monkeypatch):
    from custom_components.mcp_server_http_transport.tools import knx

    monkeypatch.setattr(knx, "_get_knx_module", lambda hass: None)


class TestDescription:
    description = oidc_provider.build_description()

    def test_it_names_both_endpoints(self):
        assert self.description["resource"] == [MCP_PATH, MCP_HTTP_PATH]

    def test_the_areas_are_the_ones_enforced(self):
        groups = _groups(self.description)

        assert [group["id"] for group in groups] == list(AREAS)
        for group in groups:
            area = AREAS[group["id"]]
            assert group["title"] == area.title
            assert [option["id"] for option in group["options"]] == list(area.options)
            for option in group["options"]:
                expected = [] if option["id"] == "none" else [area.scope(option["id"])]
                assert option.get("scopes", []) == expected

    def test_the_sections_group_the_areas_as_enforced(self):
        assert [s["id"] for s in self.description["sections"]] == list(SECTIONS)
        for section in self.description["sections"]:
            assert {g["id"] for g in section["groups"]} == {
                a.id for a in AREAS.values() if a.section == section["id"]
            }

    def test_the_levels_are_the_ones_enforced(self):
        presets = self.description["presets"]

        assert [p["id"] for p in presets] == [p.id for p in PRESETS]
        for described, preset in zip(presets, PRESETS, strict=True):
            assert described["title"] == preset.title
            assert described["options"] == dict(preset.levels)
            assert described["scopes"] == [preset.scope]

    def test_every_scope_declared_is_one_the_server_understands(self):
        assert set(self.description["scopes"]) == set(SCOPE_GRANTS)

    def test_look_only_first_and_sessions_from_before_get_administer(self):
        assert self.description["default_preset"] == "read"
        assert self.description["existing_sessions_preset"] == "admin"

    def test_the_accepted_wording(self):
        assert self.description["prompt"] == "Choose what {app} can do in Home Assistant."
        assert self.description["presets"][0]["description"] == (
            "See devices, history and settings, without changing anything."
        )
        assert [p["title"] for p in self.description["presets"]] == [
            "Look only",
            "Control devices",
            "Edit the setup",
            "Administer",
        ]

    def test_the_notes_say_what_the_service_filter_does_not_cover(self):
        configure = self.description["presets"][2]
        (note,) = configure["notes"]

        assert "write an automation or script that performs any action" in note["text"]

    async def test_the_text_is_english_in_every_language(self):
        assert await oidc_provider.async_describe_resource(Mock(), "sv") == self.description


class TestAvailability:
    async def test_nothing_is_unavailable_with_every_option_on(self, knx_set_up):
        assert await oidc_provider.async_get_availability(_hass(**ALL_ON), "en") == {}

    def test_areas_whose_option_is_off_say_so(self, knx_missing):
        unavailable = oidc_provider.unavailable_areas(_hass())

        assert set(unavailable) == {
            "files",
            "appdaemon",
            "templates",
            "integrations",
            "cameras",
            "knx",
        }
        assert unavailable["files"] == "Turned off in the MCP Server integration's options."
        assert "list of integrations can still be seen" in unavailable["integrations"]
        assert unavailable["knx"] == "KNX is not set up in this Home Assistant."

    @pytest.mark.parametrize(
        ("off", "reason"),
        [("camera_image_access", "Camera images"), ("image_file_access", "Image files")],
    )
    def test_half_of_cameras_and_images_says_which_half(self, knx_set_up, off, reason):
        unavailable = oidc_provider.unavailable_areas(_hass(**{**ALL_ON, off: False}))

        assert unavailable["cameras"].startswith(reason)

    def test_an_unloaded_integration_reports_nothing(self):
        hass = Mock()
        hass.data = {}

        assert oidc_provider.unavailable_areas(hass) == {}


class TestCeiling:
    async def test_no_ceiling_outside_read_only_mode(self):
        assert await oidc_provider.async_get_ceiling(_hass(), Mock(), "en") is None

    async def test_read_only_mode_holds_every_area_at_read(self):
        ceiling = await oidc_provider.async_get_ceiling(_hass(read_only=True), Mock(), "en")

        assert set(ceiling["groups"]) == {a.id for a in AREAS.values() if a.highest == FULL}
        assert {limit["max"] for limit in ceiling["groups"].values()} == {READ}
        assert ceiling["locked_presets"] == ["control", "configure", "admin"]
        assert "read-only mode" in ceiling["reason"]


@needs_provider
class TestProviderValidation:
    """The provider's own validator accepts what this server describes."""

    def test_the_description_is_valid(self):
        _provider_validator.validate_description(oidc_provider.build_description())

    def test_the_availability_is_valid(self, knx_missing):
        description = _provider_validator.validate_description(oidc_provider.build_description())

        _provider_validator.validate_availability(
            description, oidc_provider.unavailable_areas(_hass())
        )

    def test_the_read_only_ceiling_is_valid(self):
        description = _provider_validator.validate_description(oidc_provider.build_description())

        _provider_validator.validate_ceiling(
            description, oidc_provider.read_only_ceiling(_hass(read_only=True))
        )

    def test_a_level_grants_its_own_scope_and_a_custom_choice_its_area_scopes(self):
        description = _provider_validator.validate_description(oidc_provider.build_description())
        control = dict(PRESETS[1].levels)

        assert _provider_validator.granted_scopes(description, control) == ["mcp:control"]
        custom = {**control, "cameras": "none"}
        granted = _provider_validator.granted_scopes(description, custom)
        assert "mcp:cameras:read" not in granted and "mcp:entities:full" in granted

    def test_sessions_from_before_in_read_only_mode_get_every_area_at_read(self):
        description = _provider_validator.validate_description(oidc_provider.build_description())
        ceiling = _provider_validator.validate_ceiling(
            description, oidc_provider.read_only_ceiling(_hass(read_only=True))
        )

        options = _provider_validator.existing_sessions_options(description, ceiling)

        assert set(options.values()) == {READ}
        _provider_validator.validate_choice(description, ceiling, {"options": options})

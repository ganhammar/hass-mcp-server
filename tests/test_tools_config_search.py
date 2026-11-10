"""Tests for search_config.

The configs are real files read through Home Assistant's own YAML loader, and the
dashboards are stored through the real Lovelace component, so what is searched is
what get_automation_config, get_dashboard_config and their siblings return.
"""

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.dashboard_manager import (
    create_dashboard,
    get_dashboard_config,
    save_dashboard_config,
)
from custom_components.mcp_server_http_transport.json_patch import resolve_pointer
from custom_components.mcp_server_http_transport.tools import TOOLS, call_tool, tool_is_read_only
from custom_components.mcp_server_http_transport.tools.config_search import (
    SNIPPET_LENGTH,
    _Matches,
    _walk,
    search_config,
)

AUTOMATIONS = """\
- id: "1001"
  alias: Kitchen motion
  triggers:
    - trigger: state
      entity_id: binary_sensor.kitchen_motion
  actions:
    - action: light.turn_on
      target:
        entity_id: light.kitchen
- id: "1002"
  alias: Porch at sunset
  triggers:
    - trigger: sun
      event: sunset
  actions:
    - action: light.turn_on
      target:
        entity_id: light.porch
"""

SCRIPTS = """\
evening:
  alias: Evening
  sequence:
    - action: light.turn_off
      target:
        entity_id: [light.porch, light.Kitchen]
"""

SCENES = """\
- id: "2001"
  name: Dinner
  entities:
    light.kitchen:
      state: "on"
      brightness: 120
"""

DEFAULT_DASHBOARD = {
    "title": "Home",
    "views": [
        {"title": "Downstairs", "cards": [{"type": "tile", "entity": "light.kitchen"}]},
    ],
}


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _json(result: dict) -> dict:
    return json.loads(_text(result))


def _hits(result: dict) -> dict[tuple[str, str], list[dict]]:
    return {(item["type"], item["id"]): item["matches"] for item in _json(result)["items"]}


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Point the instance at a config directory holding the three YAML files."""
    (tmp_path / "automations.yaml").write_text(AUTOMATIONS)
    (tmp_path / "scripts.yaml").write_text(SCRIPTS)
    (tmp_path / "scenes.yaml").write_text(SCENES)
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture
def loaded(hass: HomeAssistant) -> None:
    """Stand in for the set-up entry, whose options decide which tools are available."""
    hass.data[DOMAIN] = {"read_only": False}


@pytest.fixture
async def lovelace(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "lovelace", {})
    await save_dashboard_config(hass, "default", DEFAULT_DASHBOARD)


pytestmark = pytest.mark.usefixtures("config_dir", "loaded")


def test_the_tool_is_registered_read_only():
    assert "search_config" in TOOLS
    assert tool_is_read_only("search_config")


@pytest.mark.usefixtures("lovelace")
async def test_every_kind_is_searched_and_each_match_located(hass: HomeAssistant):
    result = _json(await search_config(hass, {"query": "light.kitchen"}))

    assert result["counts"] == {"automation": 1, "script": 1, "scene": 1, "dashboard": 1}
    assert "truncated" not in result and "skipped" not in result
    assert [(i["type"], i["id"], i.get("name"), i["match_count"]) for i in result["items"]] == [
        ("automation", "1001", "Kitchen motion", 1),
        ("script", "evening", "Evening", 1),
        ("scene", "2001", "Dinner", 1),
        ("dashboard", "default", "Home", 1),
    ]
    assert _hits(await search_config(hass, {"query": "light.kitchen"})) == {
        ("automation", "1001"): [{"path": "/actions/0/target/entity_id", "value": "light.kitchen"}],
        ("script", "evening"): [
            {"path": "/sequence/0/target/entity_id/1", "value": "light.Kitchen"}
        ],
        # A scene keys its entities by entity ID, so the match is on the key.
        ("scene", "2001"): [{"path": "/entities/light.kitchen", "key": "light.kitchen"}],
        ("dashboard", "default"): [{"path": "/views/0/cards/0/entity", "value": "light.kitchen"}],
    }


@pytest.mark.usefixtures("lovelace")
async def test_a_dashboard_pointer_is_the_one_get_dashboard_config_takes(hass: HomeAssistant):
    (item,) = _json(await search_config(hass, {"query": "tile", "types": ["dashboard"]}))["items"]
    (match,) = item["matches"]

    config = await get_dashboard_config(hass, item["id"])
    assert resolve_pointer(config, match["path"]) == "tile"
    shown = _json(await call_tool(hass, "get_dashboard_config", {"url_path": "default"}))
    assert resolve_pointer(shown, match["path"]) == "tile"


async def test_a_pointer_resolves_in_what_the_sibling_read_tool_returns(hass: HomeAssistant):
    hits = _hits(await search_config(hass, {"query": "porch"}))
    automation = _json(await call_tool(hass, "get_automation_config", {"automation_id": "1002"}))
    script = _json(await call_tool(hass, "get_script_config", {"key": "evening"}))

    for match in hits[("automation", "1002")]:
        assert "porch" in resolve_pointer(automation, match["path"]).lower()
    for match in hits[("script", "evening")]:
        assert "porch" in resolve_pointer(script, match["path"]).lower()


async def test_matching_ignores_case_and_finds_part_of_a_value(hass: HomeAssistant):
    hits = _hits(await search_config(hass, {"query": "KITCHEN"}))

    assert hits[("automation", "1001")] == [
        {"path": "/alias", "value": "Kitchen motion"},
        {"path": "/triggers/0/entity_id", "value": "binary_sensor.kitchen_motion"},
        {"path": "/actions/0/target/entity_id", "value": "light.kitchen"},
    ]


async def test_types_limits_the_kinds_searched(hass: HomeAssistant):
    result = _json(await search_config(hass, {"query": "light.", "types": ["scene", "script"]}))

    assert result["counts"] == {"script": 1, "scene": 1}
    assert [item["type"] for item in result["items"]] == ["script", "scene"]


async def test_the_item_limit_is_reported_as_truncated(hass: HomeAssistant):
    result = _json(await search_config(hass, {"query": "light.", "limit": 2}))

    assert [item["id"] for item in result["items"]] == ["1001", "1002"]
    assert result["counts"] == {"automation": 2, "script": 1, "scene": 1}
    assert result["truncated"] is True


async def test_matches_beyond_the_per_item_cap_are_counted_not_listed(
    hass: HomeAssistant, config_dir: Path
):
    rows = "\n".join(f"        - light.hall_{i}" for i in range(12))
    (config_dir / "scripts.yaml").write_text(
        "hall:\n  sequence:\n    - action: light.turn_on\n      target:\n"
        f"        entity_id:\n{rows}\n"
    )

    capped = _json(await search_config(hass, {"query": "light.hall", "types": ["script"]}))
    (item,) = capped["items"]
    assert (item["match_count"], len(item["matches"]), item["truncated"]) == (12, 5, True)
    assert "truncated" not in capped

    (item,) = _json(
        await search_config(
            hass, {"query": "light.hall", "types": ["script"], "matches_per_item": 20}
        )
    )["items"]
    assert (item["match_count"], len(item["matches"])) == (12, 12)
    assert "truncated" not in item


async def test_a_long_value_is_cut_to_a_window_around_the_match(
    hass: HomeAssistant, config_dir: Path
):
    template = (
        "{% set lights = ['light.hall', 'light.landing', 'light.stairs', 'light.attic'] %}\n"
        "{{ is_state('light.kitchen', 'on') and lights | select('is_state', 'on') "
        "| list | count > 2 }}"
    )
    (config_dir / "scripts.yaml").write_text(
        "check:\n  sequence:\n    - condition: template\n"
        f"      value_template: {json.dumps(template)}\n"
    )

    (item,) = _json(await search_config(hass, {"query": "light.kitchen", "types": ["script"]}))[
        "items"
    ]
    (match,) = item["matches"]

    snippet = match["value"]
    assert "light.kitchen" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) == SNIPPET_LENGTH + 2
    assert "\n" not in snippet


async def test_a_dashboard_without_stored_config_is_left_out(hass: HomeAssistant):
    """A dashboard Home Assistant generates has nothing stored to search or patch."""
    assert await async_setup_component(hass, "lovelace", {})
    await create_dashboard(hass, url_path="empty-dash", title="Empty")
    await create_dashboard(hass, url_path="cards-dash", title="Cards")
    await save_dashboard_config(
        hass, "cards-dash", {"views": [{"cards": [{"type": "entity", "entity": "light.attic"}]}]}
    )

    result = _json(await search_config(hass, {"query": "dash", "types": ["dashboard"]}))
    assert result == {"query": "dash", "counts": {}, "items": []}

    result = _json(await search_config(hass, {"query": "light.attic", "types": ["dashboard"]}))
    assert [(i["id"], i["name"]) for i in result["items"]] == [("cards-dash", "Cards")]


async def test_a_yaml_mode_dashboard_is_left_out(hass: HomeAssistant, config_dir: Path):
    (config_dir / "yaml-dash.yaml").write_text(
        "views:\n  - cards:\n      - type: entity\n        entity: light.kitchen\n"
    )
    assert await async_setup_component(
        hass,
        "lovelace",
        {
            "lovelace": {
                "dashboards": {
                    "yaml-dash": {"mode": "yaml", "filename": "yaml-dash.yaml", "title": "Y"}
                }
            }
        },
    )

    result = _json(await search_config(hass, {"query": "light.kitchen", "types": ["dashboard"]}))

    assert result["items"] == []


async def test_without_lovelace_the_other_kinds_are_still_searched(hass: HomeAssistant):
    result = _json(await search_config(hass, {"query": "light.kitchen"}))

    assert result["counts"] == {"automation": 1, "script": 1, "scene": 1}
    assert result["skipped"] == {"dashboard": "could not be read: Lovelace is not loaded"}


async def test_an_unreadable_file_skips_only_its_kind(hass: HomeAssistant, config_dir: Path):
    (config_dir / "automations.yaml").write_text("- id: [unclosed\n")

    result = _json(
        await search_config(hass, {"query": "light.kitchen", "types": ["automation", "scene"]})
    )

    assert result["counts"] == {"scene": 1}
    assert result["skipped"]["automation"].startswith("could not be read: ")


class TestToolLimits:
    """A kind is searched only while every tool that returns its config is available."""

    @pytest.mark.usefixtures("lovelace")
    async def test_a_disabled_read_tool_withholds_its_kind(self, hass: HomeAssistant):
        hass.data[DOMAIN] = {"disabled_tools": frozenset({"get_dashboard_config"})}

        result = _json(await call_tool(hass, "search_config", {"query": "light.kitchen"}))

        assert result["counts"] == {"automation": 1, "script": 1, "scene": 1}
        assert result["skipped"] == {
            "dashboard": (
                "Tool 'get_dashboard_config' is turned off in the MCP Server integration options"
            )
        }

    @pytest.mark.parametrize("tool", ["list_automations", "get_automation_config"])
    async def test_either_automation_read_tool_withholds_automations(
        self, hass: HomeAssistant, tool: str
    ):
        hass.data[DOMAIN] = {"disabled_tools": frozenset({tool})}

        result = _json(await call_tool(hass, "search_config", {"query": "light.kitchen"}))

        assert "automation" not in result["counts"]
        assert set(result["skipped"]) == {"automation", "dashboard"}

    @pytest.mark.usefixtures("lovelace")
    async def test_read_only_mode_searches_everything_and_writes_nothing(
        self, hass: HomeAssistant, config_dir: Path
    ):
        hass.data[DOMAIN] = {"read_only": True}
        before = {path.name: path.read_bytes() for path in config_dir.iterdir()}
        stored = await get_dashboard_config(hass, "default")

        result = _json(await call_tool(hass, "search_config", {"query": "light.kitchen"}))

        assert len(result["items"]) == 4
        assert {path.name: path.read_bytes() for path in config_dir.iterdir()} == before
        assert await get_dashboard_config(hass, "default") == stored


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"query": ""}, "query must be a non-empty string"),
        ({"query": "   "}, "query must be a non-empty string"),
        ({"query": 5}, "query must be a non-empty string"),
        ({"query": "x", "types": ["automation", "blueprint"]}, "types must be a non-empty list"),
        ({"query": "x", "types": []}, "types must be a non-empty list"),
        ({"query": "x", "types": "scene"}, "types must be a non-empty list"),
        ({"query": "x", "limit": 0}, "limit must be an integer between 1 and 100"),
        ({"query": "x", "limit": 2.5}, "limit must be an integer between 1 and 100"),
        ({"query": "x", "limit": True}, "limit must be an integer between 1 and 100"),
        ({"query": "x", "matches_per_item": 101}, "matches_per_item must be an integer"),
    ],
)
async def test_invalid_arguments_are_explained(hass: HomeAssistant, arguments, message):
    text = _text(await search_config(hass, arguments))

    assert text.startswith("Error searching config: ")
    assert message in text


def test_a_structure_that_contains_itself_is_walked_once():
    """A YAML anchor can make a list contain itself."""
    node: list = ["light.kitchen"]
    node.append(node)
    matches = _Matches("light.kitchen", 10)

    _walk(node, [], set(), matches)

    assert (matches.count, matches.shown) == (1, [{"path": "/0", "value": "light.kitchen"}])


def test_a_key_is_escaped_in_its_pointer():
    matches = _Matches("a/b", 10)

    _walk({"x": {"a/b~": 1}}, [], set(), matches)

    assert matches.shown == [{"path": "/x/a~1b~0", "key": "a/b~"}]

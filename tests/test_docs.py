"""The documentation names what the server really offers, and its links lead somewhere.

The tool reference is written by hand, so these fail when a tool, resource or
prompt is added, renamed or removed without the reference following.
"""

import re
from pathlib import Path

import pytest

from custom_components.mcp_server_http_transport import tools
from custom_components.mcp_server_http_transport.access import (
    AREAS,
    LEVEL_TITLES,
    PROMPT_AREAS,
    RESOURCE_AREAS,
)
from custom_components.mcp_server_http_transport.prompts import get_prompts
from custom_components.mcp_server_http_transport.resources import RESOURCE_TEMPLATES, RESOURCES
from custom_components.mcp_server_http_transport.tools import TOOLS

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"
PAGES = [ROOT / "README.md", *sorted(DOCS.glob("*.md"))]
REPO_URL = "https://github.com/ganhammar/hass-mcp-server/blob/main/"
RAW_URL = "https://raw.githubusercontent.com/ganhammar/hass-mcp-server/main/"


def _section(text: str, heading: str) -> str:
    """Return the body of a second-level section."""
    match = re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text)
    assert match, f"no section '{heading}'"
    return match[1]


def _first_column(section: str) -> list[str]:
    return re.findall(r"(?m)^\| `([^`]+)` \|", section)


def _second_column(section: str) -> dict[str, str]:
    return dict(re.findall(r"(?m)^\| `([^`]+)` \| ([^|]+?) \|", section))


def _anchors(page: Path) -> set[str]:
    """Return the anchors GitHub gives a page's headings."""
    anchors = set()
    in_code = False
    for line in page.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code or not line.startswith("#"):
            continue
        title = line.lstrip("#").strip().lower()
        anchors.add(re.sub(r"[^a-z0-9 _-]", "", title).replace(" ", "-"))
    return anchors


class TestToolReference:
    reference = (DOCS / "tools.md").read_text()

    def test_every_tool_has_exactly_one_row(self):
        documented = _first_column(_section(self.reference, "Tools"))

        assert sorted(documented) == sorted(TOOLS)

    def test_every_resource_is_listed(self):
        documented = _first_column(_section(self.reference, "Resources"))
        served = [r["uri"] for r in RESOURCES] + [t["uriTemplate"] for t in RESOURCE_TEMPLATES]

        assert sorted(documented) == sorted(served)

    def test_every_prompt_is_listed(self):
        documented = _first_column(_section(self.reference, "Prompts"))

        assert sorted(documented) == sorted(prompt["name"] for prompt in get_prompts())

    def test_the_access_column_names_each_tools_area_and_setting(self):
        documented = _second_column(_section(self.reference, "Tools"))

        expected = {}
        for name in TOOLS:
            area, level = tools.tool_access(name)
            expected[name] = f"{AREAS[area].title}: {LEVEL_TITLES[level]}"
        assert documented == expected

    def test_the_access_column_names_each_resources_area(self):
        documented = _second_column(_section(self.reference, "Resources"))

        assert documented == {
            key: f"{AREAS[area].title}: Read" for key, area in RESOURCE_AREAS.items()
        }

    def test_the_access_column_names_the_areas_each_prompt_reads(self):
        documented = _second_column(_section(self.reference, "Prompts"))

        assert documented == {
            name: "; ".join(AREAS[area].title for area in areas)
            for name, areas in PROMPT_AREAS.items()
        }

    def test_the_tool_counts_quoted_elsewhere_are_current(self):
        """The README gives the total, and the configuration page how many start hidden."""
        behind_an_option = [
            name
            for name, tool in TOOLS.items()
            if name in tools._OPTION_BY_TOOL
            or tool["handler"].__module__.rpartition(".")[2] in {*tools._OPTION_BY_MODULE, "knx"}
        ]

        assert f"{len(TOOLS)} tools" in (ROOT / "README.md").read_text()
        assert (
            f"{len(behind_an_option)} of the {len(TOOLS)} tools"
            in (DOCS / "configuration.md").read_text()
        )


def test_the_repair_for_a_contested_path_links_to_a_section_that_exists():
    source = (ROOT / "custom_components/mcp_server_http_transport/__init__.py").read_text()
    path, anchor = re.search(r'blob/main/(docs/[a-z]+\.md)"\s+"#([a-z0-9-]+)"', source).groups()

    assert anchor in _anchors(ROOT / path)


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_links_between_the_pages_lead_somewhere(page: Path):
    """The README links by full URL, since HACS shows it outside the repository.

    Its images are linked as raw files, which is what an image needs outside GitHub.
    """
    for target in re.findall(r"\]\(([^)\s]+)\)", page.read_text()):
        if target.startswith(REPO_URL):
            path, _, anchor = target[len(REPO_URL) :].partition("#")
            linked = ROOT / path
        elif target.startswith(RAW_URL):
            path, anchor = target[len(RAW_URL) :], ""
            linked = ROOT / path
        elif target.startswith(("http://", "https://")):
            continue
        else:
            path, _, anchor = target.partition("#")
            linked = (page.parent / path) if path else page
        assert linked.is_file(), f"{page.name} links to missing {target}"
        if anchor and linked.suffix == ".md":
            assert anchor in _anchors(linked), f"{page.name} links to missing anchor {target}"

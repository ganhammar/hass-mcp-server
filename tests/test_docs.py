"""The documentation names what the server really offers, and its links lead somewhere.

The tool reference is written by hand, so these fail when a tool, resource or
prompt is added, renamed or removed without the reference following.
"""

import re
from pathlib import Path

import pytest

from custom_components.mcp_server_http_transport.prompts import get_prompts
from custom_components.mcp_server_http_transport.resources import RESOURCE_TEMPLATES, RESOURCES
from custom_components.mcp_server_http_transport.tools import TOOLS

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"
PAGES = [ROOT / "README.md", *sorted(DOCS.glob("*.md"))]
REPO_URL = "https://github.com/ganhammar/hass-mcp-server/blob/main/"


def _section(text: str, heading: str) -> str:
    """Return the body of a second-level section."""
    match = re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text)
    assert match, f"no section '{heading}'"
    return match[1]


def _first_column(section: str) -> list[str]:
    return re.findall(r"(?m)^\| `([^`]+)` \|", section)


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

    def test_the_tool_count_quoted_elsewhere_is_current(self):
        for page in (ROOT / "README.md", DOCS / "configuration.md"):
            counts = re.findall(r"(\d+)(?: of the (\d+))? tools", page.read_text())
            assert counts, page.name
            assert {int(total or count) for count, total in counts} == {len(TOOLS)}, page.name


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_links_between_the_pages_lead_somewhere(page: Path):
    """The README links by full URL, since HACS shows it outside the repository."""
    for target in re.findall(r"\]\(([^)\s]+)\)", page.read_text()):
        if target.startswith(REPO_URL):
            path, _, anchor = target[len(REPO_URL) :].partition("#")
            linked = ROOT / path
        elif target.startswith(("http://", "https://")):
            continue
        else:
            path, _, anchor = target.partition("#")
            linked = (page.parent / path) if path else page
        assert linked.is_file(), f"{page.name} links to missing {target}"
        if anchor and linked.suffix == ".md":
            assert anchor in _anchors(linked), f"{page.name} links to missing anchor {target}"

"""End-to-end tests against a real Home Assistant instance.

Proof of concept for the pivot on #44: instead of constructing the view by hand
with a mock ``hass`` and a faked request, this sets the integration up through
HA's real config-entry machinery and drives it over the real aiohttp test
client. Per HA version in the CI matrix this proves three things the current
suite cannot:

* the component actually *loads* (manifest, requirements, ``async_setup_entry``),
* the HTTP views actually *route* through aiohttp,
* native HA auth actually *accepts* a real token and *rejects* a missing one.

Auth is exercised for real via the helper's ``hass_client`` (authenticated) and
``hass_client_no_auth`` (anonymous) fixtures, which mint and attach real HA
access tokens, so no token is faked. The OIDC provider is a separate integration
that is not installed here; its import seam is mocked in ``conftest.py`` and the
token validator returns ``None``, so requests fall through to the native-auth
path under test.
"""

import pytest
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.mcp_server_http_transport.const import (
    CONF_NATIVE_AUTH,
    DOMAIN,
    ISSUE_ENDPOINT_CONFLICT,
    MCP_HTTP_PATH,
    MCP_PATH,
)


@pytest.fixture
async def loaded_entry(enable_custom_integrations: None, hass: HomeAssistant) -> MockConfigEntry:
    """Set the integration up for real with native auth enabled.

    Running ``async_setup_entry`` registers the real HTTP views on ``hass.http``,
    so this must happen after ``http`` is up and before a test client is created.
    """
    assert await async_setup_component(hass, "http", {})

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NATIVE_AUTH: True})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # The integration loaded cleanly on this HA version.
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_authenticated_request_is_served(
    loaded_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """A request with a real HA token routes through aiohttp and is served."""
    client = await hass_client()

    resp = await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )

    assert resp.status == 200
    body = await resp.json()
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == "2024-11-05"
    caps = body["result"]["capabilities"]
    assert {"tools", "resources", "prompts"} <= caps.keys()


async def test_tools_list_dispatches_to_real_handler(
    loaded_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """tools/list dispatches to the real handler and returns well-formed tools."""
    client = await hass_client()

    resp = await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 2},
    )

    assert resp.status == 200
    tools = (await resp.json())["result"]["tools"]
    assert tools, "expected at least one tool"
    for tool in tools:
        assert {"name", "description", "inputSchema"} <= tool.keys()
        assert tool["inputSchema"]["type"] == "object"


async def test_unauthenticated_request_is_rejected(
    loaded_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A request without a token hits the real native-auth rejection path."""
    client = await hass_client_no_auth()

    resp = await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )

    assert resp.status == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert (await resp.json())["error"] == "invalid_token"


async def test_unauthenticated_get_carries_the_challenge(
    loaded_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A GET probe reaches the view instead of aiohttp's own method-not-allowed.

    aiohttp answers an unrouted method itself, and that response carries no
    WWW-Authenticate, so this asserts the route exists rather than that the
    handler body is correct.
    """
    client = await hass_client_no_auth()

    resp = await client.get("/api/mcp")

    assert resp.status == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert (await resp.json())["error"] == "invalid_token"


async def test_dedicated_path_is_served(
    loaded_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The path only this integration claims routes to it."""
    client = await hass_client()

    resp = await client.post(
        MCP_HTTP_PATH,
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )

    assert resp.status == 200
    assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


async def test_dedicated_path_challenges_an_unauthenticated_probe(
    loaded_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A GET probe on the dedicated path gets the same challenge as /api/mcp."""
    client = await hass_client_no_auth()

    resp = await client.get(MCP_HTTP_PATH)

    assert resp.status == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


async def test_no_repair_when_nothing_else_serves_api_mcp(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
) -> None:
    """An uncontested endpoint raises no repair."""
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ENDPOINT_CONFLICT) is None


class _CompetingMCPView(HomeAssistantView):
    """Stand-in for Home Assistant's built-in mcp_server streamable endpoint.

    Core registers POST /api/mcp (and no GET) from its own async_setup, which
    runs before this integration's config entry on a normal startup.
    """

    url = MCP_PATH
    name = "test:competing_mcp"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Answer the way the other integration would."""
        return web.json_response({"served_by": "built-in"})


@pytest.fixture
async def contested_entry(enable_custom_integrations: None, hass: HomeAssistant) -> MockConfigEntry:
    """Set the integration up with /api/mcp already taken by something else."""
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(_CompetingMCPView())

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NATIVE_AUTH: True})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_contested_path_is_left_to_the_integration_that_claimed_it(
    contested_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """aiohttp keeps sending /api/mcp to whichever view registered first."""
    client = await hass_client()

    resp = await client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )

    assert resp.status == 200
    assert (await resp.json()) == {"served_by": "built-in"}


async def test_contested_path_is_not_half_claimed(
    contested_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """This integration registers no method at all on a path it does not own."""
    client = await hass_client_no_auth()

    resp = await client.get(MCP_PATH)

    assert resp.status == 405


async def test_dedicated_path_serves_while_api_mcp_is_contested(
    contested_entry: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    """The dedicated path reaches this integration no matter who holds /api/mcp."""
    client = await hass_client()

    resp = await client.post(
        MCP_HTTP_PATH,
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )

    assert resp.status == 200
    assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


async def test_contested_path_raises_a_repair(
    contested_entry: MockConfigEntry,
    hass: HomeAssistant,
) -> None:
    """The silent shadowing surfaces as a repair naming the dedicated path."""
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ENDPOINT_CONFLICT)

    assert issue is not None
    assert issue.translation_placeholders == {
        "path": MCP_PATH,
        "dedicated_path": MCP_HTTP_PATH,
    }

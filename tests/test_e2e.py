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
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.mcp_server_http_transport.const import CONF_NATIVE_AUTH, DOMAIN


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

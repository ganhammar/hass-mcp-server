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

import json
from ipaddress import ip_address
from socket import herror
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.ban import KEY_BAN_MANAGER, KEY_FAILED_LOGIN_ATTEMPTS
from homeassistant.config_entries import ConfigEntryState, ConfigFlow, OptionsFlow
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import ATTR_COMPONENT, async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockUser,
    async_capture_events,
    mock_config_flow,
    mock_integration,
    mock_platform,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.mcp_server_http_transport.const import (
    CONF_DISABLED_TOOLS,
    CONF_INTEGRATION_MANAGEMENT,
    CONF_NATIVE_AUTH,
    CONF_READ_ONLY,
    DOMAIN,
    EVENT_TOOL_CALLED,
    ISSUE_ENDPOINT_CONFLICT,
    MCP_HTTP_PATH,
    MCP_PATH,
    RESOURCE_METADATA_PREFIX,
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


async def test_non_admin_token_is_refused(
    loaded_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
    hass_read_only_access_token: str,
) -> None:
    """A real token for a non-administrator is valid but not enough."""
    client = await hass_client_no_auth()

    resp = await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        headers={"Authorization": f"Bearer {hass_read_only_access_token}"},
    )

    assert resp.status == 403
    assert (await resp.json())["error"] == "insufficient_scope"
    assert resp.headers["WWW-Authenticate"].startswith('Bearer error="insufficient_scope"')


async def test_a_write_tool_fires_the_audit_event_as_its_caller(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    hass_client: ClientSessionGenerator,
) -> None:
    """The event carries the caller, and so does the context Home Assistant records."""
    events = async_capture_events(hass, EVENT_TOOL_CALLED)
    client = await hass_client()

    resp = await client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "fire_event",
                "arguments": {"event_type": "mcp_e2e", "event_data": {"api_key": "k"}},
            },
            "id": 1,
        },
    )
    await hass.async_block_till_done()

    assert resp.status == 200
    (event,) = events
    assert event.data == {
        "tool": "fire_event",
        "user_id": hass_admin_user.id,
        "auth_method": "native",
        "outcome": "ok",
        "arguments": {"event_type": "mcp_e2e", "event_data": {"api_key": "***"}},
    }
    assert event.context.user_id == hass_admin_user.id


async def test_a_read_only_tool_fires_no_audit_event(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """Reads would flood the recorder, so they are only logged."""
    events = async_capture_events(hass, EVENT_TOOL_CALLED)
    client = await hass_client()

    resp = await client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "get_state", "arguments": {"entity_id": "sun.sun"}},
            "id": 1,
        },
    )
    await hass.async_block_till_done()

    assert resp.status == 200
    assert events == []


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


@pytest.fixture
async def banning_entry(enable_custom_integrations: None, hass: HomeAssistant) -> MockConfigEntry:
    """The integration set up behind an http component with IP banning switched on."""
    assert await async_setup_component(
        hass, "http", {"http": {"ip_ban_enabled": True, "login_attempts_threshold": 10}}
    )

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NATIVE_AUTH: True})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_a_rejected_bearer_token_counts_as_a_failed_login(
    banning_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """A bad token reaches Home Assistant's login attempt counter, a bare probe does not."""
    client = await hass_client_no_auth()
    attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    request = {"jsonrpc": "2.0", "method": "initialize", "id": 1}

    # The reverse DNS lookup process_wrong_login does would hit the network.
    with patch("homeassistant.components.http.ban.gethostbyaddr", side_effect=herror):
        resp = await client.post("/api/mcp", json=request)
        assert resp.status == 401
        assert (await client.get("/api/mcp")).status == 401
        assert attempts == {}

        resp = await client.post(
            "/api/mcp", json=request, headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert resp.status == 401
        assert (await resp.json())["error"] == "invalid_token"
        assert attempts[ip_address("127.0.0.1")] == 1

        resp = await client.get("/api/mcp", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status == 401
        assert attempts[ip_address("127.0.0.1")] == 2


async def test_a_valid_token_is_not_counted_as_a_failed_login(
    banning_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    client = await hass_client()

    resp = await client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})

    assert resp.status == 200
    assert hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS] == {}


async def test_an_authorized_request_clears_earlier_failures(
    banning_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """An expired token presented once an hour before refreshing never adds up to a ban."""
    client = await hass_client()
    attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    request = {"jsonrpc": "2.0", "method": "initialize", "id": 1}

    with patch("homeassistant.components.http.ban.gethostbyaddr", side_effect=herror):
        resp = await client.post(
            "/api/mcp", json=request, headers={"Authorization": "Bearer expired-token"}
        )
    assert resp.status == 401
    assert attempts[ip_address("127.0.0.1")] == 1

    resp = await client.post("/api/mcp", json=request)

    assert resp.status == 200
    assert attempts == {}


async def test_a_non_admin_token_is_not_counted_as_a_failed_login(
    banning_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_client_no_auth()

    resp = await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        headers={"Authorization": f"Bearer {hass_read_only_access_token}"},
    )

    assert resp.status == 403
    assert hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS] == {}


async def test_reaching_the_threshold_bans_the_address(
    enable_custom_integrations: None,
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Enough rejected tokens trip Home Assistant's own ban, which then blocks every request."""
    assert await async_setup_component(
        hass, "http", {"http": {"ip_ban_enabled": True, "login_attempts_threshold": 2}}
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NATIVE_AUTH: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client = await hass_client_no_auth()
    request = {"jsonrpc": "2.0", "method": "initialize", "id": 1}
    bad = {"Authorization": "Bearer not-a-real-token"}

    # The ban is kept in memory for the middleware and appended to ip_bans.yaml;
    # the file write is the only part that must not reach the test config dir.
    with (
        patch("homeassistant.components.http.ban.gethostbyaddr", side_effect=herror),
        patch("homeassistant.components.http.ban.IpBanManager._add_ban"),
    ):
        assert (await client.post("/api/mcp", json=request, headers=bad)).status == 401
        assert (await client.post("/api/mcp", json=request, headers=bad)).status == 401
        assert ip_address("127.0.0.1") in hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup

        # Banned means banned for everything from that address, probes included.
        assert (await client.post("/api/mcp", json=request)).status == 403
        assert (await client.get("/api/mcp")).status == 403


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


async def test_contested_path_metadata_is_not_served_either(
    contested_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Nothing else serves this metadata, so answering would misdirect a client.

    A client that gets a 401 from the integration holding /api/mcp builds the
    RFC 9728 path-suffixed URL. Answering it here would name this integration's
    authorization server for a token that integration will reject.
    """
    client = await hass_client_no_auth()

    resp = await client.get(f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}")

    assert resp.status == 404


async def test_dedicated_path_metadata_is_still_served(
    contested_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Discovery for the path this integration does serve keeps working."""
    client = await hass_client_no_auth()

    resp = await client.get(f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}")

    assert resp.status == 200
    assert (await resp.json())["resource"].endswith(MCP_HTTP_PATH)


async def test_root_metadata_names_a_path_this_integration_answers(
    contested_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The bare well-known path is where a client falls back when the suffixed one 404s.

    Home Assistant's auth component claims this path on the versions that have a
    root view of its own; where it does not, this integration answers and must
    not name the endpoint another integration is holding.
    """
    client = await hass_client_no_auth()

    resp = await client.get(RESOURCE_METADATA_PREFIX)

    if resp.status == 404:
        pytest.skip("Home Assistant's auth component serves this path on this version")
    assert resp.status == 200
    assert (await resp.json())["resource"].endswith(MCP_HTTP_PATH)


async def test_root_metadata_names_api_mcp_while_this_integration_holds_it(
    loaded_entry: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """An uncontested instance keeps advertising /api/mcp as the resource."""
    client = await hass_client_no_auth()

    resp = await client.get(RESOURCE_METADATA_PREFIX)

    if resp.status == 404:
        pytest.skip("Home Assistant's auth component serves this path on this version")
    assert resp.status == 200
    assert (await resp.json())["resource"].endswith(MCP_PATH)


async def test_a_competitor_arriving_after_setup_raises_the_repair(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
) -> None:
    """The user adding the built-in integration later is the likely path here."""
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ENDPOINT_CONFLICT) is None

    hass.http.register_view(_CompetingMCPView())
    # The check re-runs on any component load, so the event names one that is
    # actually loaded here rather than the integration being stood in for.
    hass.bus.async_fire(EVENT_COMPONENT_LOADED, {ATTR_COMPONENT: "http"})
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ENDPOINT_CONFLICT) is not None


async def test_a_competitor_arriving_after_setup_does_not_take_the_path(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """Routes registered first keep answering, so this integration still serves."""
    hass.http.register_view(_CompetingMCPView())
    client = await hass_client()

    resp = await client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )

    assert resp.status == 200
    assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


async def test_a_reload_keeps_serving_both_paths(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """A reload updates the registered view rather than queueing another one."""
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    client = await hass_client()
    for path in (MCP_PATH, MCP_HTTP_PATH):
        resp = await client.post(
            path,
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )

        assert resp.status == 200, path
        assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


_CREATE_CALLS = {
    "automation": (
        "create_automation",
        {
            "config": {
                "alias": "Porch light",
                "triggers": [{"trigger": "event", "event_type": "test"}],
                "actions": [],
            }
        },
    ),
    "script": ("create_script", {"key": "porch", "config": {"sequence": []}}),
    "scene": ("create_scene", {"config": {"name": "Evening", "entities": {}}}),
    "helpers": ("create_helper", {"domain": "input_boolean", "config": {"name": "Guests"}}),
}


@pytest.mark.parametrize("scope", list(_CREATE_CALLS))
async def test_create_tools_categorize_and_set_icon_on_what_the_real_load_registered(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    tmp_path,
    scope: str,
) -> None:
    """Each create tool's item is in the entity registry by the time it is categorized.

    Categories and the icon override live on the entity registry entry, which only exists once the
    reload (or, for a helper, the storage collection) has loaded the new item,
    and each domain registers it under its own platform and unique ID. This
    drives the real loading so a release that stops registering within the call,
    or changes either key, fails here rather than leaving items uncategorized.
    """
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\n"
        "script: !include scripts.yaml\n"
        "scene: !include scenes.yaml\n"
    )
    (tmp_path / "automations.yaml").write_text("[]\n")
    (tmp_path / "scenes.yaml").write_text("[]\n")
    (tmp_path / "scripts.yaml").write_text("{}\n")
    hass.config.config_dir = str(tmp_path)
    for domain in ("automation", "script", "scene", "input_boolean"):
        assert await async_setup_component(hass, domain, {})
    client = await hass_client()
    tool, arguments = _CREATE_CALLS[scope]

    resp = await client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": {**arguments, "category": "Mine", "icon": "mdi:star"},
            },
            "id": 3,
        },
    )

    text = (await resp.json())["result"]["content"][0]["text"]
    assert "with category 'Mine'" in text, text
    assert "with icon mdi:star" in text, text
    (category,) = cr.async_get(hass).async_list_categories(scope=scope)
    categorized = [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.categories == {scope: category.category_id} and entry.icon == "mdi:star"
    ]
    assert len(categorized) == 1


class _OneFormFlow(ConfigFlow):
    """A config flow with a single form, the shape of most simple integrations."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required("url"): str})
            )
        return self.async_create_entry(title="Demo", data=user_input)

    @staticmethod
    def async_get_options_flow(config_entry):
        return _OneFormOptionsFlow()


class _OneFormOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is None:
            schema = vol.Schema(
                {vol.Required("interval"): int, vol.Required("api_key", default="k3y"): str}
            )
            return self.async_show_form(step_id="init", data_schema=schema)
        return self.async_create_entry(data=user_input)


async def test_an_integration_is_set_up_reloaded_and_removed_through_the_tools(
    enable_custom_integrations: None,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """The config flow tools drive the real flow manager from first step to removal.

    The flow manager, its result dicts and the schema serializer all belong to
    core, so this runs on every Home Assistant version in the matrix. It also
    shows the option reaching the tools through a real setup, and the audit
    event carrying the submitted field names without their values.
    """
    assert await async_setup_component(hass, "http", {})
    own = MockConfigEntry(
        domain=DOMAIN, data={CONF_NATIVE_AUTH: True, CONF_INTEGRATION_MANAGEMENT: True}
    )
    own.add_to_hass(hass)
    assert await hass.config_entries.async_setup(own.entry_id)
    mock_integration(
        hass,
        MockModule(
            "mcp_demo",
            async_setup_entry=AsyncMock(return_value=True),
            async_unload_entry=AsyncMock(return_value=True),
        ),
    )
    mock_platform(hass, "mcp_demo.config_flow", None)
    events = async_capture_events(hass, EVENT_TOOL_CALLED)
    client = await hass_client()

    async def call(name: str, arguments: dict) -> dict:
        resp = await client.post(
            MCP_HTTP_PATH,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
                "id": 1,
            },
        )
        result = (await resp.json())["result"]
        assert "isError" not in result, result
        return json.loads(result["content"][0]["text"])

    with mock_config_flow("mcp_demo", _OneFormFlow):
        form = await call("start_config_flow", {"domain": "mcp_demo"})
        assert form["type"] == "form"
        assert form["data_schema"] == [{"name": "url", "required": True, "type": "string"}]

        # A value the schema rejects is reported per field and leaves the flow open.
        resp = await client.post(
            MCP_HTTP_PATH,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "continue_config_flow",
                    "arguments": {"flow_id": form["flow_id"], "user_input": {"url": 5}},
                },
                "id": 1,
            },
        )
        rejected = (await resp.json())["result"]
        assert "isError" not in rejected, rejected
        assert "still on the same step" in rejected["content"][0]["text"]
        assert '"url"' in rejected["content"][0]["text"]

        created = await call(
            "continue_config_flow",
            {"flow_id": form["flow_id"], "user_input": {"url": "https://example.com/?key=k"}},
        )

        (entry,) = hass.config_entries.async_entries("mcp_demo")
        assert created["type"] == "create_entry"
        assert created["entry"]["entry_id"] == entry.entry_id
        assert entry.state is ConfigEntryState.LOADED
        await hass.async_block_till_done()
        assert events[-1].data["arguments"] == {
            "flow_id": form["flow_id"],
            "user_input": {"url": "***"},
        }

        options = await call("start_options_flow", {"entry_id": entry.entry_id})
        # The stored secret the form is filled in with is masked, and kept when sent back so.
        assert options["data_schema"] == [
            {"name": "interval", "required": True, "type": "integer"},
            {"name": "api_key", "required": True, "type": "string", "default": "***"},
        ]
        saved = await call(
            "continue_config_flow",
            {"flow_id": options["flow_id"], "user_input": {"interval": 5, "api_key": "***"}},
        )
        assert saved["options_saved"] is True
        assert entry.options == {"interval": 5, "api_key": "k3y"}

        disabled = await call(
            "set_integration_enabled", {"entry_id": entry.entry_id, "enabled": False}
        )
        assert disabled["disabled_by"] == "user"
        enabled = await call(
            "set_integration_enabled", {"entry_id": entry.entry_id, "enabled": True}
        )
        assert (enabled["disabled_by"], enabled["state"]) == (None, str(ConfigEntryState.LOADED))

        refused = await client.post(
            MCP_HTTP_PATH,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "start_config_flow", "arguments": {"domain": DOMAIN}},
                "id": 1,
            },
        )
        assert (
            "Refusing to configure the MCP Server itself"
            in (await refused.json())["result"]["content"][0]["text"]
        )
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1

        reloaded = await call("reload_integration", {"entry_id": entry.entry_id})
        assert reloaded["state"] == str(ConfigEntryState.LOADED)
        assert reloaded["require_restart"] is False

        removed = await call("delete_integration", {"entry_id": entry.entry_id, "confirm": True})
        assert removed["require_restart"] is False
        assert hass.config_entries.async_entries("mcp_demo") == []


async def test_areas_devices_and_entities_are_managed_over_http(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """The registry tools run against this core's own registries, start to finish."""
    mock_integration(
        hass,
        MockModule(
            "mcp_gadget",
            async_setup_entry=AsyncMock(return_value=True),
            async_remove_config_entry_device=AsyncMock(return_value=True),
        ),
    )
    mock_platform(hass, "mcp_gadget.config_flow", None)
    owner = MockConfigEntry(domain="mcp_gadget")
    owner.add_to_hass(hass)
    with mock_config_flow("mcp_gadget", _OneFormFlow):
        assert await hass.config_entries.async_setup(owner.entry_id)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("mcp_gadget", "plug")}, name="Plug"
    )
    entity_id = entities.async_get_or_create(
        "switch", "mcp_gadget", "plug", device_id=device.id, config_entry=owner
    ).entity_id
    leftover = entities.async_get_or_create("sensor", "mcp_gadget", "gone").entity_id
    client = await hass_client()

    async def call(name: str, arguments: dict) -> str:
        resp = await client.post(
            MCP_HTTP_PATH,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
                "id": 1,
            },
        )
        result = (await resp.json())["result"]
        assert "isError" not in result, result
        return result["content"][0]["text"]

    def created(text: str) -> dict:
        return json.loads(text[text.index("{") :])

    floor = created(await call("create_floor", {"name": "Upstairs", "level": 1}))
    area = created(await call("create_area", {"name": "Study", "floor_id": floor["floor_id"]}))
    assert json.loads(await call("list_floors", {})) == [floor]
    assert json.loads(await call("list_areas", {})) == [area]

    moved = created(await call("update_device", {"device_id": device.id, "area_id": area["id"]}))
    assert moved["area_id"] == area["id"]
    hidden = json.loads(
        await call(
            "update_entity_registry", {"entity_id": entity_id, "hidden": True, "apply": True}
        )
    )
    assert hidden["after"]["hidden_by"] == "user"

    hass.states.async_set(entity_id, "on")
    refused = await call("delete_entity", {"entity_id": entity_id, "confirm": True})
    assert "still provided" in refused
    assert (
        json.loads(await call("delete_entity", {"entity_id": leftover, "confirm": True}))[
            "deleted"
        ]["entity_id"]
        == leftover
    )

    gated = await call("delete_device", {"device_id": device.id, "confirm": True})
    assert gated.startswith("Deleting a device is part of integration management")
    hass.data[DOMAIN]["integration_management"] = True
    deleted = await call("delete_device", {"device_id": device.id, "confirm": True})
    assert deleted == f"Deleted device 'Plug' ({device.id}), with 1 entity"
    await hass.async_block_till_done()
    assert devices.async_get(device.id) is None
    assert entities.async_get(entity_id) is None

    assert await call("delete_area", {"area_id": area["id"]}) == (
        f"Deleted area 'Study' ({area['id']})"
    )
    assert await call("delete_floor", {"floor_id": floor["floor_id"]}) == (
        f"Deleted floor 'Upstairs' ({floor['floor_id']})"
    )


async def test_a_second_entry_of_the_integration_cannot_be_set_up(
    loaded_entry: MockConfigEntry, hass: HomeAssistant
) -> None:
    """Setting one up would replace the tool limits of the first."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert (result["type"], result["reason"]) == ("abort", "single_instance_allowed")
    assert hass.config_entries.async_entries(DOMAIN) == [loaded_entry]


async def test_entries_from_before_the_single_entry_rule_are_reported_and_recover(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """A second entry left over from an older version shares the first one's options."""
    assert ir.async_get(hass).async_get_issue(DOMAIN, "multiple_entries") is None
    second = MockConfigEntry(domain=DOMAIN, data={CONF_NATIVE_AUTH: True, CONF_READ_ONLY: True})
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, "multiple_entries") is not None
    assert hass.data[DOMAIN]["read_only"] is True

    # Removing the extra entry puts the remaining one's options back in effect.
    await hass.config_entries.async_remove(second.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, "multiple_entries") is None
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN]["read_only"] is False
    client = await hass_client()
    resp = await client.post(
        MCP_HTTP_PATH, json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}
    )
    assert "get_state" in {tool["name"] for tool in (await resp.json())["result"]["tools"]}


async def test_diagnostics_are_read_over_http(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    backup_manager,
    hass_client: ClientSessionGenerator,
) -> None:
    """Repairs, system health and backups come from this core's own components."""
    assert await async_setup_component(hass, "system_health", {})
    ir.async_create_issue(
        hass,
        DOMAIN,
        "mcp_e2e",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="endpoint_conflict",
        translation_placeholders={"path": "/api/mcp", "dedicated_path": "/api/mcp_http"},
    )
    client = await hass_client()

    async def call(name: str) -> dict | list:
        resp = await client.post(
            MCP_HTTP_PATH,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
                "id": 1,
            },
        )
        result = (await resp.json())["result"]
        assert "isError" not in result, result
        return json.loads(result["content"][0]["text"])

    issues = {issue["issue_id"]: issue for issue in await call("list_repairs")}
    assert issues["mcp_e2e"]["title"] == "Another integration also serves /api/mcp"
    assert issues["mcp_e2e"]["severity"] == "warning"

    assert isinstance(await call("get_system_health"), dict)

    backups = await call("list_backups")
    assert (backups["backups"], backups["total"], backups["state"]) == ([], 0, "idle")


async def test_config_search_finds_references_over_http(
    loaded_entry: MockConfigEntry,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    tmp_path,
) -> None:
    """One search finds an entity in every kind of config this core stores.

    The dashboard is saved through save_dashboard_config, so the pointer the search
    returns is checked against the config that tool wrote on this core's Lovelace.
    """
    (tmp_path / "automations.yaml").write_text(
        '- id: "1"\n  alias: Kitchen motion\n  triggers: []\n'
        "  actions:\n    - action: light.turn_on\n      target:\n"
        "        entity_id: light.kitchen\n"
    )
    (tmp_path / "scripts.yaml").write_text(
        "evening:\n  sequence:\n    - action: light.turn_off\n"
        "      target:\n        entity_id: light.kitchen\n"
    )
    (tmp_path / "scenes.yaml").write_text(
        '- id: "2"\n  name: Dinner\n  entities:\n    light.kitchen:\n      state: "on"\n'
    )
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "lovelace", {})
    client = await hass_client()

    async def call(name: str, arguments: dict) -> str:
        resp = await client.post(
            MCP_HTTP_PATH,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
                "id": 1,
            },
        )
        result = (await resp.json())["result"]
        assert "isError" not in result, result
        return result["content"][0]["text"]

    dashboard = {"views": [{"cards": [{"type": "tile", "entity": "light.kitchen"}]}]}
    saved = await call("save_dashboard_config", {"url_path": "default", "config": dashboard})
    assert saved.startswith("Successfully saved"), saved

    found = json.loads(await call("search_config", {"query": "Light.Kitchen"}))

    assert found["counts"] == {"automation": 1, "script": 1, "scene": 1, "dashboard": 1}
    assert {(item["type"], item["id"]): item["matches"] for item in found["items"]} == {
        ("automation", "1"): [{"path": "/actions/0/target/entity_id", "value": "light.kitchen"}],
        ("script", "evening"): [{"path": "/sequence/0/target/entity_id", "value": "light.kitchen"}],
        ("scene", "2"): [{"path": "/entities/light.kitchen", "key": "light.kitchen"}],
        ("dashboard", "default"): [{"path": "/views/0/cards/0/entity", "value": "light.kitchen"}],
    }
    pointed = await call(
        "get_dashboard_config", {"url_path": "default", "path": "/views/0/cards/0/entity"}
    )
    assert json.loads(pointed) == "light.kitchen"


async def test_read_only_mode_and_disabled_tools_limit_what_a_client_can_reach(
    enable_custom_integrations: None,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    """The limits set on the entry hold over HTTP, for listing and for calling."""
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NATIVE_AUTH: True, CONF_READ_ONLY: True, CONF_DISABLED_TOOLS: ["get_error_log"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    events = async_capture_events(hass, "mcp_e2e")
    audit = async_capture_events(hass, EVENT_TOOL_CALLED)
    client = await hass_client()

    async def rpc(method: str, params: dict | None = None) -> dict:
        resp = await client.post(
            MCP_HTTP_PATH,
            json={"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1},
        )
        assert resp.status == 200
        return await resp.json()

    names = {tool["name"] for tool in (await rpc("tools/list"))["result"]["tools"]}
    assert "get_state" in names
    assert not names & {"fire_event", "call_service", "get_error_log"}

    refused = await rpc(
        "tools/call", {"name": "fire_event", "arguments": {"event_type": "mcp_e2e"}}
    )
    assert refused["error"]["code"] == -32602
    assert "read-only mode" in refused["error"]["message"]
    disabled = await rpc("tools/call", {"name": "get_error_log", "arguments": {}})
    assert disabled["error"]["code"] == -32602
    assert "turned off" in disabled["error"]["message"]
    allowed = await rpc("tools/call", {"name": "get_config", "arguments": {}})
    assert "error" not in allowed

    await hass.async_block_till_done()
    assert events == []
    # Both refusals are on the audit trail, marked as never having run. The
    # disabled tool only reads, and a read that ran would not be there.
    assert [(event.data["tool"], event.data["outcome"]) for event in audit] == [
        ("fire_event", "invalid"),
        ("get_error_log", "invalid"),
    ]

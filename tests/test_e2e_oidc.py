"""End-to-end test of the OIDC audience contract this server enforces.

The MCP server is an OAuth protected resource (RFC 8707). For the OIDC path it
delegates signature/issuer checking to the paired provider's
``validate_access_token``, but it owns one piece of the contract itself: it
derives ``expected_audience`` from the endpoint the request came in on, and
rejects any token whose ``aud`` is not bound to that resource. The two endpoints
are separate resources, so a token minted for one is not accepted on the other.
That seam breaks silently if either repo changes how the resource URI is derived
or how ``aud`` is shaped, because each repo's own suite stays green.

This pins the seam by exercising the *real* ``validate_access_token`` (the
function ``http.py`` imports), which lives in the paired ``oidc_provider``
integration. CI vendors that integration into ``custom_components/`` for this
file; when it isn't present (a normal local checkout) ``conftest.py`` stubs the
module instead and these tests skip.

Most tests need no issuance flow: a token is signed directly with a provider
keypair seeded into ``hass.data`` and POSTed to the endpoint under test. The
provider is not set up for those, so it has collected no description and the
tokens are not limited by their scopes. ``TestChosenAccess`` sets the real
provider up instead and drives its authorization flow, browser-free, from the
consent screen's choice to the tools the resulting token is offered.
"""

import base64
import hashlib
import re
import secrets
import time
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockUser,
    async_capture_events,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.mcp_server_http_transport.const import (
    DOMAIN,
    EVENT_TOOL_CALLED,
    MCP_HTTP_PATH,
    MCP_PATH,
)

# Skip unless the real paired provider is vendored. When conftest.py installs the
# stub instead, the imported module is a Mock and there is nothing real to pin.
try:
    from custom_components.oidc_provider import token_validator as _token_validator

    _PROVIDER_AVAILABLE = not isinstance(_token_validator, Mock)
except ImportError:
    _PROVIDER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _PROVIDER_AVAILABLE,
    reason="paired oidc_provider integration not vendored into the test environment",
)

OIDC_DOMAIN = "oidc_provider"

# The issuer is derived from the request host; pin it with forwarded headers so
# the token claims are deterministic. The MCP server binds the audience to
# {issuer}/api/mcp (RFC 8707).
FORWARDED = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "mcp.example.com"}
ISSUER_BASE = "https://mcp.example.com"
TOKEN_ISSUER = f"{ISSUER_BASE}/oidc"
RESOURCE = f"{ISSUER_BASE}{MCP_PATH}"
RESOURCE_HTTP = f"{ISSUER_BASE}{MCP_HTTP_PATH}"


@pytest.fixture
async def oidc_ready(enable_custom_integrations: None, hass: HomeAssistant) -> rsa.RSAPrivateKey:
    """Set up the MCP server (OIDC path only) and seed the provider's keypair.

    The validator only reads ``hass.data["oidc_provider"]["jwt_public_key"]``, so
    the provider's own config entry need not be set up; seeding the key directly
    is enough to exercise the real validation path.
    """
    assert await async_setup_component(hass, "http", {})

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hass.data[OIDC_DOMAIN] = {"jwt_public_key": private_key.public_key(), "clients": {}}

    # No native-auth fallback: the OIDC path is the only thing that can authorize.
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    return private_key


def _sign(
    private_key: rsa.RSAPrivateKey,
    *,
    aud,
    sub: str,
    iss: str = TOKEN_ISSUER,
    exp_offset: int = 3600,
    claims: dict | None = None,
) -> str:
    """Sign an access token the provider would issue, with controllable claims.

    The provider signs the id of the Home Assistant user who authorized as the
    subject, and the server resolves that user before serving the request.
    """
    now = int(time.time())
    payload = {"sub": sub, "iat": now, "exp": now + exp_offset, "iss": iss, **(claims or {})}
    if aud is not None:
        payload["aud"] = aud
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # The provider accepts only a token typed as an access token (RFC 9068).
    return jwt.encode(payload, pem, algorithm="RS256", headers={"typ": "at+jwt"})


async def _post_initialize(client: ClientSessionGenerator, token: str, path: str = MCP_PATH):
    return await client.post(
        path,
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        headers={**FORWARDED, "Authorization": f"Bearer {token}"},
    )


async def test_audience_bound_token_is_accepted(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """A token whose aud is this resource passes the real OIDC validation path."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE, sub=hass_admin_user.id)

    resp = await _post_initialize(client, token)

    assert resp.status == 200
    assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


async def test_token_bound_to_other_resource_is_rejected(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """A valid token bound to a different resource is rejected (the contract)."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud="https://evil.example.com/api/mcp", sub=hass_admin_user.id)

    resp = await _post_initialize(client, token)

    assert resp.status == 401
    assert (await resp.json())["error"] == "invalid_token"


async def test_token_without_audience_is_rejected(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """A token with no aud claim is rejected."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=None, sub=hass_admin_user.id)

    resp = await _post_initialize(client, token)

    assert resp.status == 401


async def test_expired_token_is_rejected(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """An expired token is rejected even though its audience is correct."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE, exp_offset=-3600, sub=hass_admin_user.id)

    resp = await _post_initialize(client, token)

    assert resp.status == 401


async def test_unauthenticated_get_advertises_resource_metadata(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """A GET probe is told where this resource's metadata lives.

    The advertised path is the RFC 9728 suffixed form. Home Assistant serves its
    own metadata at the bare /.well-known/oauth-protected-resource and wins that
    route by registering first, so pointing at the root would send the client to
    HA's own authorization server.
    """
    client = await hass_client_no_auth()

    resp = await client.get("/api/mcp", headers=FORWARDED)

    assert resp.status == 401
    assert resp.headers["WWW-Authenticate"] == (
        'Bearer realm="MCP Server",'
        f' resource_metadata="{ISSUER_BASE}/.well-known/oauth-protected-resource/api/mcp",'
        ' scope="openid"'
    )


async def test_dedicated_path_accepts_a_token_bound_to_it(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """The path this integration serves alone is a resource in its own right."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE_HTTP, sub=hass_admin_user.id)

    resp = await _post_initialize(client, token, MCP_HTTP_PATH)

    assert resp.status == 200
    assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


async def test_dedicated_path_rejects_a_token_bound_to_the_other_endpoint(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """The two endpoints are separate resources, so their tokens do not cross.

    A client that moves to the dedicated path re-runs discovery, is handed that
    path's metadata by the 401, and asks for a token bound to it.
    """
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE, sub=hass_admin_user.id)

    resp = await _post_initialize(client, token, MCP_HTTP_PATH)

    assert resp.status == 401


async def test_dedicated_path_advertises_its_own_resource_metadata(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """The challenge points at the metadata for the path the client called."""
    client = await hass_client_no_auth()

    resp = await client.get(MCP_HTTP_PATH, headers=FORWARDED)

    assert resp.status == 401
    assert resp.headers["WWW-Authenticate"] == (
        'Bearer realm="MCP Server",'
        f' resource_metadata="{ISSUER_BASE}/.well-known/oauth-protected-resource{MCP_HTTP_PATH}",'
        ' scope="openid"'
    )


async def test_dedicated_path_metadata_names_itself_as_the_resource(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Following that pointer yields metadata for the dedicated path."""
    client = await hass_client_no_auth()

    resp = await client.get(
        f"/.well-known/oauth-protected-resource{MCP_HTTP_PATH}", headers=FORWARDED
    )

    assert resp.status == 200
    assert (await resp.json())["resource"] == RESOURCE_HTTP


async def test_a_non_admin_users_token_is_refused(
    oidc_ready: rsa.RSAPrivateKey,
    hass_client_no_auth: ClientSessionGenerator,
    hass_read_only_user: MockUser,
) -> None:
    """A valid token for a user without administrator rights is refused with 403."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE, sub=hass_read_only_user.id)

    resp = await _post_initialize(client, token)

    assert resp.status == 403
    assert (await resp.json())["error"] == "insufficient_scope"


async def test_a_token_for_an_unknown_user_is_refused(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """A subject that names no Home Assistant user grants nothing."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE, sub="no-such-user")

    resp = await _post_initialize(client, token)

    assert resp.status == 403


async def test_the_tokens_client_reaches_the_audit_event(
    oidc_ready: rsa.RSAPrivateKey,
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    hass_admin_user: MockUser,
) -> None:
    """The client and user a real validated token names are the ones the audit trail records."""
    client_id = "https://app.example.com/oauth/client.json"
    events = async_capture_events(hass, EVENT_TOOL_CALLED)
    client = await hass_client_no_auth()
    token = _sign(
        oidc_ready,
        aud=RESOURCE,
        sub=hass_admin_user.id,
        claims={"client_id": client_id, "azp": client_id, "scope": "openid profile"},
    )

    resp = await client.post(
        MCP_PATH,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fire_event", "arguments": {"event_type": "mcp_e2e_oidc"}},
            "id": 1,
        },
        headers={**FORWARDED, "Authorization": f"Bearer {token}"},
    )
    await hass.async_block_till_done()

    assert resp.status == 200
    (event,) = events
    assert event.data["client_id"] == client_id
    assert event.data["auth_method"] == "oidc"
    assert event.data["user_id"] == hass_admin_user.id
    assert event.context.user_id == hass_admin_user.id


# The real provider, set up through its config entry, for the tests below.

REDIRECT_URI = "https://app.example.com/callback"
ADMIN_ONLY_TOOL = "restart_ha"

# A client can put a path into the forwarded host. The issuer, and with it the
# audience this server checks, then carry that path too.
STEERED = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "mcp.example.com/x"}
STEERED_RESOURCE = f"{ISSUER_BASE}/x{MCP_PATH}"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def _setup_provider(hass: HomeAssistant) -> None:
    from custom_components.oidc_provider.config_flow import OIDCProviderConfigFlow
    from custom_components.oidc_provider.const import CONF_ALLOW_DYNAMIC_REGISTRATION

    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=OIDC_DOMAIN,
        data={},
        minor_version=OIDCProviderConfigFlow.MINOR_VERSION,
        options={CONF_ALLOW_DYNAMIC_REGISTRATION: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _setup_server(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _register(client) -> dict:
    resp = await client.post(
        "/oidc/register",
        json={"client_name": "Assistant", "redirect_uris": [REDIRECT_URI]},
        headers=FORWARDED,
    )
    assert resp.status == 201, await resp.text()
    return await resp.json()


async def _sign_in(
    client,
    registration: dict,
    access: dict | None,
    *,
    resource: str = RESOURCE,
    headers: dict[str, str] = FORWARDED,
    offered: list | None = None,
) -> dict:
    """Authorize as the signed-in user, approve with a choice of access, redeem the code.

    Returns the token response. ``access`` is what the consent screen posts: None
    for the plain approve screen, or {"options": {area: setting}}. When ``offered`` is
    a list, what the consent screen offers of access, or None, is appended to it.
    """
    verifier, challenge = _pkce_pair()
    resp = await client.get(
        "/oidc/authorize",
        params={
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        },
        headers=headers,
    )
    assert resp.status == 200, await resp.text()
    request_id = re.search(r"oidc_request_id', '([^']+)'", await resp.text()).group(1)
    if offered is not None:
        resp = await client.get(
            "/oidc/continue", params={"request_id": request_id}, headers=headers
        )
        assert resp.status == 200, await resp.text()
        offered.append((await resp.json()).get("access"))

    decision = {"request_id": request_id, "approve": True}
    if access is not None:
        decision["access"] = access
    resp = await client.post("/oidc/continue", json=decision, headers=headers)
    assert resp.status == 200, await resp.text()
    code = parse_qs(urlparse((await resp.json())["redirect_url"]).query)["code"][0]

    resp = await client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": registration["client_id"],
            "client_secret": registration["client_secret"],
            "code_verifier": verifier,
        },
        headers=headers,
    )
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def _refresh(
    client, registration: dict, refresh_token: str, headers: dict[str, str] = FORWARDED
) -> dict:
    resp = await client.post(
        "/oidc/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": registration["client_id"],
            "client_secret": registration["client_secret"],
        },
        headers=headers,
    )
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def _rpc(
    client,
    token: str,
    method: str,
    params: dict | None = None,
    headers: dict[str, str] = FORWARDED,
):
    body = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params is not None:
        body["params"] = params
    return await client.post(
        MCP_PATH, json=body, headers={**headers, "Authorization": f"Bearer {token}"}
    )


async def _tool_names(client, token: str) -> set[str]:
    resp = await _rpc(client, token, "tools/list")
    assert resp.status == 200, await resp.text()
    return {tool["name"] for tool in (await resp.json())["result"]["tools"]}


def _levels(preset_id: str) -> dict[str, str]:
    from custom_components.mcp_server_http_transport.access import PRESETS

    return dict(next(p for p in PRESETS if p.id == preset_id).levels)


class TestProviderContract:
    """What this server relies on in the provider, beyond validate_access_token."""

    async def test_described_resources_names_the_integration_that_described_it(
        self, enable_custom_integrations: None, hass: HomeAssistant
    ) -> None:
        """http._provider_describes asks described_resources(hass) for this domain."""
        import inspect

        from custom_components.oidc_provider.resources import described_resources

        assert list(inspect.signature(described_resources).parameters) == ["hass"]
        assert described_resources(hass) == []

        await _setup_provider(hass)
        assert [d.domain for d in described_resources(hass)] == []
        await _setup_server(hass)

        assert [d.domain for d in described_resources(hass)] == [DOMAIN]

    async def test_the_provider_does_not_recognise_a_steered_resource(
        self, enable_custom_integrations: None, hass: HomeAssistant
    ) -> None:
        """A resource under a path the description does not name gets no consent choice.

        This is why whether a token is limited cannot be decided by matching the
        audience the request steers: the provider matches nothing either.
        """
        from custom_components.oidc_provider.resources import find_resource

        await _setup_provider(hass)
        await _setup_server(hass)

        for indicator in (RESOURCE, RESOURCE_HTTP, "http://192.168.1.10:8123/api/mcp"):
            assert find_resource(hass, indicator).domain == DOMAIN
        assert find_resource(hass, STEERED_RESOURCE) is None

    async def test_the_validator_returns_the_scope_claim(
        self, oidc_ready: rsa.RSAPrivateKey, hass: HomeAssistant, hass_admin_user: MockUser
    ) -> None:
        from custom_components.oidc_provider.token_validator import validate_access_token

        token = _sign(
            oidc_ready, aud=RESOURCE, sub=hass_admin_user.id, claims={"scope": "openid mcp:read"}
        )

        claims = validate_access_token(hass, token, ISSUER_BASE, expected_audience=RESOURCE)

        assert claims["scope"] == "openid mcp:read"


class TestChosenAccess:
    """The whole path with the real provider: describe, choose, token, what is offered."""

    @pytest.fixture
    async def both(self, enable_custom_integrations: None, hass: HomeAssistant) -> None:
        await _setup_provider(hass)
        await _setup_server(hass)

    async def test_the_provider_collects_and_advertises_the_description(
        self, both: None, hass_client_no_auth: ClientSessionGenerator
    ) -> None:
        client = await hass_client_no_auth()

        resp = await client.get("/oidc/.well-known/openid-configuration", headers=FORWARDED)

        scopes = (await resp.json())["scopes_supported"]
        assert {"openid", "mcp:read", "mcp:admin", "mcp:dashboards:full"} <= set(scopes)

    async def test_the_consent_screen_offers_the_levels(
        self, both: None, hass_client: ClientSessionGenerator
    ) -> None:
        client = await hass_client()
        registration = await _register(client)
        _, challenge = _pkce_pair()
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": registration["client_id"],
                "redirect_uri": REDIRECT_URI,
                "scope": "openid",
                "state": "s",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": RESOURCE,
            },
            headers=FORWARDED,
        )
        request_id = re.search(r"oidc_request_id', '([^']+)'", await resp.text()).group(1)

        resp = await client.get(
            "/oidc/continue", params={"request_id": request_id}, headers=FORWARDED
        )

        assert resp.status == 200, await resp.text()
        access = (await resp.json())["access"]
        assert [p["title"] for p in access["presets"]] == [
            "Look only",
            "Control devices",
            "Edit the setup",
            "Administer",
        ]
        assert "files" in access["unavailable"]

    async def test_a_level_reaches_the_token_and_limits_the_tools(
        self,
        both: None,
        hass: HomeAssistant,
        hass_client: ClientSessionGenerator,
        hass_client_no_auth: ClientSessionGenerator,
    ) -> None:
        provider = await hass_client()
        mcp = await hass_client_no_auth()
        registration = await _register(provider)

        tokens = await _sign_in(provider, registration, {"options": _levels("read")})

        assert tokens["scope"].split() == ["openid", "mcp:read"]
        names = await _tool_names(mcp, tokens["access_token"])
        assert "get_state" in names
        assert "call_service" not in names and ADMIN_ONLY_TOOL not in names

        resp = await _rpc(
            mcp,
            tokens["access_token"],
            "tools/call",
            {"name": "call_service", "arguments": {"domain": "light", "service": "turn_on"}},
        )
        error = (await resp.json())["error"]
        assert error["code"] == -32602
        assert "access granted to this application does not include tool 'call_service'" in (
            error["message"]
        )

    async def test_a_detailed_choice_reaches_the_token_area_by_area(
        self,
        both: None,
        hass_client: ClientSessionGenerator,
        hass_client_no_auth: ClientSessionGenerator,
    ) -> None:
        provider = await hass_client()
        mcp = await hass_client_no_auth()
        registration = await _register(provider)
        choice = {**_levels("admin"), "cameras": "none", "dashboards": "read"}

        tokens = await _sign_in(provider, registration, {"options": choice})

        scopes = set(tokens["scope"].split())
        assert {"mcp:dashboards:read", "mcp:entities:full"} <= scopes
        assert not any(scope.startswith("mcp:cameras") for scope in scopes)
        names = await _tool_names(mcp, tokens["access_token"])
        assert "get_dashboard_config" in names and "save_dashboard_config" not in names
        assert ADMIN_ONLY_TOOL in names

    async def test_the_service_filter_holds_below_diagnostics_at_full(
        self,
        both: None,
        hass: HomeAssistant,
        hass_client: ClientSessionGenerator,
        hass_client_no_auth: ClientSessionGenerator,
    ) -> None:
        calls = []
        hass.services.async_register("test", "ping", lambda call: calls.append(call))
        provider = await hass_client()
        mcp = await hass_client_no_auth()
        registration = await _register(provider)
        tokens = await _sign_in(provider, registration, {"options": _levels("control")})

        async def call(domain: str, service: str) -> dict:
            resp = await _rpc(
                mcp,
                tokens["access_token"],
                "tools/call",
                {"name": "call_service", "arguments": {"domain": domain, "service": service}},
            )
            return (await resp.json())["result"]

        refused = await call("homeassistant", "restart")
        allowed = await call("test", "ping")

        assert refused["isError"] is True
        assert "Diagnostics at Full" in refused["content"][0]["text"]
        assert allowed.get("isError") is not True
        assert len(calls) == 1

    async def test_a_token_granting_nothing_gets_401_and_its_refresh_still_does(
        self,
        both: None,
        hass_client: ClientSessionGenerator,
        hass_client_no_auth: ClientSessionGenerator,
    ) -> None:
        provider = await hass_client()
        mcp = await hass_client_no_auth()
        registration = await _register(provider)
        nothing = dict.fromkeys(_levels("admin"), "none")

        tokens = await _sign_in(provider, registration, {"options": nothing})

        assert tokens["scope"].split() == ["openid"]
        resp = await _rpc(mcp, tokens["access_token"], "initialize")
        assert resp.status == 401
        assert 'error="invalid_token"' in resp.headers["WWW-Authenticate"]
        refreshed = await _refresh(provider, registration, tokens["refresh_token"])
        resp = await _rpc(mcp, refreshed["access_token"], "initialize")
        assert resp.status == 401

    async def test_a_steered_audience_does_not_skip_the_choice(
        self,
        both: None,
        hass_client: ClientSessionGenerator,
        hass_client_no_auth: ClientSessionGenerator,
    ) -> None:
        """A client that steers the resource past the provider's match gains nothing.

        The provider does not recognise the steered resource, so the user sees the
        plain approve screen and the token carries only openid. This server accepts
        the token, since its audience is the steered one, and must treat it as
        granting nothing; so must it the refreshed token, and the client ends back
        at the consent screen.
        """
        provider = await hass_client()
        mcp = await hass_client_no_auth()
        registration = await _register(provider)
        offered: list = []

        tokens = await _sign_in(
            provider,
            registration,
            None,
            resource=STEERED_RESOURCE,
            headers=STEERED,
            offered=offered,
        )

        assert offered == [None]
        assert tokens["scope"].split() == ["openid"]
        resp = await _rpc(mcp, tokens["access_token"], "tools/list", headers=STEERED)
        assert resp.status == 401, await resp.text()
        assert 'error="invalid_token"' in resp.headers["WWW-Authenticate"]

        refreshed = await _refresh(provider, registration, tokens["refresh_token"], STEERED)

        assert refreshed["scope"].split() == ["openid"]
        resp = await _rpc(mcp, refreshed["access_token"], "tools/list", headers=STEERED)
        assert resp.status == 401

    async def test_a_session_from_before_the_description_gets_administer_at_refresh(
        self,
        enable_custom_integrations: None,
        hass: HomeAssistant,
        hass_client: ClientSessionGenerator,
        hass_client_no_auth: ClientSessionGenerator,
    ) -> None:
        """A session approved before this server described itself keeps what it could do.

        The provider's collection of the description is held back while the session
        is approved on the plain screen, as with a provider upgraded under sessions
        that already exist, and let through afterwards.
        """
        from custom_components.oidc_provider import resources as provider_resources
        from custom_components.oidc_provider.resources import find_resource

        collect = provider_resources._async_collect
        held = []

        async def hold(*args) -> None:
            held.append(args)

        with patch.object(provider_resources, "_async_collect", hold):
            await _setup_provider(hass)
            await _setup_server(hass)
        provider = await hass_client()
        mcp = await hass_client_no_auth()
        registration = await _register(provider)
        tokens = await _sign_in(provider, registration, None)
        assert tokens["scope"].split() == ["openid"]
        assert ADMIN_ONLY_TOOL in await _tool_names(mcp, tokens["access_token"])

        for args in held:
            await collect(*args)
        assert find_resource(hass, RESOURCE).domain == DOMAIN

        resp = await _rpc(mcp, tokens["access_token"], "initialize")
        assert resp.status == 401
        assert 'error="invalid_token"' in resp.headers["WWW-Authenticate"]

        refreshed = await _refresh(provider, registration, tokens["refresh_token"])

        assert refreshed["scope"].split() == ["openid", "mcp:admin"]
        assert ADMIN_ONLY_TOOL in await _tool_names(mcp, refreshed["access_token"])

    async def test_read_only_mode_locks_the_levels_above_look_only(
        self,
        enable_custom_integrations: None,
        hass: HomeAssistant,
        hass_client: ClientSessionGenerator,
    ) -> None:
        from custom_components.mcp_server_http_transport.const import CONF_READ_ONLY

        await _setup_provider(hass)
        await _setup_server(hass, **{CONF_READ_ONLY: True})
        provider = await hass_client()
        registration = await _register(provider)
        _, challenge = _pkce_pair()
        resp = await provider.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": registration["client_id"],
                "redirect_uri": REDIRECT_URI,
                "scope": "openid",
                "state": "s",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": RESOURCE,
            },
            headers=FORWARDED,
        )
        request_id = re.search(r"oidc_request_id', '([^']+)'", await resp.text()).group(1)

        resp = await provider.post(
            "/oidc/continue",
            json={
                "request_id": request_id,
                "approve": True,
                "access": {"options": _levels("admin")},
            },
            headers=FORWARDED,
        )

        assert resp.status == 400

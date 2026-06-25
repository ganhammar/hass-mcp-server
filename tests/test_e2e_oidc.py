"""End-to-end test of the OIDC audience contract this server enforces.

The MCP server is an OAuth protected resource (RFC 8707). For the OIDC path it
delegates signature/issuer checking to the paired provider's
``validate_access_token``, but it owns one piece of the contract itself: it
derives ``expected_audience = {issuer}/api/mcp`` from the request and rejects any
token whose ``aud`` is not bound to that resource. That seam breaks silently if
either repo changes how the resource URI is derived or how ``aud`` is shaped,
because each repo's own suite stays green.

This pins the seam by exercising the *real* ``validate_access_token`` (the
function ``http.py`` imports), which lives in the paired ``oidc_provider``
integration. CI vendors that integration into ``custom_components/`` for this
file; when it isn't present (a normal local checkout) ``conftest.py`` stubs the
module instead and these tests skip.

No browser and no issuance flow: a token is signed directly with a provider
keypair seeded into ``hass.data`` and POSTed to ``/api/mcp``.
"""

import time
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.mcp_server_http_transport.const import DOMAIN

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
RESOURCE = f"{ISSUER_BASE}/api/mcp"


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
    iss: str = TOKEN_ISSUER,
    exp_offset: int = 3600,
) -> str:
    """Sign an access token the provider would issue, with controllable claims."""
    now = int(time.time())
    payload = {"sub": "user-1", "iat": now, "exp": now + exp_offset, "iss": iss}
    if aud is not None:
        payload["aud"] = aud
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem, algorithm="RS256")


async def _post_initialize(client: ClientSessionGenerator, token: str):
    return await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        headers={**FORWARDED, "Authorization": f"Bearer {token}"},
    )


async def test_audience_bound_token_is_accepted(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """A token whose aud is this resource passes the real OIDC validation path."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE)

    resp = await _post_initialize(client, token)

    assert resp.status == 200
    assert (await resp.json())["result"]["protocolVersion"] == "2024-11-05"


async def test_token_bound_to_other_resource_is_rejected(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """A valid token bound to a different resource is rejected (the contract)."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud="https://evil.example.com/api/mcp")

    resp = await _post_initialize(client, token)

    assert resp.status == 401
    assert (await resp.json())["error"] == "invalid_token"


async def test_token_without_audience_is_rejected(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """A token with no aud claim is rejected."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=None)

    resp = await _post_initialize(client, token)

    assert resp.status == 401


async def test_expired_token_is_rejected(
    oidc_ready: rsa.RSAPrivateKey, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """An expired token is rejected even though its audience is correct."""
    client = await hass_client_no_auth()
    token = _sign(oidc_ready, aud=RESOURCE, exp_offset=-3600)

    resp = await _post_initialize(client, token)

    assert resp.status == 401

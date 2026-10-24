"""Tests for HTTP transport, auth, and JSON-RPC routing."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, create_autospec, patch

import pytest
from custom_components.oidc_provider.token_validator import get_issuer_from_request

from custom_components.mcp_server_http_transport.const import (
    DOMAIN,
    EVENT_TOOL_CALLED,
    ISSUE_OIDC_AUDIENCE_UNSUPPORTED,
    MCP_HTTP_PATH,
    MCP_PATH,
    OIDC_PROVIDER_MIN_AUDIENCE_VERSION,
    OIDC_PROVIDER_RELEASES_URL,
    RESOURCE_METADATA_PREFIX,
)
from custom_components.mcp_server_http_transport.http import (
    ARGUMENTS_PREVIEW_CHARS,
    REGISTERED_ENDPOINT,
    REGISTERED_ROUTES,
    SECRET_MASK,
    TOOL_NAME_PREVIEW_CHARS,
    Caller,
    MCPEndpointView,
    MCPProtectedResourceMetadataView,
    MCPSubpathProtectedResourceMetadataView,
    ValidatedToken,
    _accepts_expected_audience,
    _get_issuer,
    _get_protected_resource_metadata,
    mask_secrets,
    mcp_path_is_contested,
    register_mcp_views,
    serves_mcp_path,
    truncate_arguments,
)
from custom_components.mcp_server_http_transport.json_utils import dumps
from custom_components.mcp_server_http_transport.tools import TOOLS, call_tool

# What a validated bearer token resolves to; the view then looks its user up.
TOKEN = ValidatedToken(sub="user123", auth_method="native")

_MANIFEST = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN / "manifest.json"


def _manifest_version() -> str:
    """The version hacs.json releases, read independently of the integration."""
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))["version"]


def test_appdaemon_tools_are_registered_by_production_registry():
    """The production tools package imports AppDaemon handlers for registry startup."""
    assert all(
        name in TOOLS
        for name in {
            "list_appdaemon_files",
            "get_appdaemon_file",
            "save_appdaemon_file",
            "delete_appdaemon_file",
            "backup_appdaemon_files",
            "list_appdaemon_backups",
            "cleanup_appdaemon_backups",
            "restore_appdaemon_backup",
        }
    )


@pytest.mark.asyncio
async def test_appdaemon_tool_is_callable_through_production_registry():
    """The production registry dispatches an AppDaemon handler, not just its schema."""
    hass = Mock()
    hass.data = {"mcp_server_http_transport": {"appdaemon_file_access": False}}
    result = await call_tool(hass, "list_appdaemon_files", {})
    assert "disabled" in result["content"][0]["text"]


def test_get_base_url_with_forwarded_headers():
    """Test get_issuer_from_request with X-Forwarded headers (proxy setup)."""
    request = Mock(path=MCP_PATH)
    request.headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "example.com",
    }
    request.url.origin.return_value = "http://localhost:8123"

    result = get_issuer_from_request(request)

    assert result == "https://example.com"
    request.url.origin.assert_not_called()


def test_get_base_url_without_forwarded_headers():
    """Test get_issuer_from_request without X-Forwarded headers (direct connection)."""
    request = Mock(path=MCP_PATH)
    request.headers = {}
    request.url.origin.return_value = "http://192.168.1.100:8123"

    result = get_issuer_from_request(request)

    assert result == "http://192.168.1.100:8123"
    request.url.origin.assert_called_once()


def test_get_base_url_with_partial_forwarded_headers():
    """Test get_issuer_from_request with only one X-Forwarded header (should use fallback)."""
    request = Mock(path=MCP_PATH)
    request.headers = {
        "X-Forwarded-Proto": "https",
    }
    request.url.origin.return_value = "http://localhost:8123"

    result = get_issuer_from_request(request)

    assert result == "http://localhost:8123"
    request.url.origin.assert_called_once()


def test_get_issuer_returns_none_when_oidc_unavailable():
    """Test _get_issuer returns None when oidc_provider import fails."""
    import sys

    request = Mock(path=MCP_PATH)
    # Temporarily remove the mocked oidc module so the import raises ImportError
    saved = sys.modules.pop("custom_components.oidc_provider.token_validator", None)
    saved_parent = sys.modules.pop("custom_components.oidc_provider", None)
    try:
        result = _get_issuer(request)
        assert result is None
    finally:
        if saved is not None:
            sys.modules["custom_components.oidc_provider.token_validator"] = saved
        if saved_parent is not None:
            sys.modules["custom_components.oidc_provider"] = saved_parent


def test_get_protected_resource_metadata():
    """Test _get_protected_resource_metadata returns correct structure."""
    base_url = "https://homeassistant.local"

    metadata = _get_protected_resource_metadata(base_url, MCP_PATH)

    assert metadata["resource"] == f"{base_url}/api/mcp"
    assert metadata["authorization_servers"] == [f"{base_url}/oidc"]
    assert metadata["scopes_supported"] == ["openid"]
    assert metadata["bearer_methods_supported"] == ["header"]
    assert metadata["resource_signing_alg_values_supported"] == ["RS256"]
    assert metadata["resource_documentation"] == f"{base_url}/api/mcp"


class TestMCPProtectedResourceMetadataView:
    """Test the MCP protected resource metadata view at root."""

    async def test_get_returns_metadata(self, routing_hass):
        """Test GET returns protected resource metadata."""
        register_mcp_views(routing_hass, False)
        request = Mock(path=MCP_PATH)
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPProtectedResourceMetadataView(routing_hass)
        response = await view.get(request)

        assert response.status == 200
        assert response.content_type == "application/json"

        body = json.loads(response.body)
        assert body["resource"] == "https://homeassistant.local/api/mcp"
        assert body["authorization_servers"] == ["https://homeassistant.local/oidc"]

    async def test_get_with_forwarded_headers(self, routing_hass):
        """Test GET with X-Forwarded headers."""
        register_mcp_views(routing_hass, False)
        request = Mock(path=MCP_PATH)
        request.headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "example.com",
        }

        view = MCPProtectedResourceMetadataView(routing_hass)
        response = await view.get(request)

        body = json.loads(response.body)
        assert body["resource"] == "https://example.com/api/mcp"

    async def test_get_names_the_dedicated_path_when_api_mcp_is_contested(self, routing_hass):
        """The root path describes an endpoint this integration actually answers.

        A client falls back here when the path-suffixed metadata 404s, so naming
        a contested /api/mcp would send it to this authorization server for a
        token the integration holding that path rejects.
        """
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))
        register_mcp_views(routing_hass, False)
        request = Mock(path=RESOURCE_METADATA_PREFIX)
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPProtectedResourceMetadataView(routing_hass)
        response = await view.get(request)

        assert json.loads(response.body)["resource"] == (
            f"https://homeassistant.local{MCP_HTTP_PATH}"
        )

    async def test_get_returns_404_when_oidc_unavailable(self):
        """Test GET returns 404 when OIDC provider is not installed."""
        request = Mock(path=MCP_PATH)

        view = MCPProtectedResourceMetadataView(Mock())
        with patch(
            "custom_components.mcp_server_http_transport.http._get_issuer",
            return_value=None,
        ):
            response = await view.get(request)

        assert response.status == 404


class TestMCPSubpathProtectedResourceMetadataView:
    """Test the MCP protected resource metadata view with /mcp suffix."""

    async def test_get_returns_metadata(self):
        """Test GET returns protected resource metadata."""
        request = Mock(path=f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}")
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPSubpathProtectedResourceMetadataView(Mock())
        response = await view.get(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["resource"] == "https://homeassistant.local/api/mcp"

    async def test_get_describes_the_endpoint_the_path_names(self):
        """Metadata for the dedicated path describes that path as the resource."""
        request = Mock(path=f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}")
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPSubpathProtectedResourceMetadataView(Mock())
        response = await view.get(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["resource"] == f"https://homeassistant.local{MCP_HTTP_PATH}"
        assert body["resource_documentation"] == f"https://homeassistant.local{MCP_HTTP_PATH}"

    async def test_get_returns_404_when_oidc_unavailable(self):
        """Test GET returns 404 when OIDC provider is not installed."""
        request = Mock(path=MCP_PATH)

        view = MCPSubpathProtectedResourceMetadataView(Mock())
        with patch(
            "custom_components.mcp_server_http_transport.http._get_issuer",
            return_value=None,
        ):
            response = await view.get(request)

        assert response.status == 404


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance with the integration loaded."""
    hass = Mock()
    hass.states = Mock()
    hass.services = Mock()
    hass.data = {DOMAIN: {"entry_id": Mock()}}
    hass.auth.async_get_user = AsyncMock(
        return_value=Mock(id="user123", is_active=True, is_admin=True)
    )
    return hass


@pytest.fixture
def view(mock_hass):
    """Create an MCPEndpointView instance."""
    return MCPEndpointView(mock_hass)


class TestMCPEndpointView:
    """Test the MCP endpoint view: auth, routing, and error handling."""

    async def test_post_without_token_returns_401(self, view):
        """Test POST without Authorization header returns 401."""
        request = Mock(path=MCP_PATH)
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        response = await view.post(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"
        assert "WWW-Authenticate" in response.headers

    async def test_post_with_invalid_token_returns_401(self, view):
        """Test POST with invalid token returns 401."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer invalid_token"}
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=None):
            response = await view.post(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"

    async def test_post_initialize_request(self, view):
        """Test POST with initialize request."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 1})

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["protocolVersion"] == "2024-11-05"
        assert body["result"]["serverInfo"]["name"] == "home-assistant-mcp-server"
        assert body["result"]["serverInfo"]["version"] == _manifest_version()
        assert body["id"] == 1

    async def test_post_initialize_reports_configured_server_name(self, view):
        """A configured name is what lets a client tell two instances apart."""
        view.server_name = "ha-mcp-secundair"
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 2})

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["serverInfo"]["name"] == "ha-mcp-secundair"

    async def test_post_initialize_advertises_capabilities(self, view):
        """Test POST initialize advertises resources and prompts capabilities."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 21})

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        body = json.loads(response.body)
        capabilities = body["result"]["capabilities"]
        assert "tools" in capabilities
        assert "resources" in capabilities
        assert "prompts" in capabilities

    async def test_post_tools_list_request(self, view):
        """Test POST with tools/list request."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/list", "id": 2})

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert "tools" in body["result"]
        assert len(body["result"]["tools"]) == 89
        tool_names = [t["name"] for t in body["result"]["tools"]]
        assert "get_state" in tool_names
        assert "call_service" in tool_names
        assert "knx_recent_telegrams" in tool_names
        assert "knx_create_entity" in tool_names
        assert "list_entities" in tool_names
        assert "get_error_log" in tool_names
        assert "restart_ha" in tool_names
        assert "get_system_status" in tool_names
        assert "get_statistics" in tool_names
        assert "get_camera_image" in tool_names
        assert "get_image_file" in tool_names
        assert "list_labels" in tool_names
        assert "list_categories" in tool_names
        assert "set_category" in tool_names
        assert "set_icon" in tool_names
        assert "batch_get_state" in tool_names
        assert "list_traces" in tool_names
        assert "get_trace" in tool_names
        assert "list_statistic_ids" in tool_names
        assert "validate_statistics" in tool_names
        assert "adjust_statistics" in tool_names
        assert "clear_statistics" in tool_names
        assert "list_appdaemon_files" in tool_names
        assert "get_appdaemon_file" in tool_names
        assert "save_appdaemon_file" in tool_names
        assert "delete_appdaemon_file" in tool_names
        assert "backup_appdaemon_files" in tool_names
        assert "list_appdaemon_backups" in tool_names
        assert "restore_appdaemon_backup" in tool_names
        assert "cleanup_appdaemon_backups" in tool_names

    async def test_post_unknown_method_returns_error(self, view):
        """Test POST with unknown method returns error."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={"jsonrpc": "2.0", "method": "unknown_method", "id": 9}
        )

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "error" in body
        assert body["error"]["code"] == -32601
        assert "Method not found" in body["error"]["message"]

    async def test_post_notification_returns_202(self, view):
        """Test POST with notification (no id) returns 202."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "some_notification"})

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 202

    async def test_get_without_token_returns_401_with_challenge(self, view):
        """Test GET without a token returns 401 carrying the metadata pointer."""
        request = Mock(path=MCP_PATH)
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        with patch(
            "custom_components.mcp_server_http_transport.http._get_issuer",
            return_value="https://homeassistant.local:8123",
        ):
            response = await view.get(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"
        assert (
            'resource_metadata="https://homeassistant.local:8123'
            '/.well-known/oauth-protected-resource/api/mcp"' in response.headers["WWW-Authenticate"]
        )

    async def test_challenge_points_at_the_metadata_for_the_path_used(self, view):
        """A probe on the dedicated path is pointed at that path's metadata."""
        request = Mock(path=MCP_HTTP_PATH)
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        with patch(
            "custom_components.mcp_server_http_transport.http._get_issuer",
            return_value="https://homeassistant.local:8123",
        ):
            response = await view.get(request)

        assert response.status == 401
        assert (
            f'resource_metadata="https://homeassistant.local:8123'
            f'{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}"' in response.headers["WWW-Authenticate"]
        )

    async def test_get_with_valid_token_returns_405(self, view):
        """Test GET with a valid token is Method Not Allowed; there is no SSE stream."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.get(request)

        assert response.status == 405
        assert response.headers["Allow"] == "OPTIONS, POST"
        assert "WWW-Authenticate" not in response.headers

    async def test_get_returns_503_when_unloaded(self):
        """Test GET is gated on the integration being loaded, like POST."""
        hass = Mock()
        hass.data = {}
        view = MCPEndpointView(hass)

        request = Mock(path=MCP_PATH)
        request.headers = {}

        response = await view.get(request)

        assert response.status == 503
        assert json.loads(response.body)["error"] == "service_unavailable"

    async def test_validate_token_without_bearer_prefix(self, view):
        """Test _validate_token without Bearer prefix returns None."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "invalid_format"}

        result = await view._validate_token(request)

        assert result is None

    async def test_post_tools_call_unknown_tool(self, view):
        """Test POST with tools/call for unknown tool."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "unknown_tool", "arguments": {}},
                "id": 10,
            }
        )
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["error"]["code"] == -32602
        assert "Unknown tool" in body["error"]["message"]


class TestToolErrorHandling:
    """Regression for #82: a failing tool call still answers with a usable body.

    A client working from a cached copy of an older tool list keeps sending the
    argument shape it last saw, so the transport has to name what is wrong. A
    JSON-RPC error is a complete response and ships at 200, because under a
    non-2xx an intermediary proxy substitutes its own error page and the client
    never sees the body.
    """

    async def _call(self, view, name, arguments, msg_id=1):
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
                "id": msg_id,
            }
        )
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        return response, json.loads(response.body)

    async def test_missing_required_argument_returns_invalid_params(self, view):
        """A stale-schema call names the property it left out."""
        response, body = await self._call(
            view,
            "get_statistics",
            {
                "statistic_ids": ["sensor.example"],
                "start_time": "2026-08-17T00:00:00",
                "period": "hour",
            },
        )

        assert response.status == 200
        assert body["error"]["code"] == -32602
        assert "entity_id" in body["error"]["message"]
        assert body["id"] == 1

    async def test_missing_arguments_lists_every_required_property(self, view):
        response, body = await self._call(view, "get_statistics", {})

        assert body["error"]["code"] == -32602
        assert "entity_id" in body["error"]["message"]
        assert "start_time" in body["error"]["message"]

    async def test_explicit_null_counts_as_a_missing_property(self, view):
        """Drift sends a null as readily as it drops the key; both are invalid."""
        response, body = await self._call(
            view,
            "get_statistics",
            {"entity_id": None, "start_time": None},
        )

        assert response.status == 200
        assert body["error"]["code"] == -32602
        assert "entity_id" in body["error"]["message"]
        assert "start_time" in body["error"]["message"]

    async def test_non_dict_arguments_are_rejected(self, view):
        response, body = await self._call(view, "get_statistics", ["sensor.example"])

        assert response.status == 200
        assert body["error"]["code"] == -32602
        assert "must be a JSON object" in body["error"]["message"]

    async def test_non_dict_arguments_never_reach_a_tool_with_no_required_properties(self, view):
        """restore_config_backup requires nothing, so a coerced {} would restore a backup."""
        called = False

        async def handler(hass, arguments):
            nonlocal called
            called = True
            return {"content": []}

        entry = TOOLS["restore_config_backup"]
        with patch.dict(
            TOOLS,
            {"restore_config_backup": {"schema": entry["schema"], "handler": handler}},
        ):
            response, body = await self._call(view, "restore_config_backup", "2026-01-01")

        assert called is False
        assert body["error"]["code"] == -32602
        assert "must be a JSON object" in body["error"]["message"]

    async def test_missing_confirm_reports_why_it_is_required(self, view):
        """The -32602 carries the schema description, so the safety reason survives."""
        response, body = await self._call(
            view, "clear_statistics", {"statistic_ids": ["sensor.energy"]}
        )

        assert body["error"]["code"] == -32602
        assert "confirm" in body["error"]["message"]
        assert "irreversible" in body["error"]["message"]

    async def test_unknown_tool_is_a_caller_error(self, view):
        response, body = await self._call(view, "get_statistcs", {})

        assert body["error"]["code"] == -32602
        assert "Unknown tool" in body["error"]["message"]

    async def test_unhandled_handler_exception_becomes_is_error_result(self, view):
        """An exception the handler does not catch is reported inside the result."""

        async def boom(hass, arguments):
            raise RuntimeError("recorder exploded")

        with patch.dict(
            TOOLS,
            {"get_statistics": {"schema": TOOLS["get_statistics"]["schema"], "handler": boom}},
        ):
            response, body = await self._call(
                view,
                "get_statistics",
                {"entity_id": "sensor.example", "start_time": "2026-08-17T00:00:00"},
            )

        assert response.status == 200
        assert "error" not in body
        assert body["result"]["isError"] is True
        assert "recorder exploded" in body["result"]["content"][0]["text"]
        assert body["id"] == 1

    async def test_unparseable_body_returns_parse_error(self, view):
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(side_effect=ValueError("not json"))
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert response.status == 400
        body = json.loads(response.body)
        assert body["error"]["code"] == -32700


class TestIntegrationDisabledGate:
    """Regression for #37: views return 503 when the integration is unloaded.

    HA's HTTP stack keeps registered views alive across config entry unloads,
    so we gate on `hass.data[DOMAIN]` — which async_unload_entry clears.
    """

    async def test_endpoint_view_returns_503_when_domain_missing(self):
        hass = Mock()
        hass.data = {}
        view = MCPEndpointView(hass)

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}

        response = await view.post(request)

        assert response.status == 503
        body = json.loads(response.body)
        assert body["error"] == "service_unavailable"

    async def test_endpoint_view_returns_503_when_domain_cleared(self):
        hass = Mock()
        hass.data = {"mcp_server_http_transport": {}}  # matches async_unload_entry.clear()
        view = MCPEndpointView(hass)

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}

        response = await view.post(request)

        assert response.status == 503

    async def test_metadata_view_returns_503_when_unloaded(self):
        hass = Mock()
        hass.data = {}
        view = MCPProtectedResourceMetadataView(hass)

        response = await view.get(Mock())

        assert response.status == 503

    async def test_subpath_metadata_view_returns_503_when_unloaded(self):
        hass = Mock()
        hass.data = {}
        view = MCPSubpathProtectedResourceMetadataView(hass)

        response = await view.get(Mock())

        assert response.status == 503

    async def test_endpoint_view_serves_when_domain_populated(self):
        hass = Mock()
        hass.data = {"mcp_server_http_transport": {"server": Mock()}}
        view = MCPEndpointView(hass)

        request = Mock(path=MCP_PATH)
        request.headers = {}  # no token → 401, not 503

        response = await view.post(request)

        assert response.status == 401


class TestNativeAuth:
    """Test native HA authentication (Long-Lived Access Tokens)."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance with auth."""
        hass = Mock()
        hass.states = Mock()
        hass.services = Mock()
        hass.auth = Mock()
        hass.auth.async_validate_access_token = Mock(return_value=None)
        hass.auth.async_get_user = AsyncMock(return_value=Mock(is_active=True, is_admin=True))
        return hass

    @pytest.fixture
    def view(self, mock_hass):
        """Create an MCPEndpointView with native auth enabled."""
        return MCPEndpointView(mock_hass, native_auth_enabled=True)

    @pytest.fixture
    def view_disabled(self, mock_hass):
        """Create an MCPEndpointView with native auth disabled."""
        return MCPEndpointView(mock_hass, native_auth_enabled=False)

    async def test_llat_validates_when_enabled(self, view, mock_hass):
        """Test that a valid LLAT is accepted when native auth is enabled."""
        mock_refresh_token = Mock()
        mock_refresh_token.user.id = "user_abc"
        mock_hass.auth.async_validate_access_token.return_value = mock_refresh_token

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_llat"}

        result = await view._validate_token(request)

        assert result == ValidatedToken(sub="user_abc", auth_method="native")
        mock_hass.auth.async_validate_access_token.assert_called_once_with("valid_llat")

    async def test_llat_rejected_when_disabled(self, view_disabled, mock_hass):
        """Test that LLAT is not tried when native auth is disabled."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer some_token"}

        result = await view_disabled._validate_token(request)

        assert result is None
        mock_hass.auth.async_validate_access_token.assert_not_called()

    async def test_invalid_llat_returns_none(self, view, mock_hass):
        """Test that an invalid LLAT returns None."""
        mock_hass.auth.async_validate_access_token.return_value = None

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer bad_token"}

        result = await view._validate_token(request)

        assert result is None

    async def test_oidc_tried_before_llat(self, view, mock_hass):
        """Test that OIDC validation is attempted before LLAT."""
        import sys

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer oidc_token"}

        mock_validator = sys.modules["custom_components.oidc_provider.token_validator"]
        original = mock_validator.validate_access_token.return_value
        mock_validator.validate_access_token.return_value = {"sub": "oidc_user"}
        try:
            result = await view._validate_token(request)
        finally:
            mock_validator.validate_access_token.return_value = original

        assert result == ValidatedToken(sub="oidc_user", auth_method="oidc")
        mock_hass.auth.async_validate_access_token.assert_not_called()

    async def test_llat_fallback_after_oidc_fails(self, view, mock_hass):
        """Test LLAT is tried as fallback when OIDC validation returns None."""
        mock_refresh_token = Mock()
        mock_refresh_token.user.id = "ha_user"
        mock_hass.auth.async_validate_access_token.return_value = mock_refresh_token

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer llat_token"}

        # OIDC will fail (ImportError from conftest mock returning None)
        result = await view._validate_token(request)

        assert result == ValidatedToken(sub="ha_user", auth_method="native")

    async def test_validate_token_import_error_falls_through(self, view, mock_hass):
        """Test _validate_token handles ImportError from OIDC and falls through to LLAT."""
        import sys

        mock_refresh_token = Mock()
        mock_refresh_token.user.id = "fallback_user"
        mock_hass.auth.async_validate_access_token.return_value = mock_refresh_token

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer some_token"}

        saved = sys.modules.pop("custom_components.oidc_provider.token_validator", None)
        saved_parent = sys.modules.pop("custom_components.oidc_provider", None)
        try:
            result = await view._validate_token(request)
        finally:
            if saved is not None:
                sys.modules["custom_components.oidc_provider.token_validator"] = saved
            if saved_parent is not None:
                sys.modules["custom_components.oidc_provider"] = saved_parent

        assert result == ValidatedToken(sub="fallback_user", auth_method="native")

    async def test_401_without_oidc_metadata_when_oidc_unavailable(self, view, mock_hass):
        """Test 401 response uses plain Bearer when OIDC is not available."""
        mock_hass.auth.async_validate_access_token.return_value = None

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer bad_token"}
        request.url.origin.return_value = "http://localhost:8123"

        with patch(
            "custom_components.mcp_server_http_transport.http._get_issuer",
            return_value=None,
        ):
            response = await view.post(request)

        assert response.status == 401
        assert 'realm="Home Assistant MCP Server"' in response.headers["WWW-Authenticate"]
        assert "resource_metadata" not in response.headers["WWW-Authenticate"]

    async def test_native_auth_full_request(self, view, mock_hass):
        """Test a full request with native auth from token to response."""
        mock_refresh_token = Mock()
        mock_refresh_token.user.id = "user_xyz"
        mock_hass.auth.async_validate_access_token.return_value = mock_refresh_token

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer my_llat"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 1})

        response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["protocolVersion"] == "2024-11-05"


class TestOidcAudienceBinding:
    """Test that OIDC validation binds the token audience to this resource."""

    @pytest.fixture
    def view(self):
        """Create an MCPEndpointView (native auth disabled)."""
        hass = Mock()
        return MCPEndpointView(hass, native_auth_enabled=False)

    async def test_passes_expected_audience_to_validator(self, view):
        """_validate_token derives the resource URI and passes it as audience."""
        import sys

        request = Mock(path=MCP_PATH)
        request.headers = {
            "Authorization": "Bearer t",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ha.example.com",
        }

        mv = sys.modules["custom_components.oidc_provider.token_validator"]
        mv.validate_access_token.reset_mock()
        mv.validate_access_token.return_value = {"sub": "u"}
        try:
            result = await view._validate_token(request)
        finally:
            mv.validate_access_token.return_value = None

        assert result == ValidatedToken(sub="u", auth_method="oidc")
        _, kwargs = mv.validate_access_token.call_args
        assert kwargs.get("expected_audience") == "https://ha.example.com/api/mcp"

    async def test_audience_follows_the_endpoint_the_client_called(self, view):
        """A token for the dedicated path is bound to that path, not /api/mcp."""
        import sys

        request = Mock(path=MCP_HTTP_PATH)
        request.headers = {
            "Authorization": "Bearer t",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ha.example.com",
        }

        mv = sys.modules["custom_components.oidc_provider.token_validator"]
        mv.validate_access_token.reset_mock()
        mv.validate_access_token.return_value = {"sub": "u"}
        try:
            await view._validate_token(request)
        finally:
            mv.validate_access_token.return_value = None

        _, kwargs = mv.validate_access_token.call_args
        assert kwargs.get("expected_audience") == f"https://ha.example.com{MCP_HTTP_PATH}"

    @patch("custom_components.mcp_server_http_transport.http.ir")
    async def test_falls_back_to_legacy_signature_for_an_old_provider(self, mock_ir, view):
        """An older OIDC provider whose validator lacks expected_audience still works."""
        import sys

        request = Mock(path=MCP_PATH)
        request.headers = {
            "Authorization": "Bearer t",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ha.example.com",
        }

        mv = sys.modules["custom_components.oidc_provider.token_validator"]
        original = mv.validate_access_token
        mv.validate_access_token = _legacy_validator()
        try:
            result = await view._validate_token(request)
        finally:
            mv.validate_access_token = original

        assert result == ValidatedToken(sub="legacy", auth_method="oidc")

    async def test_a_type_error_inside_a_modern_validator_is_not_a_fallback(self, view):
        """A provider bug surfaces instead of re-validating without the audience check."""
        import sys

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer t"}

        mv = sys.modules["custom_components.oidc_provider.token_validator"]
        mv.validate_access_token.reset_mock()
        mv.validate_access_token.side_effect = TypeError("bug inside the validator")
        try:
            with pytest.raises(TypeError):
                await view._validate_token(request)
        finally:
            mv.validate_access_token.side_effect = None
        assert mv.validate_access_token.call_count == 1


def _legacy_signature(hass, token, expected_issuer):
    """The validate_access_token signature of hass-oidc-server before 1.5.0."""


def _legacy_validator(payload: dict | None = None) -> Mock:
    """A validator mock whose signature predates expected_audience."""
    return create_autospec(
        _legacy_signature, return_value={"sub": "legacy"} if payload is None else payload
    )


class TestAcceptsExpectedAudience:
    """The provider's support for audience binding is read off its signature."""

    def test_legacy_signature_is_not_supported(self):
        assert _accepts_expected_audience(_legacy_signature) is False

    def test_keyword_parameter_is_supported(self):
        def modern(hass, token, expected_issuer, expected_audience=None): ...

        assert _accepts_expected_audience(modern) is True

    def test_var_keyword_is_supported(self):
        def forwarding(*args, **kwargs): ...

        assert _accepts_expected_audience(forwarding) is True

    def test_uninspectable_callable_is_assumed_supported(self):
        assert _accepts_expected_audience(object()) is True


class TestOidcAudienceFallbackReport:
    """Test the warning and repair raised when audience validation is unavailable."""

    @pytest.fixture
    def view(self):
        """Create an MCPEndpointView (native auth disabled)."""
        return MCPEndpointView(Mock(), native_auth_enabled=False)

    @pytest.fixture
    def validator(self):
        """The stubbed OIDC validator module, restored to its default after the test."""
        import sys

        mv = sys.modules["custom_components.oidc_provider.token_validator"]
        original = mv.validate_access_token
        original.reset_mock()
        yield mv
        mv.validate_access_token = original
        original.side_effect = None
        original.return_value = None

    @staticmethod
    def _request():
        request = Mock(path=MCP_PATH)
        request.headers = {
            "Authorization": "Bearer t",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ha.example.com",
        }
        return request

    @staticmethod
    def _fallback_warnings(caplog):
        return [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "audience" in r.getMessage()
        ]

    @patch("custom_components.mcp_server_http_transport.http.ir")
    async def test_warns_once_across_requests(self, mock_ir, view, validator, caplog):
        """Two requests through the fallback log the warning a single time."""
        validator.validate_access_token = _legacy_validator()

        with caplog.at_level(logging.DEBUG):
            first = await view._validate_token(self._request())
            second = await view._validate_token(self._request())

        assert first == second == ValidatedToken(sub="legacy", auth_method="oidc")
        warnings = self._fallback_warnings(caplog)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "without checking the audience" in message
        assert OIDC_PROVIDER_MIN_AUDIENCE_VERSION in message
        assert mock_ir.async_create_issue.call_count == 1

    @patch("custom_components.mcp_server_http_transport.http.ir")
    async def test_each_view_reports_on_its_own(self, mock_ir, validator, caplog):
        """The once-only flag belongs to the view instance."""
        validator.validate_access_token = _legacy_validator()

        with caplog.at_level(logging.DEBUG):
            await MCPEndpointView(Mock())._validate_token(self._request())
            await MCPEndpointView(Mock())._validate_token(self._request())

        assert len(self._fallback_warnings(caplog)) == 2

    @patch("custom_components.mcp_server_http_transport.http.ir")
    async def test_raises_repair_issue(self, mock_ir, view, validator):
        """The fallback raises a non-fixable warning repair naming the minimum version."""
        validator.validate_access_token = _legacy_validator()

        await view._validate_token(self._request())

        mock_ir.async_create_issue.assert_called_once()
        args, kwargs = mock_ir.async_create_issue.call_args
        assert args == (view.hass, DOMAIN, ISSUE_OIDC_AUDIENCE_UNSUPPORTED)
        assert kwargs["translation_key"] == ISSUE_OIDC_AUDIENCE_UNSUPPORTED
        assert kwargs["translation_placeholders"] == {
            "minimum_version": OIDC_PROVIDER_MIN_AUDIENCE_VERSION
        }
        assert kwargs["severity"] is mock_ir.IssueSeverity.WARNING
        assert kwargs["is_fixable"] is False
        assert kwargs["learn_more_url"] == OIDC_PROVIDER_RELEASES_URL

    @patch("custom_components.mcp_server_http_transport.http.ir")
    async def test_silent_when_audience_check_succeeds(self, mock_ir, view, validator, caplog):
        """A provider that supports the audience raises neither warning nor repair."""
        validator.validate_access_token.return_value = {"sub": "u"}

        with caplog.at_level(logging.DEBUG):
            result = await view._validate_token(self._request())

        assert result == ValidatedToken(sub="u", auth_method="oidc")
        assert self._fallback_warnings(caplog) == []
        mock_ir.async_create_issue.assert_not_called()

    @patch("custom_components.mcp_server_http_transport.http.ir")
    async def test_silent_when_token_is_merely_invalid(self, mock_ir, view, validator, caplog):
        """A token the audience-aware provider rejects is not a fallback."""
        validator.validate_access_token.return_value = None

        with caplog.at_level(logging.DEBUG):
            result = await view._validate_token(self._request())

        assert result is None
        assert self._fallback_warnings(caplog) == []
        mock_ir.async_create_issue.assert_not_called()


class _FakeRoute:
    """A registered route, carrying only what the conflict check reads."""

    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.resource = SimpleNamespace(canonical=path)


class _FakeRouter:
    """A router that records routes the way HomeAssistantView.register adds them."""

    def __init__(self) -> None:
        self._routes: list[_FakeRoute] = []

    def routes(self) -> list[_FakeRoute]:
        return list(self._routes)

    def add_view(self, view) -> None:
        for method in ("get", "post", "delete", "put", "patch", "head", "options"):
            if not getattr(view, method, None):
                continue
            for url in [view.url, *view.extra_urls]:
                self._routes.append(_FakeRoute(method.upper(), url))


@pytest.fixture
def routing_hass():
    """A hass whose HTTP router records what gets registered on it."""
    hass = Mock()
    hass.data = {DOMAIN: {"entry_id": Mock()}}
    router = _FakeRouter()
    hass.http.app.router = router
    hass.http.register_view = router.add_view
    return hass


def _paths(router: _FakeRouter, method: str = "POST") -> list[str]:
    """Return the paths serving a method, in registration order."""
    return [route.resource.canonical for route in router.routes() if route.method == method]


class TestRegisterMCPViews:
    """Test which paths the integration claims, and how it spots a conflict."""

    def test_claims_both_paths_when_api_mcp_is_free(self, routing_hass):
        """With nothing else on /api/mcp, the endpoint serves both paths."""
        register_mcp_views(routing_hass, False)

        assert _paths(routing_hass.http.app.router) == [MCP_PATH, MCP_HTTP_PATH]

    def test_registers_the_metadata_views(self, routing_hass):
        """Both RFC 9728 metadata paths are served alongside the endpoint."""
        register_mcp_views(routing_hass, False)

        assert _paths(routing_hass.http.app.router, "GET") == [
            RESOURCE_METADATA_PREFIX,
            f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}",
            f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}",
            MCP_PATH,
            MCP_HTTP_PATH,
        ]

    def test_stays_off_api_mcp_when_another_integration_serves_it(self, routing_hass):
        """A path already answering POST elsewhere is left alone entirely.

        Home Assistant's built-in mcp_server registers no GET on /api/mcp, so
        taking half the path would answer a client's discovery probe here while
        its actual traffic went to the other integration.
        """
        router = routing_hass.http.app.router
        router._routes.append(_FakeRoute("POST", MCP_PATH))

        register_mcp_views(routing_hass, False)

        assert _paths(router) == [MCP_PATH, MCP_HTTP_PATH]  # the foreign one, then ours
        assert MCP_PATH not in _paths(router, "GET")

    def test_metadata_for_a_contested_path_is_left_alone_too(self, routing_hass):
        """The RFC 9728 metadata follows the endpoint off a contested path.

        Nothing else serves that metadata, so answering there would hand a
        client this integration's authorization server for a token the
        integration actually holding /api/mcp will reject.
        """
        router = routing_hass.http.app.router
        router._routes.append(_FakeRoute("POST", MCP_PATH))

        register_mcp_views(routing_hass, False)

        assert f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}" not in _paths(router, "GET")
        assert f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}" in _paths(router, "GET")

    def test_a_wildcard_route_counts_as_a_competitor(self, routing_hass):
        """A route registered for every method answers POST as well."""
        routing_hass.http.app.router._routes.append(_FakeRoute("*", MCP_PATH))

        register_mcp_views(routing_hass, False)

        assert mcp_path_is_contested(routing_hass) is True
        assert MCP_PATH not in _paths(routing_hass.http.app.router, "GET")

    def test_own_routes_are_not_mistaken_for_a_conflict(self, routing_hass):
        """A reload does not make this integration its own competitor."""
        register_mcp_views(routing_hass, False)
        register_mcp_views(routing_hass, False)

        assert mcp_path_is_contested(routing_hass) is False

    def test_a_reload_updates_the_view_the_router_holds(self, routing_hass):
        """Registering again would sit behind the first view and never be reached."""
        register_mcp_views(routing_hass, False)
        routes_after_first_load = routing_hass.http.app.router.routes()
        endpoint = routing_hass.data[REGISTERED_ENDPOINT]

        register_mcp_views(routing_hass, True, server_name="ha-mcp-secundair")

        assert routing_hass.http.app.router.routes() == routes_after_first_load
        assert endpoint.native_auth_enabled is True
        assert endpoint.server_name == "ha-mcp-secundair"

    def test_serves_mcp_path_reports_who_answers(self, routing_hass):
        """Only the first route on the path answers, whoever registered it."""
        register_mcp_views(routing_hass, False)

        assert serves_mcp_path(routing_hass) is True

        # A competitor arriving later queues behind the route already bound.
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))

        assert mcp_path_is_contested(routing_hass) is True
        assert serves_mcp_path(routing_hass) is True

    def test_serves_mcp_path_is_false_when_another_integration_got_there_first(self, routing_hass):
        """A path claimed before setup is answered by its holder, not this one."""
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))

        register_mcp_views(routing_hass, False)

        assert serves_mcp_path(routing_hass) is False

    def test_conflict_is_seen_when_the_other_integration_arrives_later(self, routing_hass):
        """A path claimed after setup still reports as contested."""
        register_mcp_views(routing_hass, False)
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))

        assert mcp_path_is_contested(routing_hass) is True

    def test_registered_routes_survive_an_unload(self, routing_hass):
        """The tracked routes live outside hass.data[DOMAIN], which unload clears."""
        register_mcp_views(routing_hass, False)
        routing_hass.data[DOMAIN].clear()

        assert routing_hass.data[REGISTERED_ROUTES]
        assert mcp_path_is_contested(routing_hass) is False


_HTTP_LOGGER = "custom_components.mcp_server_http_transport.http"


def _user(*, is_admin: bool = True, is_active: bool = True) -> Mock:
    """A Home Assistant user as hass.auth.async_get_user returns it."""
    return Mock(id="user123", is_admin=is_admin, is_active=is_active)


def _jsonrpc_request(method: str, params: dict | None = None) -> Mock:
    """A POST carrying one JSON-RPC request and a bearer token."""
    body = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params is not None:
        body["params"] = params
    request = Mock(path=MCP_PATH)
    request.headers = {"Authorization": "Bearer secret-token-value"}
    request.json = AsyncMock(return_value=body)
    return request


class TestAdminGate:
    """Only Home Assistant administrators reach the tools unless the entry says otherwise.

    Home Assistant gates config changes, restarts, templates and the error log on
    administrator rights, and the tools do all of those, so a token from a
    non-admin user would otherwise grant more than Home Assistant itself does.
    """

    @pytest.fixture
    def gate_hass(self):
        """A loaded integration whose token resolves to a user the test picks."""
        gate_hass = Mock()
        gate_hass.data = {DOMAIN: {"entry_id": Mock()}}
        gate_hass.auth.async_get_user = AsyncMock(return_value=_user())
        return gate_hass

    async def _post(self, view, request=None):
        with patch.object(view, "_validate_token", return_value=TOKEN):
            return await view.post(request or _jsonrpc_request("initialize"))

    async def test_admin_is_served(self, gate_hass):
        view = MCPEndpointView(gate_hass)

        response = await self._post(view)

        assert response.status == 200
        gate_hass.auth.async_get_user.assert_awaited_once_with("user123")

    async def test_non_admin_is_refused_by_default(self, gate_hass, caplog):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass)

        with caplog.at_level(logging.WARNING, logger=_HTTP_LOGGER):
            response = await self._post(view)

        assert response.status == 403
        assert "user123" in caplog.text
        assert "not an administrator" in caplog.text
        assert "secret-token-value" not in caplog.text

    async def test_a_repeated_refusal_warns_once(self, gate_hass, caplog):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass)

        with caplog.at_level(logging.DEBUG, logger=_HTTP_LOGGER):
            assert (await self._post(view)).status == 403
            assert (await self._post(view)).status == 403

        refusals = [r for r in caplog.records if "Refused MCP request" in r.getMessage()]
        assert [r.levelno for r in refusals] == [logging.WARNING, logging.DEBUG]

    async def test_non_admin_is_served_when_the_requirement_is_off(self, gate_hass):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass, require_admin=False)

        response = await self._post(view)

        assert response.status == 200

    async def test_unknown_user_is_refused_even_without_the_requirement(self, gate_hass, caplog):
        gate_hass.auth.async_get_user.return_value = None
        view = MCPEndpointView(gate_hass, require_admin=False)

        with caplog.at_level(logging.WARNING, logger=_HTTP_LOGGER):
            response = await self._post(view)

        assert response.status == 403
        assert "no such user" in caplog.text

    async def test_inactive_user_is_refused_even_without_the_requirement(self, gate_hass, caplog):
        gate_hass.auth.async_get_user.return_value = _user(is_active=False)
        view = MCPEndpointView(gate_hass, require_admin=False)

        with caplog.at_level(logging.WARNING, logger=_HTTP_LOGGER):
            response = await self._post(view)

        assert response.status == 403
        assert "deactivated" in caplog.text

    async def test_refusal_follows_rfc_6750_insufficient_scope(self, gate_hass):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass)

        response = await self._post(view)

        body = json.loads(response.body)
        assert body["error"] == "insufficient_scope"
        assert "administrator" in body["error_description"]
        assert response.headers["WWW-Authenticate"] == (
            'Bearer error="insufficient_scope",' f' error_description="{body["error_description"]}"'
        )

    async def test_refused_call_never_reaches_the_tool(self, gate_hass):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass)
        handler = AsyncMock(return_value={"content": []})
        entry = TOOLS["restart_ha"]
        request = _jsonrpc_request("tools/call", {"name": "restart_ha", "arguments": {}})

        with patch.dict(TOOLS, {"restart_ha": {"schema": entry["schema"], "handler": handler}}):
            response = await self._post(view, request)

        assert response.status == 403
        handler.assert_not_awaited()

    async def test_an_invalid_token_is_still_a_401(self, gate_hass):
        view = MCPEndpointView(gate_hass)
        request = _jsonrpc_request("initialize")
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=None):
            response = await view.post(request)

        assert response.status == 401
        gate_hass.auth.async_get_user.assert_not_awaited()

    async def test_get_is_gated_the_same_way(self, gate_hass):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass)
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.get(request)

        assert response.status == 403

    async def test_a_native_token_resolves_to_its_own_user(self, gate_hass):
        refresh_token = Mock()
        refresh_token.user.id = "llat_user"
        gate_hass.auth.async_validate_access_token = Mock(return_value=refresh_token)
        view = MCPEndpointView(gate_hass, native_auth_enabled=True)

        response = await view.post(_jsonrpc_request("initialize"))

        assert response.status == 200
        gate_hass.auth.async_get_user.assert_awaited_once_with("llat_user")

    def test_requirement_is_on_unless_configured_off(self, routing_hass):
        register_mcp_views(routing_hass, False)

        assert routing_hass.data[REGISTERED_ENDPOINT].require_admin is True

    def test_a_reload_refreshes_the_requirement_on_the_registered_view(self, routing_hass):
        register_mcp_views(routing_hass, False)
        endpoint = routing_hass.data[REGISTERED_ENDPOINT]

        register_mcp_views(routing_hass, False, require_admin=False)

        assert routing_hass.data[REGISTERED_ENDPOINT] is endpoint
        assert endpoint.require_admin is False


_PROCESS_WRONG_LOGIN = f"{_HTTP_LOGGER}.process_wrong_login"
_PROCESS_SUCCESS_LOGIN = f"{_HTTP_LOGGER}.process_success_login"


class TestFailedLoginCounting:
    """A rejected bearer token counts toward Home Assistant's IP ban protection.

    The view sets requires_auth = False and validates tokens itself, so Home
    Assistant's own login attempt tracking never sees a failure here. Only a
    request that presented a token and had it rejected counts: clients probe
    without a token to discover how to authenticate, and a valid token for a user
    the gate refuses is not token guessing.
    """

    @pytest.fixture
    def gate_hass(self):
        gate_hass = Mock()
        gate_hass.data = {DOMAIN: {"entry_id": Mock()}}
        gate_hass.auth.async_get_user = AsyncMock(return_value=_user())
        return gate_hass

    @staticmethod
    def _request(*, authorization: str | None = "Bearer wrong-token") -> Mock:
        request = _jsonrpc_request("initialize")
        request.headers = {} if authorization is None else {"Authorization": authorization}
        request.url.origin.return_value = "https://homeassistant.local"
        return request

    async def test_an_authorized_request_clears_the_count(self, gate_hass):
        view = MCPEndpointView(gate_hass, native_auth_enabled=True)
        request = self._request(authorization="Bearer valid-token")

        with (
            patch.object(view, "_validate_token", return_value=ValidatedToken("u1", "native")),
            patch(_PROCESS_SUCCESS_LOGIN) as process_success_login,
        ):
            response = await view.post(request)

        assert response.status == 200
        process_success_login.assert_called_once_with(request)

    async def test_a_refused_user_does_not_clear_the_count(self, gate_hass):
        gate_hass.auth.async_get_user = AsyncMock(return_value=_user(is_admin=False))
        view = MCPEndpointView(gate_hass, native_auth_enabled=True)
        request = self._request(authorization="Bearer valid-token")

        with (
            patch.object(view, "_validate_token", return_value=ValidatedToken("u1", "native")),
            patch(_PROCESS_SUCCESS_LOGIN) as process_success_login,
        ):
            response = await view.post(request)

        assert response.status == 403
        process_success_login.assert_not_called()

    async def test_a_failing_reset_does_not_fail_the_request(self, gate_hass):
        view = MCPEndpointView(gate_hass, native_auth_enabled=True)
        request = self._request(authorization="Bearer valid-token")

        with (
            patch.object(view, "_validate_token", return_value=ValidatedToken("u1", "native")),
            patch(_PROCESS_SUCCESS_LOGIN, side_effect=RuntimeError("no http app")),
        ):
            response = await view.post(request)

        assert response.status == 200

    async def test_an_invalid_bearer_token_is_counted_once(self, gate_hass):
        view = MCPEndpointView(gate_hass)
        request = self._request()

        with (
            patch.object(view, "_validate_token", return_value=None),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            response = await view.post(request)

        assert response.status == 401
        process_wrong_login.assert_awaited_once_with(request)

    async def test_a_get_with_an_invalid_bearer_token_is_counted(self, gate_hass):
        view = MCPEndpointView(gate_hass)
        request = self._request()

        with (
            patch.object(view, "_validate_token", return_value=None),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            response = await view.get(request)

        assert response.status == 401
        process_wrong_login.assert_awaited_once_with(request)

    async def test_a_request_without_an_authorization_header_is_not_counted(self, gate_hass):
        view = MCPEndpointView(gate_hass)

        with (
            patch.object(view, "_validate_token", return_value=None),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            response = await view.post(self._request(authorization=None))

        assert response.status == 401
        process_wrong_login.assert_not_awaited()

    async def test_a_non_bearer_authorization_header_is_not_counted(self, gate_hass):
        view = MCPEndpointView(gate_hass)

        with (
            patch.object(view, "_validate_token", return_value=None),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            response = await view.post(self._request(authorization="Basic dXNlcjpwYXNz"))

        assert response.status == 401
        process_wrong_login.assert_not_awaited()

    async def test_a_valid_token_is_not_counted(self, gate_hass):
        view = MCPEndpointView(gate_hass)

        with (
            patch.object(view, "_validate_token", return_value=TOKEN),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            response = await view.post(self._request())

        assert response.status == 200
        process_wrong_login.assert_not_awaited()

    async def test_a_valid_token_for_a_non_admin_is_not_counted(self, gate_hass):
        gate_hass.auth.async_get_user.return_value = _user(is_admin=False)
        view = MCPEndpointView(gate_hass)

        with (
            patch.object(view, "_validate_token", return_value=TOKEN),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            response = await view.post(self._request())

        assert response.status == 403
        process_wrong_login.assert_not_awaited()

    async def test_a_failure_while_counting_still_yields_the_401(self, gate_hass):
        view = MCPEndpointView(gate_hass)

        with (
            patch.object(view, "_validate_token", return_value=None),
            patch(_PROCESS_WRONG_LOGIN, new_callable=AsyncMock) as process_wrong_login,
        ):
            process_wrong_login.side_effect = AssertionError("no remote address")
            response = await view.post(self._request())

        assert response.status == 401
        assert json.loads(response.body)["error"] == "invalid_token"
        process_wrong_login.assert_awaited_once()


class TestMaskSecrets:
    """Secret-named argument values never reach the audit log or event."""

    def test_masks_lock_and_alarm_codes_but_not_lookalike_keys(self):
        arguments = {
            "service_data": {"code": "1234", "PIN": "0000", "passcode": "x", "zip_code": "12345"},
            "pin_count": 3,
        }

        assert mask_secrets(arguments) == {
            "service_data": {
                "code": SECRET_MASK,
                "PIN": SECRET_MASK,
                "passcode": SECRET_MASK,
                "zip_code": "12345",
            },
            "pin_count": 3,
        }

    def test_masks_secret_keys_at_any_depth(self):
        arguments = {
            "domain": "notify",
            "service_data": {"message": "hi", "api_key": "abc", "auth": {"Password": "pw"}},
            "access_token": "t",
        }

        assert mask_secrets(arguments) == {
            "domain": "notify",
            "service_data": {
                "message": "hi",
                "api_key": SECRET_MASK,
                "auth": {"Password": SECRET_MASK},
            },
            "access_token": SECRET_MASK,
        }

    def test_masks_inside_lists_of_objects(self):
        arguments = {"users": [{"name": "a", "client_secret": "s"}, {"Authorization": "b"}]}

        assert mask_secrets(arguments) == {
            "users": [{"name": "a", "client_secret": SECRET_MASK}, {"Authorization": SECRET_MASK}]
        }

    def test_masks_a_secret_whole_when_it_holds_an_object(self):
        assert mask_secrets({"credentials": {"user": "u"}}) == {"credentials": SECRET_MASK}

    @pytest.mark.parametrize("arguments", ["password", 5, None, ["token"]])
    def test_leaves_anything_but_objects_alone(self, arguments):
        assert mask_secrets(arguments) == arguments

    def test_does_not_modify_the_arguments_it_was_given(self):
        arguments = {"password": "pw"}

        mask_secrets(arguments)

        assert arguments == {"password": "pw"}


class TestTruncateArguments:
    """Arguments are shortened to a preview, keeping their shape."""

    def test_arguments_that_fit_are_unchanged(self):
        arguments = {"entity_id": "light.kitchen"}

        assert truncate_arguments(arguments) is arguments

    def test_a_long_string_is_cut_and_marked(self):
        result = truncate_arguments({"content": "x" * 10_000})

        assert set(result) == {"content"}
        assert result["content"].startswith("x" * 100)
        assert result["content"].endswith("chars)")
        assert len(dumps(result)) <= ARGUMENTS_PREVIEW_CHARS + 50

    def test_many_keys_are_capped_with_a_count(self):
        result = truncate_arguments({f"key{i}": i for i in range(1_000)})

        assert result["key0"] == 0
        assert result["(truncated)"].endswith("more keys")
        assert len(dumps(result)) <= ARGUMENTS_PREVIEW_CHARS + 50

    def test_a_long_list_is_capped_with_a_count(self):
        result = truncate_arguments(list(range(10_000)))

        assert result[0] == 0
        assert result[-1].endswith("more items)")
        assert len(dumps(result)) <= ARGUMENTS_PREVIEW_CHARS + 50


class TestToolAuditLog:
    """Each tools/call leaves one log line, and a call that can change something an event."""

    CALLER = Caller(user_id="user123", auth_method="oidc", user=_user())

    @pytest.fixture
    def audit_hass(self):
        audit_hass = Mock()
        audit_hass.data = {DOMAIN: {"entry_id": Mock()}}
        return audit_hass

    @pytest.fixture
    def view(self, audit_hass):
        return MCPEndpointView(audit_hass)

    async def _call(self, view, name, arguments, handler=None, schema=None):
        """POST one tools/call, optionally standing a handler in for the tool's own."""
        request = _jsonrpc_request("tools/call", {"name": name, "arguments": arguments})
        tools = {}
        if handler is not None:
            tools[name] = {"schema": schema or TOOLS[name]["schema"], "handler": handler}
        with (
            patch.object(view, "_authenticate", return_value=self.CALLER),
            patch.dict(TOOLS, tools),
        ):
            response = await view.post(request)
        return json.loads(response.body)

    @staticmethod
    def _audit_records(caplog):
        return [r for r in caplog.records if r.getMessage().startswith("MCP tool call")]

    @staticmethod
    def _audit_events(audit_hass):
        return [
            c
            for c in audit_hass.bus.async_fire.call_args_list
            if c.args and c.args[0] == EVENT_TOOL_CALLED
        ]

    async def test_an_unknown_tool_name_cannot_forge_a_log_line(self, audit_hass, caplog):
        view = MCPEndpointView(audit_hass)
        name = "restart_ha -> ok\nMCP tool call by user admin (native auth): restart_ha -> ok"

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            await view._handle_message(
                {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name}, "id": 1},
                self.CALLER,
            )

        (record,) = [r for r in caplog.records if "MCP tool call" in r.getMessage()]
        assert "\n" not in record.getMessage()
        assert "-> invalid" in record.getMessage()

    async def test_an_overlong_tool_name_is_cut_in_the_record(self, audit_hass, caplog):
        view = MCPEndpointView(audit_hass)
        name = "x" * 500

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            await view._handle_message(
                {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name}, "id": 1},
                self.CALLER,
            )

        (event,) = self._audit_events(audit_hass)
        assert event.args[1]["tool"] == "x" * TOOL_NAME_PREVIEW_CHARS
        assert name not in caplog.text

    async def test_a_write_tool_is_logged_at_info_with_its_caller(self, view, caplog):
        handler = AsyncMock(return_value={"content": [{"type": "text", "text": "done"}]})

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            await self._call(
                view, "call_service", {"domain": "light", "service": "turn_on"}, handler
            )

        (record,) = self._audit_records(caplog)
        assert record.levelno == logging.INFO
        message = record.getMessage()
        assert "user123" in message
        assert "oidc" in message
        assert "call_service" in message
        assert "-> ok" in message
        assert '{"domain":"light","service":"turn_on"}' in message

    async def test_a_read_only_tool_is_logged_at_debug_only(self, view, caplog):
        handler = AsyncMock(return_value={"content": []})

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            await self._call(view, "get_state", {"entity_id": "sun.sun"}, handler)
        assert self._audit_records(caplog) == []

        with caplog.at_level(logging.DEBUG, logger=_HTTP_LOGGER):
            await self._call(view, "get_state", {"entity_id": "sun.sun"}, handler)
        (record,) = self._audit_records(caplog)
        assert record.levelno == logging.DEBUG

    async def test_a_tool_without_annotations_counts_as_a_write(self, view, audit_hass, caplog):
        schema = {k: v for k, v in TOOLS["get_state"]["schema"].items() if k != "annotations"}
        handler = AsyncMock(return_value={"content": []})

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            await self._call(view, "get_state", {"entity_id": "sun.sun"}, handler, schema)

        (record,) = self._audit_records(caplog)
        assert record.levelno == logging.INFO
        assert len(self._audit_events(audit_hass)) == 1

    async def test_secret_arguments_are_masked_in_the_log(self, view, caplog):
        handler = AsyncMock(return_value={"content": []})
        arguments = {
            "domain": "notify",
            "service": "send",
            "service_data": {"password": "hunter2"},
        }

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            await self._call(view, "call_service", arguments, handler)

        (record,) = self._audit_records(caplog)
        assert "hunter2" not in record.getMessage()
        assert SECRET_MASK in record.getMessage()

    async def test_an_is_error_result_is_logged_as_error(self, view, caplog):
        handler = AsyncMock(side_effect=RuntimeError("boom"))

        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            body = await self._call(
                view, "call_service", {"domain": "light", "service": "turn_on"}, handler
            )

        assert body["result"]["isError"] is True
        (record,) = self._audit_records(caplog)
        assert "-> error" in record.getMessage()

    async def test_an_invalid_call_is_logged_as_invalid(self, view, audit_hass, caplog):
        with caplog.at_level(logging.INFO, logger=_HTTP_LOGGER):
            body = await self._call(view, "call_service", {"domain": "light"})

        assert body["error"]["code"] == -32602
        (record,) = self._audit_records(caplog)
        assert "-> invalid" in record.getMessage()
        (event,) = self._audit_events(audit_hass)
        assert event.args[1]["outcome"] == "invalid"

    async def test_a_write_tool_fires_the_event_as_its_caller(self, view, audit_hass):
        handler = AsyncMock(return_value={"content": []})
        arguments = {"domain": "lock", "service": "unlock", "service_data": {"entity_id": "l.a"}}
        arguments["service_data"]["token"] = "t"

        await self._call(view, "call_service", arguments, handler)

        (event,) = self._audit_events(audit_hass)
        assert event.args[1] == {
            "tool": "call_service",
            "user_id": "user123",
            "auth_method": "oidc",
            "outcome": "ok",
            "arguments": {
                "domain": "lock",
                "service": "unlock",
                "service_data": {"entity_id": "l.a", "token": SECRET_MASK},
            },
        }
        assert event.kwargs["context"].user_id == "user123"

    async def test_the_event_carries_truncated_arguments(self, view, audit_hass):
        handler = AsyncMock(return_value={"content": []})
        arguments = {"domain": "notify", "service": "send", "service_data": {"m": "x" * 5_000}}

        await self._call(view, "call_service", arguments, handler)

        (event,) = self._audit_events(audit_hass)
        assert len(dumps(event.args[1]["arguments"])) <= ARGUMENTS_PREVIEW_CHARS + 50

    async def test_a_read_only_tool_fires_no_event(self, view, audit_hass):
        handler = AsyncMock(return_value={"content": []})

        await self._call(view, "get_state", {"entity_id": "sun.sun"}, handler)

        assert self._audit_events(audit_hass) == []

    async def test_a_bus_that_refuses_the_event_does_not_fail_the_call(self, view, audit_hass):
        audit_hass.bus.async_fire = Mock(side_effect=RuntimeError("bus down"))
        handler = AsyncMock(return_value={"content": [{"type": "text", "text": "done"}]})

        body = await self._call(
            view, "call_service", {"domain": "light", "service": "turn_on"}, handler
        )

        assert body["result"]["content"][0]["text"] == "done"

    async def test_other_methods_are_not_audited(self, view, caplog):
        request = _jsonrpc_request("resources/list")

        with (
            caplog.at_level(logging.DEBUG, logger=_HTTP_LOGGER),
            patch.object(view, "_authenticate", return_value=self.CALLER),
        ):
            await view.post(request)

        assert self._audit_records(caplog) == []

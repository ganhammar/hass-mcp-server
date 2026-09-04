"""Tests for HTTP transport, auth, and JSON-RPC routing."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.oidc_provider.token_validator import get_issuer_from_request

from custom_components.mcp_server_http_transport.const import (
    DOMAIN,
    MCP_HTTP_PATH,
    MCP_PATH,
    RESOURCE_METADATA_PREFIX,
)
from custom_components.mcp_server_http_transport.http import (
    REGISTERED_ENDPOINT,
    REGISTERED_ROUTES,
    MCPEndpointView,
    MCPProtectedResourceMetadataView,
    MCPSubpathProtectedResourceMetadataView,
    _get_issuer,
    _get_protected_resource_metadata,
    mcp_path_is_contested,
    register_mcp_views,
    serves_mcp_path,
)
from custom_components.mcp_server_http_transport.tools import TOOLS, call_tool


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

    async def test_get_returns_metadata(self, routing_hass, mock_server):
        """Test GET returns protected resource metadata."""
        register_mcp_views(routing_hass, mock_server, False)
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

    async def test_get_with_forwarded_headers(self, routing_hass, mock_server):
        """Test GET with X-Forwarded headers."""
        register_mcp_views(routing_hass, mock_server, False)
        request = Mock(path=MCP_PATH)
        request.headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "example.com",
        }

        view = MCPProtectedResourceMetadataView(routing_hass)
        response = await view.get(request)

        body = json.loads(response.body)
        assert body["resource"] == "https://example.com/api/mcp"

    async def test_get_names_the_dedicated_path_when_api_mcp_is_contested(
        self, routing_hass, mock_server
    ):
        """The root path describes an endpoint this integration actually answers.

        A client falls back here when the path-suffixed metadata 404s, so naming
        a contested /api/mcp would send it to this authorization server for a
        token the integration holding that path rejects.
        """
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))
        register_mcp_views(routing_hass, mock_server, False)
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
def mock_server():
    """Create a mock MCP server."""
    return Mock()


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance with the integration loaded."""
    hass = Mock()
    hass.states = Mock()
    hass.services = Mock()
    hass.data = {DOMAIN: {"entry_id": Mock()}}
    return hass


@pytest.fixture
def view(mock_hass, mock_server):
    """Create an MCPEndpointView instance."""
    return MCPEndpointView(mock_hass, mock_server)


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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["protocolVersion"] == "2024-11-05"
        assert body["result"]["serverInfo"]["name"] == "home-assistant-mcp-server"
        assert body["id"] == 1

    async def test_post_initialize_advertises_capabilities(self, view):
        """Test POST initialize advertises resources and prompts capabilities."""
        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 21})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert "tools" in body["result"]
        assert len(body["result"]["tools"]) == 86
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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.get(request)

        assert response.status == 405
        assert response.headers["Allow"] == "OPTIONS, POST"
        assert "WWW-Authenticate" not in response.headers

    async def test_get_returns_503_when_unloaded(self, mock_server):
        """Test GET is gated on the integration being loaded, like POST."""
        hass = Mock()
        hass.data = {}
        view = MCPEndpointView(hass, mock_server)

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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
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

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
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
        view = MCPEndpointView(hass, Mock())

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_token"}

        response = await view.post(request)

        assert response.status == 503
        body = json.loads(response.body)
        assert body["error"] == "service_unavailable"

    async def test_endpoint_view_returns_503_when_domain_cleared(self):
        hass = Mock()
        hass.data = {"mcp_server_http_transport": {}}  # matches async_unload_entry.clear()
        view = MCPEndpointView(hass, Mock())

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
        view = MCPEndpointView(hass, Mock())

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
        return hass

    @pytest.fixture
    def view(self, mock_hass):
        """Create an MCPEndpointView with native auth enabled."""
        return MCPEndpointView(mock_hass, Mock(), native_auth_enabled=True)

    @pytest.fixture
    def view_disabled(self, mock_hass):
        """Create an MCPEndpointView with native auth disabled."""
        return MCPEndpointView(mock_hass, Mock(), native_auth_enabled=False)

    async def test_llat_validates_when_enabled(self, view, mock_hass):
        """Test that a valid LLAT is accepted when native auth is enabled."""
        mock_refresh_token = Mock()
        mock_refresh_token.user.id = "user_abc"
        mock_hass.auth.async_validate_access_token.return_value = mock_refresh_token

        request = Mock(path=MCP_PATH)
        request.headers = {"Authorization": "Bearer valid_llat"}

        result = await view._validate_token(request)

        assert result == {"sub": "user_abc"}
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

        assert result == {"sub": "oidc_user"}
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

        assert result == {"sub": "ha_user"}

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

        assert result == {"sub": "fallback_user"}

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
        return MCPEndpointView(hass, Mock(), native_auth_enabled=False)

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

        assert result == {"sub": "u"}
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

    async def test_falls_back_to_legacy_signature_on_type_error(self, view):
        """An older OIDC provider without expected_audience still works."""
        import sys

        request = Mock(path=MCP_PATH)
        request.headers = {
            "Authorization": "Bearer t",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ha.example.com",
        }

        def side_effect(*args, **kwargs):
            if "expected_audience" in kwargs:
                raise TypeError("unexpected keyword argument 'expected_audience'")
            return {"sub": "legacy"}

        mv = sys.modules["custom_components.oidc_provider.token_validator"]
        mv.validate_access_token.reset_mock()
        mv.validate_access_token.side_effect = side_effect
        try:
            result = await view._validate_token(request)
        finally:
            mv.validate_access_token.side_effect = None
            mv.validate_access_token.return_value = None

        assert result == {"sub": "legacy"}


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

    def test_claims_both_paths_when_api_mcp_is_free(self, routing_hass, mock_server):
        """With nothing else on /api/mcp, the endpoint serves both paths."""
        register_mcp_views(routing_hass, mock_server, False)

        assert _paths(routing_hass.http.app.router) == [MCP_PATH, MCP_HTTP_PATH]

    def test_registers_the_metadata_views(self, routing_hass, mock_server):
        """Both RFC 9728 metadata paths are served alongside the endpoint."""
        register_mcp_views(routing_hass, mock_server, False)

        assert _paths(routing_hass.http.app.router, "GET") == [
            RESOURCE_METADATA_PREFIX,
            f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}",
            f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}",
            MCP_PATH,
            MCP_HTTP_PATH,
        ]

    def test_stays_off_api_mcp_when_another_integration_serves_it(self, routing_hass, mock_server):
        """A path already answering POST elsewhere is left alone entirely.

        Home Assistant's built-in mcp_server registers no GET on /api/mcp, so
        taking half the path would answer a client's discovery probe here while
        its actual traffic went to the other integration.
        """
        router = routing_hass.http.app.router
        router._routes.append(_FakeRoute("POST", MCP_PATH))

        register_mcp_views(routing_hass, mock_server, False)

        assert _paths(router) == [MCP_PATH, MCP_HTTP_PATH]  # the foreign one, then ours
        assert MCP_PATH not in _paths(router, "GET")

    def test_metadata_for_a_contested_path_is_left_alone_too(self, routing_hass, mock_server):
        """The RFC 9728 metadata follows the endpoint off a contested path.

        Nothing else serves that metadata, so answering there would hand a
        client this integration's authorization server for a token the
        integration actually holding /api/mcp will reject.
        """
        router = routing_hass.http.app.router
        router._routes.append(_FakeRoute("POST", MCP_PATH))

        register_mcp_views(routing_hass, mock_server, False)

        assert f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}" not in _paths(router, "GET")
        assert f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}" in _paths(router, "GET")

    def test_a_wildcard_route_counts_as_a_competitor(self, routing_hass, mock_server):
        """A route registered for every method answers POST as well."""
        routing_hass.http.app.router._routes.append(_FakeRoute("*", MCP_PATH))

        register_mcp_views(routing_hass, mock_server, False)

        assert mcp_path_is_contested(routing_hass) is True
        assert MCP_PATH not in _paths(routing_hass.http.app.router, "GET")

    def test_own_routes_are_not_mistaken_for_a_conflict(self, routing_hass, mock_server):
        """A reload does not make this integration its own competitor."""
        register_mcp_views(routing_hass, mock_server, False)
        register_mcp_views(routing_hass, mock_server, False)

        assert mcp_path_is_contested(routing_hass) is False

    def test_a_reload_updates_the_view_the_router_holds(self, routing_hass, mock_server):
        """Registering again would sit behind the first view and never be reached."""
        register_mcp_views(routing_hass, mock_server, False)
        routes_after_first_load = routing_hass.http.app.router.routes()
        endpoint = routing_hass.data[REGISTERED_ENDPOINT]

        reloaded_server = Mock()
        register_mcp_views(routing_hass, reloaded_server, True)

        assert routing_hass.http.app.router.routes() == routes_after_first_load
        assert endpoint.native_auth_enabled is True
        assert endpoint.server is reloaded_server

    def test_serves_mcp_path_reports_who_answers(self, routing_hass, mock_server):
        """Only the first route on the path answers, whoever registered it."""
        register_mcp_views(routing_hass, mock_server, False)

        assert serves_mcp_path(routing_hass) is True

        # A competitor arriving later queues behind the route already bound.
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))

        assert mcp_path_is_contested(routing_hass) is True
        assert serves_mcp_path(routing_hass) is True

    def test_serves_mcp_path_is_false_when_another_integration_got_there_first(
        self, routing_hass, mock_server
    ):
        """A path claimed before setup is answered by its holder, not this one."""
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))

        register_mcp_views(routing_hass, mock_server, False)

        assert serves_mcp_path(routing_hass) is False

    def test_conflict_is_seen_when_the_other_integration_arrives_later(
        self, routing_hass, mock_server
    ):
        """A path claimed after setup still reports as contested."""
        register_mcp_views(routing_hass, mock_server, False)
        routing_hass.http.app.router._routes.append(_FakeRoute("POST", MCP_PATH))

        assert mcp_path_is_contested(routing_hass) is True

    def test_registered_routes_survive_an_unload(self, routing_hass, mock_server):
        """The tracked routes live outside hass.data[DOMAIN], which unload clears."""
        register_mcp_views(routing_hass, mock_server, False)
        routing_hass.data[DOMAIN].clear()

        assert routing_hass.data[REGISTERED_ROUTES]
        assert mcp_path_is_contested(routing_hass) is False

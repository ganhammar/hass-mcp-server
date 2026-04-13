"""Tests for HTTP transport, auth, and JSON-RPC routing."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.oidc_provider.token_validator import get_issuer_from_request

from custom_components.mcp_server_http_transport.http import (
    MCPEndpointView,
    MCPProtectedResourceMetadataView,
    MCPSubpathProtectedResourceMetadataView,
    _get_issuer,
    _get_protected_resource_metadata,
)


def test_get_base_url_with_forwarded_headers():
    """Test get_issuer_from_request with X-Forwarded headers (proxy setup)."""
    request = Mock()
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
    request = Mock()
    request.headers = {}
    request.url.origin.return_value = "http://192.168.1.100:8123"

    result = get_issuer_from_request(request)

    assert result == "http://192.168.1.100:8123"
    request.url.origin.assert_called_once()


def test_get_base_url_with_partial_forwarded_headers():
    """Test get_issuer_from_request with only one X-Forwarded header (should use fallback)."""
    request = Mock()
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

    request = Mock()
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

    metadata = _get_protected_resource_metadata(base_url)

    assert metadata["resource"] == f"{base_url}/api/mcp"
    assert metadata["authorization_servers"] == [f"{base_url}/oidc"]
    assert metadata["bearer_methods_supported"] == ["header"]
    assert metadata["resource_signing_alg_values_supported"] == ["RS256"]
    assert metadata["resource_documentation"] == f"{base_url}/api/mcp"


class TestMCPProtectedResourceMetadataView:
    """Test the MCP protected resource metadata view at root."""

    async def test_get_returns_metadata(self):
        """Test GET returns protected resource metadata."""
        request = Mock()
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPProtectedResourceMetadataView()
        response = await view.get(request)

        assert response.status == 200
        assert response.content_type == "application/json"

        body = json.loads(response.body)
        assert body["resource"] == "https://homeassistant.local/api/mcp"
        assert body["authorization_servers"] == ["https://homeassistant.local/oidc"]

    async def test_get_with_forwarded_headers(self):
        """Test GET with X-Forwarded headers."""
        request = Mock()
        request.headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "example.com",
        }

        view = MCPProtectedResourceMetadataView()
        response = await view.get(request)

        body = json.loads(response.body)
        assert body["resource"] == "https://example.com/api/mcp"

    async def test_get_returns_404_when_oidc_unavailable(self):
        """Test GET returns 404 when OIDC provider is not installed."""
        request = Mock()

        view = MCPProtectedResourceMetadataView()
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
        request = Mock()
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        view = MCPSubpathProtectedResourceMetadataView()
        response = await view.get(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["resource"] == "https://homeassistant.local/api/mcp"

    async def test_get_returns_404_when_oidc_unavailable(self):
        """Test GET returns 404 when OIDC provider is not installed."""
        request = Mock()

        view = MCPSubpathProtectedResourceMetadataView()
        with patch(
            "custom_components.mcp_server_http_transport.http._get_issuer",
            return_value=None,
        ):
            response = await view.get(request)

        assert response.status == 404


class TestMCPEndpointView:
    """Test the MCP endpoint view: auth, routing, and error handling."""

    @pytest.fixture
    def mock_server(self):
        """Create a mock MCP server."""
        return Mock()

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.services = Mock()
        return hass

    @pytest.fixture
    def view(self, mock_hass, mock_server):
        """Create an MCPEndpointView instance."""
        return MCPEndpointView(mock_hass, mock_server)

    async def test_post_without_token_returns_401(self, view):
        """Test POST without Authorization header returns 401."""
        request = Mock()
        request.headers = {}
        request.url.origin.return_value = "https://homeassistant.local"

        response = await view.post(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"
        assert "WWW-Authenticate" in response.headers

    async def test_post_with_invalid_token_returns_401(self, view):
        """Test POST with invalid token returns 401."""
        request = Mock()
        request.headers = {"Authorization": "Bearer invalid_token"}
        request.url.origin.return_value = "https://homeassistant.local"

        with patch.object(view, "_validate_token", return_value=None):
            response = await view.post(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"] == "invalid_token"

    async def test_post_initialize_request(self, view):
        """Test POST with initialize request."""
        request = Mock()
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
        request = Mock()
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
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/list", "id": 2})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["jsonrpc"] == "2.0"
        assert "tools" in body["result"]
        assert len(body["result"]["tools"]) == 43
        tool_names = [t["name"] for t in body["result"]["tools"]]
        assert "get_state" in tool_names
        assert "call_service" in tool_names
        assert "list_entities" in tool_names
        assert "get_error_log" in tool_names
        assert "restart_ha" in tool_names
        assert "get_system_status" in tool_names
        assert "get_statistics" in tool_names
        assert "list_labels" in tool_names
        assert "batch_get_state" in tool_names

    async def test_post_unknown_method_returns_error(self, view):
        """Test POST with unknown method returns error."""
        request = Mock()
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
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "some_notification"})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 202

    async def test_validate_token_without_bearer_prefix(self, view):
        """Test _validate_token without Bearer prefix returns None."""
        request = Mock()
        request.headers = {"Authorization": "invalid_format"}

        result = await view._validate_token(request)

        assert result is None

    async def test_post_tools_call_unknown_tool(self, view):
        """Test POST with tools/call for unknown tool."""
        request = Mock()
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

        assert response.status == 500
        body = json.loads(response.body)
        assert "error" in body
        assert "Unknown tool" in body["error"]["message"]


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

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_llat"}

        result = await view._validate_token(request)

        assert result == {"sub": "user_abc"}
        mock_hass.auth.async_validate_access_token.assert_called_once_with("valid_llat")

    async def test_llat_rejected_when_disabled(self, view_disabled, mock_hass):
        """Test that LLAT is not tried when native auth is disabled."""
        request = Mock()
        request.headers = {"Authorization": "Bearer some_token"}

        result = await view_disabled._validate_token(request)

        assert result is None
        mock_hass.auth.async_validate_access_token.assert_not_called()

    async def test_invalid_llat_returns_none(self, view, mock_hass):
        """Test that an invalid LLAT returns None."""
        mock_hass.auth.async_validate_access_token.return_value = None

        request = Mock()
        request.headers = {"Authorization": "Bearer bad_token"}

        result = await view._validate_token(request)

        assert result is None

    async def test_oidc_tried_before_llat(self, view, mock_hass):
        """Test that OIDC validation is attempted before LLAT."""
        import sys

        request = Mock()
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

        request = Mock()
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

        request = Mock()
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

        request = Mock()
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

        request = Mock()
        request.headers = {"Authorization": "Bearer my_llat"}
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "method": "initialize", "id": 1})

        response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["result"]["protocolVersion"] == "2024-11-05"

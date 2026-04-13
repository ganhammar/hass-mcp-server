"""HTTP transport for MCP server."""

import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from mcp.server import Server

from .completions import complete
from .prompts import get_prompt, get_prompts
from .resources import get_resources, read_resource
from .tools import call_tool, get_tool_schemas

_LOGGER = logging.getLogger(__name__)


def _get_issuer(request: web.Request) -> str | None:
    """Get the OIDC issuer URL from the request, or None if unavailable."""
    try:
        from custom_components.oidc_provider.token_validator import (
            get_issuer_from_request,
        )

        return get_issuer_from_request(request)
    except ImportError:
        return None


def _get_protected_resource_metadata(base_url: str) -> dict[str, Any]:
    """Generate OAuth 2.0 Protected Resource Metadata (RFC 9728)."""
    return {
        "resource": f"{base_url}/api/mcp",
        "authorization_servers": [f"{base_url}/oidc"],
        "bearer_methods_supported": ["header"],
        "resource_signing_alg_values_supported": ["RS256"],
        "resource_documentation": f"{base_url}/api/mcp",
    }


class MCPProtectedResourceMetadataView(HomeAssistantView):
    """OAuth 2.0 Protected Resource Metadata endpoint (RFC 9728) at root."""

    url = "/.well-known/oauth-protected-resource"
    name = "api:mcp:metadata:root"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return protected resource metadata."""
        base_url = _get_issuer(request)
        if base_url is None:
            return web.json_response({"error": "OIDC provider not available"}, status=404)
        metadata = _get_protected_resource_metadata(base_url)
        return web.json_response(metadata)


class MCPSubpathProtectedResourceMetadataView(HomeAssistantView):
    """OAuth 2.0 Protected Resource Metadata endpoint (RFC 9728) with /mcp suffix."""

    url = "/.well-known/oauth-protected-resource/api/mcp"
    name = "api:mcp:metadata:mcp"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return protected resource metadata with /mcp suffix."""
        base_url = _get_issuer(request)
        if base_url is None:
            return web.json_response({"error": "OIDC provider not available"}, status=404)
        metadata = _get_protected_resource_metadata(base_url)
        return web.json_response(metadata)


class MCPEndpointView(HomeAssistantView):
    """MCP HTTP endpoint view."""

    url = "/api/mcp"
    name = "api:mcp"
    requires_auth = False

    def __init__(
        self, hass: HomeAssistant, server: Server, native_auth_enabled: bool = False
    ) -> None:
        """Initialize the MCP endpoint."""
        self.hass = hass
        self.server = server
        self.native_auth_enabled = native_auth_enabled

    async def _validate_token(self, request: web.Request) -> dict[str, Any] | None:
        """Validate the bearer token via OIDC (if available) then native HA auth."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]  # Remove "Bearer " prefix

        # 1. Try OIDC first
        try:
            from custom_components.oidc_provider.token_validator import (
                get_issuer_from_request,
                validate_access_token,
            )

            expected_issuer = get_issuer_from_request(request)
            result = validate_access_token(self.hass, token, expected_issuer)
            if result:
                return result
        except ImportError as e:
            _LOGGER.debug("OIDC provider not available: %s", e)

        # 2. Fall back to native HA auth (Long-Lived Access Tokens)
        if self.native_auth_enabled:
            refresh_token = self.hass.auth.async_validate_access_token(token)
            if refresh_token is not None:
                return {"sub": refresh_token.user.id}

        return None

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST requests for MCP messages."""
        # Validate token
        token_payload = await self._validate_token(request)
        if not token_payload:
            # Build WWW-Authenticate header
            base_url = _get_issuer(request)
            if base_url is not None:
                resource_metadata_url = f"{base_url}/.well-known/oauth-protected-resource/api/mcp"
                www_authenticate = (
                    f'Bearer realm="MCP Server",' f' resource_metadata="{resource_metadata_url}"'
                )
            else:
                www_authenticate = 'Bearer realm="Home Assistant MCP Server"'

            return web.json_response(
                {
                    "error": "invalid_token",
                    "error_description": "Invalid or missing token",
                },
                status=401,
                headers={"WWW-Authenticate": www_authenticate},
            )

        try:
            # Parse JSON-RPC message
            body = await request.json()
            _LOGGER.debug("Received MCP request: %s", body)

            # Process the message directly
            response_data = await self._handle_message(body)

            if response_data is None:
                # Notification - return 202 Accepted
                return web.Response(status=202)

            # Return JSON response
            return web.json_response(response_data)

        except Exception as e:
            _LOGGER.error("Error handling MCP request: %s", e, exc_info=True)
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}",
                    },
                    "id": body.get("id") if isinstance(body, dict) else None,
                },
                status=500,
            )

    async def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a JSON-RPC message."""
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")

        # Handle initialization
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                        "prompts": {},
                    },
                    "serverInfo": {
                        "name": "home-assistant-mcp-server",
                        "version": "0.1.0",
                    },
                },
                "id": msg_id,
            }

        # Handle tools/list
        if method == "tools/list":
            tools = await self._get_tools()
            return {
                "jsonrpc": "2.0",
                "result": {"tools": tools},
                "id": msg_id,
            }

        # Handle tools/call
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})

            result = await self._call_tool(name, arguments)
            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": msg_id,
            }

        # Handle resources/list
        if method == "resources/list":
            result = get_resources()
            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": msg_id,
            }

        # Handle resources/read
        if method == "resources/read":
            uri = params.get("uri", "")
            contents = await read_resource(self.hass, uri)
            return {
                "jsonrpc": "2.0",
                "result": {"contents": contents},
                "id": msg_id,
            }

        # Handle prompts/list
        if method == "prompts/list":
            prompts = get_prompts()
            return {
                "jsonrpc": "2.0",
                "result": {"prompts": prompts},
                "id": msg_id,
            }

        # Handle prompts/get
        if method == "prompts/get":
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await get_prompt(self.hass, name, arguments)
            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": msg_id,
            }

        # Handle completion/complete
        if method == "completion/complete":
            ref = params.get("ref", {})
            argument = params.get("argument", {})
            result = await complete(self.hass, ref, argument)
            return {
                "jsonrpc": "2.0",
                "result": {"completion": result},
                "id": msg_id,
            }

        # Unknown method
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
                "id": msg_id,
            }

        return None

    async def _get_tools(self) -> list[dict[str, Any]]:
        """Get available tools."""
        return get_tool_schemas()

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool by name."""
        return await call_tool(self.hass, name, arguments)

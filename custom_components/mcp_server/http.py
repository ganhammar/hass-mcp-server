"""HTTP transport for MCP server."""

import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from mcp.server import Server

from custom_components.oidc_provider.token_validator import get_issuer_from_request

_LOGGER = logging.getLogger(__name__)


def _get_protected_resource_metadata(base_url: str) -> dict[str, Any]:
    """Generate OAuth 2.0 Protected Resource Metadata (RFC 9728)."""
    return {
        "resource": base_url,
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
        base_url = get_issuer_from_request(request)
        metadata = _get_protected_resource_metadata(base_url)
        return web.json_response(metadata)


class MCPSubpathProtectedResourceMetadataView(HomeAssistantView):
    """OAuth 2.0 Protected Resource Metadata endpoint (RFC 9728) with /mcp suffix."""

    url = "/.well-known/oauth-protected-resource/api/mcp"
    name = "api:mcp:metadata:mcp"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return protected resource metadata with /mcp suffix."""
        base_url = get_issuer_from_request(request)
        metadata = _get_protected_resource_metadata(base_url)
        return web.json_response(metadata)


class MCPEndpointView(HomeAssistantView):
    """MCP HTTP endpoint view."""

    url = "/api/mcp"
    name = "api:mcp"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, server: Server) -> None:
        """Initialize the MCP endpoint."""
        self.hass = hass
        self.server = server

    def _validate_token(self, request: web.Request) -> dict[str, Any] | None:
        """Validate the OAuth bearer token."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Import dynamically to avoid circular dependency
        try:
            from custom_components.oidc_provider.token_validator import (
                get_issuer_from_request,
                validate_access_token,
            )

            # Get the expected issuer from the request
            expected_issuer = get_issuer_from_request(request)

            return validate_access_token(self.hass, token, expected_issuer)
        except ImportError as e:
            _LOGGER.error("OIDC provider integration not found: %s", e)
            return None

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST requests for MCP messages."""
        # Validate OAuth token
        token_payload = self._validate_token(request)
        if not token_payload:
            base_url = get_issuer_from_request(request)
            # Point to /oidc authorization server metadata directly
            resource_metadata_url = f"{base_url}/oidc/.well-known/oauth-authorization-server"
            www_authenticate = (
                f'Bearer realm="MCP Server", resource_metadata="{resource_metadata_url}"'
            )
            return web.json_response(
                {"error": "invalid_token", "error_description": "Invalid or missing token"},
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
                    "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
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

        # Unknown method
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": msg_id,
            }

        return None

    async def _get_tools(self) -> list[dict[str, Any]]:
        """Get available tools."""
        return [
            {
                "name": "get_state",
                "description": "Get the state of a Home Assistant entity",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "The entity ID (e.g., light.living_room)",
                        }
                    },
                    "required": ["entity_id"],
                },
            },
            {
                "name": "call_service",
                "description": "Call a Home Assistant service",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "The service domain (e.g., light, switch)",
                        },
                        "service": {
                            "type": "string",
                            "description": "The service name (e.g., turn_on, turn_off)",
                        },
                        "entity_id": {
                            "type": "string",
                            "description": "The entity ID to target",
                        },
                        "data": {
                            "type": "object",
                            "description": "Additional service data",
                        },
                    },
                    "required": ["domain", "service"],
                },
            },
            {
                "name": "list_entities",
                "description": "List all entities in Home Assistant",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "Filter by domain (optional)",
                        }
                    },
                },
            },
        ]

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool."""
        if name == "get_state":
            return await self._get_state(arguments)
        elif name == "call_service":
            return await self._call_service(arguments)
        elif name == "list_entities":
            return await self._list_entities(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")

    async def _get_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Get entity state."""
        entity_id = arguments["entity_id"]
        state = self.hass.states.get(entity_id)

        if state is None:
            return {"content": [{"type": "text", "text": f"Entity {entity_id} not found"}]}

        result = {
            "entity_id": state.entity_id,
            "state": state.state,
            "attributes": dict(state.attributes),
            "last_changed": state.last_changed.isoformat(),
            "last_updated": state.last_updated.isoformat(),
        }

        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    async def _call_service(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a Home Assistant service."""
        domain = arguments["domain"]
        service = arguments["service"]
        entity_id = arguments.get("entity_id")
        data = arguments.get("data", {})

        service_data = {**data}
        if entity_id:
            service_data["entity_id"] = entity_id

        try:
            await self.hass.services.async_call(domain, service, service_data, blocking=True)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Successfully called {domain}.{service}",
                    }
                ]
            }
        except Exception as e:
            _LOGGER.error("Error calling service: %s", e)
            return {"content": [{"type": "text", "text": f"Error calling service: {str(e)}"}]}

    async def _list_entities(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """List entities."""
        domain_filter = arguments.get("domain")

        entities = []
        for state in self.hass.states.async_all():
            if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
                continue
            entities.append(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                }
            )

        return {"content": [{"type": "text", "text": json.dumps(entities, indent=2)}]}

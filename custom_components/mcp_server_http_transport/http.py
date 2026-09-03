"""HTTP transport for MCP server."""

import logging
from typing import Any

from aiohttp import hdrs, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from mcp.server import Server

from .completions import complete
from .const import (
    DOMAIN,
    MCP_HTTP_PATH,
    MCP_PATH,
    RESOURCE_METADATA_PREFIX,
)
from .prompts import get_prompt, get_prompts
from .resources import get_resources, read_resource
from .tools import InvalidToolRequest, call_tool, get_tool_schemas

_LOGGER = logging.getLogger(__name__)

# hass.data keys holding the routes this integration registered and the endpoint
# view serving them. They sit outside hass.data[DOMAIN] on purpose:
# async_unload_entry clears that, while routes stay bound until Home Assistant
# restarts and a reload has to find the view the router already holds.
REGISTERED_ROUTES = f"{DOMAIN}_routes"
REGISTERED_ENDPOINT = f"{DOMAIN}_endpoint_view"


def _resource_path(request_path: str) -> str:
    """Return which MCP endpoint a request is about.

    The RFC 9728 metadata path is the endpoint path suffixed onto the well-known
    prefix, so one suffix test covers both /api/mcp_http and
    /.well-known/oauth-protected-resource/api/mcp_http.
    """
    return MCP_HTTP_PATH if request_path.endswith(MCP_HTTP_PATH) else MCP_PATH


def _mcp_post_routes(hass: HomeAssistant) -> list[web.AbstractRoute]:
    """Return every route answering a POST to MCP_PATH, in registration order.

    aiohttp walks same-path resources linearly and hands the request to the
    first one that has the method, so the head of this list is what a client
    POSTing to MCP_PATH actually reaches. A wildcard route carries METH_ANY
    rather than a method name and answers POST just the same.
    """
    return [
        route
        for route in hass.http.app.router.routes()
        if route.method in (hdrs.METH_POST, hdrs.METH_ANY)
        and (resource := route.resource) is not None
        and resource.canonical == MCP_PATH
    ]


def mcp_path_is_contested(hass: HomeAssistant) -> bool:
    """Return True when an integration other than this one has MCP_PATH bound."""
    ours = hass.data.get(REGISTERED_ROUTES, set())
    return any(route not in ours for route in _mcp_post_routes(hass))


def serves_mcp_path(hass: HomeAssistant) -> bool:
    """Return True when a POST to MCP_PATH reaches this integration.

    Only the head of the list matters, and it can be a route from an earlier
    load: routes cannot be unregistered, so the view that claimed MCP_PATH
    before a competitor appeared keeps answering there until a restart.
    """
    routes = _mcp_post_routes(hass)
    return bool(routes) and routes[0] in hass.data.get(REGISTERED_ROUTES, set())


def _integration_loaded(hass: HomeAssistant) -> bool:
    """Return True when the config entry is active.

    HA's HTTP stack has no public way to unregister a view, so registered
    views survive `async_unload_entry`. async_unload_entry clears
    `hass.data[DOMAIN]`, so we gate requests on it being populated — when
    the user disables the integration, requests return 503 immediately
    instead of continuing to succeed until the next HA restart (#37).
    """
    return bool(hass.data.get(DOMAIN))


def _service_unavailable() -> web.Response:
    """Build a 503 response for requests made while the integration is disabled."""
    return web.json_response(
        {
            "error": "service_unavailable",
            "error_description": "MCP Server integration is disabled",
        },
        status=503,
    )


def _jsonrpc_error(code: int, message: str, msg_id: Any = None) -> dict[str, Any]:
    """Build a JSON-RPC error response object."""
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": msg_id,
    }


def _get_issuer(request: web.Request) -> str | None:
    """Get the OIDC issuer URL from the request, or None if unavailable."""
    try:
        from custom_components.oidc_provider.token_validator import (
            get_issuer_from_request,
        )

        return get_issuer_from_request(request)
    except ImportError:
        return None


def _get_protected_resource_metadata(base_url: str, resource_path: str) -> dict[str, Any]:
    """Generate OAuth 2.0 Protected Resource Metadata (RFC 9728)."""
    resource = f"{base_url}{resource_path}"
    return {
        "resource": resource,
        "authorization_servers": [f"{base_url}/oidc"],
        "scopes_supported": ["openid"],
        "bearer_methods_supported": ["header"],
        "resource_signing_alg_values_supported": ["RS256"],
        "resource_documentation": resource,
    }


class MCPProtectedResourceMetadataView(HomeAssistantView):
    """OAuth 2.0 Protected Resource Metadata endpoint (RFC 9728) at root."""

    url = "/.well-known/oauth-protected-resource"
    name = "api:mcp:metadata:root"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the metadata view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return protected resource metadata for the endpoint this server holds.

        The root path names no endpoint of its own, and a client falls back to it
        when the path-suffixed form 404s, so it has to describe a path this
        integration actually answers: naming a contested MCP_PATH would send the
        client here for a token the integration holding that path rejects.

        Home Assistant's auth component serves its own metadata here on the
        versions that have a root view, and wins the route by registering first,
        so this answers only where nothing else does.
        """
        if not _integration_loaded(self.hass):
            return _service_unavailable()
        base_url = _get_issuer(request)
        if base_url is None:
            return web.json_response({"error": "OIDC provider not available"}, status=404)
        resource_path = MCP_PATH if serves_mcp_path(self.hass) else MCP_HTTP_PATH
        metadata = _get_protected_resource_metadata(base_url, resource_path)
        return web.json_response(metadata)


class MCPSubpathProtectedResourceMetadataView(HomeAssistantView):
    """OAuth 2.0 Protected Resource Metadata (RFC 9728) for each endpoint path."""

    url = f"{RESOURCE_METADATA_PREFIX}{MCP_PATH}"
    extra_urls = [f"{RESOURCE_METADATA_PREFIX}{MCP_HTTP_PATH}"]
    name = "api:mcp:metadata:mcp"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, paths: list[str] | None = None) -> None:
        """Initialize the metadata view, optionally narrowing the paths served."""
        self.hass = hass
        if paths is not None:
            self.url, self.extra_urls = paths[0], list(paths[1:])

    async def get(self, request: web.Request) -> web.Response:
        """Return protected resource metadata for the endpoint the path names."""
        if not _integration_loaded(self.hass):
            return _service_unavailable()
        base_url = _get_issuer(request)
        if base_url is None:
            return web.json_response({"error": "OIDC provider not available"}, status=404)
        metadata = _get_protected_resource_metadata(base_url, _resource_path(request.path))
        return web.json_response(metadata)


class MCPEndpointView(HomeAssistantView):
    """MCP HTTP endpoint view."""

    url = MCP_PATH
    extra_urls = [MCP_HTTP_PATH]
    name = "api:mcp"
    requires_auth = False

    def __init__(
        self,
        hass: HomeAssistant,
        server: Server,
        native_auth_enabled: bool = False,
        paths: list[str] | None = None,
    ) -> None:
        """Initialize the MCP endpoint, optionally narrowing the paths served."""
        self.hass = hass
        self.server = server
        self.native_auth_enabled = native_auth_enabled
        if paths is not None:
            self.url, self.extra_urls = paths[0], list(paths[1:])

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
            # This MCP server is the protected resource (RFC 8707); its canonical
            # URI is the resource a compliant client (e.g. Claude) binds the token
            # to. Require the token's aud to match it.
            expected_audience = f"{expected_issuer}{_resource_path(request.path)}"
            try:
                result = validate_access_token(
                    self.hass, token, expected_issuer, expected_audience=expected_audience
                )
            except TypeError:
                # OIDC provider predates resource-aware validation; fall back to
                # the legacy signature so an un-upgraded provider still works.
                result = validate_access_token(self.hass, token, expected_issuer)
            if result is not None:
                return result
        except ImportError as e:
            _LOGGER.debug("OIDC provider not available: %s", e)

        # 2. Fall back to native HA auth (Long-Lived Access Tokens)
        if self.native_auth_enabled:
            refresh_token = self.hass.auth.async_validate_access_token(token)
            if refresh_token is not None:
                return {"sub": refresh_token.user.id}

        return None

    def _unauthorized(self, request: web.Request) -> web.Response:
        """Build the 401 that points a client at this resource's metadata.

        The resource_metadata URL is the RFC 9728 path-suffixed form. HA Core
        serves its own metadata at the bare /.well-known/oauth-protected-resource
        and wins that route by registering first, so a client that falls back to
        the root path is told the authorization server is HA itself.
        """
        base_url = _get_issuer(request)
        if base_url is not None:
            resource_metadata_url = (
                f"{base_url}{RESOURCE_METADATA_PREFIX}{_resource_path(request.path)}"
            )
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

    async def get(self, request: web.Request) -> web.Response:
        """Answer GET probes, which clients use to discover how to authenticate.

        This endpoint carries no SSE stream, so an authenticated GET is Method
        Not Allowed. An unauthenticated one still gets the challenge, because a
        bare 405 leaves the client with no pointer to the resource metadata.
        """
        if not _integration_loaded(self.hass):
            return _service_unavailable()

        if not await self._validate_token(request):
            return self._unauthorized(request)

        return web.json_response(
            {
                "error": "method_not_allowed",
                "error_description": "This endpoint accepts POST and serves no SSE stream",
            },
            status=405,
            headers={"Allow": "OPTIONS, POST"},
        )

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST requests for MCP messages."""
        if not _integration_loaded(self.hass):
            return _service_unavailable()

        # Validate token
        token_payload = await self._validate_token(request)
        if not token_payload:
            return self._unauthorized(request)

        try:
            # Parse JSON-RPC message. Only decoding failures belong here —
            # aiohttp's own exceptions (an oversized body, a client that hung
            # up mid-read) carry the right status already and must propagate.
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as e:
            _LOGGER.error("Could not parse MCP request body: %s", e)
            return web.json_response(
                _jsonrpc_error(-32700, f"Parse error: {str(e)}"),
                status=400,
            )

        _LOGGER.debug("Received MCP request: %s", body)

        try:
            # Process the message directly
            response_data = await self._handle_message(body)

            if response_data is None:
                # Notification - return 202 Accepted
                return web.Response(status=202)

            # Return JSON response
            return web.json_response(response_data)

        except Exception as e:
            _LOGGER.error("Error handling MCP request: %s", e, exc_info=True)
            # A JSON-RPC error is a complete response, so it ships with 200.
            # Under a non-2xx status intermediary proxies substitute their own
            # error page and the client never sees what actually went wrong.
            return web.json_response(
                _jsonrpc_error(
                    -32603,
                    f"Internal error: {str(e)}",
                    body.get("id") if isinstance(body, dict) else None,
                )
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

            try:
                result = await self._call_tool(name, arguments)
            except InvalidToolRequest as err:
                return _jsonrpc_error(-32602, str(err), msg_id)

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
            return _jsonrpc_error(-32601, f"Method not found: {method}", msg_id)

        return None

    async def _get_tools(self) -> list[dict[str, Any]]:
        """Get available tools."""
        return get_tool_schemas()

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool by name."""
        return await call_tool(self.hass, name, arguments)


def register_mcp_views(hass: HomeAssistant, server: Server, native_auth_enabled: bool) -> None:
    """Register the HTTP views this integration serves.

    MCP_PATH is skipped, endpoint and metadata alike, when something else
    already has it bound. Home Assistant's built-in mcp_server registers no GET
    on the endpoint and no metadata at all, so serving either half would answer
    a client's discovery from this integration while its actual traffic went
    elsewhere, sending it to this integration's authorization server for a token
    the other one will reject. MCP_HTTP_PATH is always served.

    A reload updates the view the router already holds instead of registering a
    second one, which would sit behind the first and never be reached.
    """
    if (endpoint := hass.data.get(REGISTERED_ENDPOINT)) is not None:
        endpoint.server = server
        endpoint.native_auth_enabled = native_auth_enabled
        return

    contested = mcp_path_is_contested(hass)
    endpoint_paths = [MCP_HTTP_PATH] if contested else [MCP_PATH, MCP_HTTP_PATH]
    endpoint = MCPEndpointView(hass, server, native_auth_enabled, paths=endpoint_paths)
    metadata_paths = [f"{RESOURCE_METADATA_PREFIX}{path}" for path in endpoint_paths]

    router = hass.http.app.router
    before = set(router.routes())
    hass.http.register_view(MCPProtectedResourceMetadataView(hass))
    hass.http.register_view(MCPSubpathProtectedResourceMetadataView(hass, paths=metadata_paths))
    hass.http.register_view(endpoint)

    hass.data[REGISTERED_ROUTES] = {route for route in router.routes() if route not in before}
    hass.data[REGISTERED_ENDPOINT] = endpoint

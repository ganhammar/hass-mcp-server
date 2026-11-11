"""HTTP transport for MCP server.

The endpoint does not validate the Origin header, although the Streamable HTTP
transport says a server MUST, to stop DNS rebinding. Rebinding lets a web page
send requests to this server under the page's own origin; the browser attaches
only the credentials it holds for that origin. The endpoint accepts nothing but a
bearer token in the Authorization header, which a browser never sends on its
own, so a page that reaches it this way has nothing to authenticate with and
gets the 401 any anonymous request gets. Home Assistant's own API takes the same
position. A check would also refuse real clients: a page served from Home
Assistant's own address sends an Origin equal to the Host, exactly as a
rebinding page does, and telling the two apart needs the instance's URLs, which
most installs never configure. Validate Origin before anything else once the
endpoint accepts a credential a browser sends by itself, such as a cookie.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from aiohttp import hdrs, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.ban import process_success_login, process_wrong_login
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .access import carries_server_scope, grant_from_scopes
from .caller import Caller, acting_as
from .completions import complete
from .const import (
    DEFAULT_SERVER_NAME,
    DOMAIN,
    EVENT_TOOL_CALLED,
    ISSUE_OIDC_AUDIENCE_UNSUPPORTED,
    MCP_HTTP_PATH,
    MCP_PATH,
    OIDC_PROVIDER_MIN_AUDIENCE_VERSION,
    OIDC_PROVIDER_RELEASES_URL,
    RESOURCE_METADATA_PREFIX,
    SERVER_INSTRUCTIONS,
    VERSION,
)
from .json_utils import dumps
from .prompts import InvalidPromptRequest, PromptNotAvailable, get_prompt, get_prompts
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    STATELESS_VERSIONS,
    ProtocolError,
    Revision,
    capabilities,
    check_batch,
    classify,
    finish_result,
    method_not_found,
    routing_headers,
)
from .resources import (
    ResourceNotAvailable,
    ResourceNotFound,
    get_resource_templates,
    get_resources,
    read_resource,
)
from .secret_keys import SECRET_MASK, is_secret_key
from .tools import (
    TOOLS,
    InvalidToolRequest,
    ToolNotAvailable,
    call_tool,
    get_tool_schemas,
    tool_is_read_only,
)

_LOGGER = logging.getLogger(__name__)

# hass.data keys holding the routes this integration registered and the endpoint
# view serving them. They sit outside hass.data[DOMAIN] on purpose:
# async_unload_entry clears that, while routes stay bound until Home Assistant
# restarts and a reload has to find the view the router already holds.
REGISTERED_ROUTES = f"{DOMAIN}_routes"
REGISTERED_ENDPOINT = f"{DOMAIN}_endpoint_view"

# How a bearer token was validated.
AUTH_METHOD_OIDC = "oidc"
AUTH_METHOD_NATIVE = "native"

# Outcome of a tools/call, as the audit log and event report it.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_INVALID = "invalid"

# Tool arguments audited by key only. A config flow step names its fields freely,
# and any of them may carry a credential (a calendar URL with a token in it), so
# no key name can be trusted to mark the ones that do.
_VALUE_MASKED_ARGUMENTS = {"continue_config_flow": "user_input"}

# Roughly how many characters of a call's arguments the audit log and event carry.
ARGUMENTS_PREVIEW_CHARS = 500
# The tool name comes straight from the request, so it is cut to this before it is
# logged or fired. Every registered name is far shorter.
TOOL_NAME_PREVIEW_CHARS = 100
# The client ID is cut the same way. A client that signs in with a Client ID
# Metadata Document is named by that document's URL, which the client chooses.
CLIENT_ID_PREVIEW_CHARS = 200


@dataclass(frozen=True)
class ValidatedToken:
    """A bearer token that passed validation.

    ``sub`` is the Home Assistant user id the token was issued for, on both paths:
    the paired OIDC provider signs the authorizing user's id as the subject, and a
    Long-Lived Access Token belongs to a user directly. ``client_id`` and
    ``scopes`` come from an OAuth token's claims; a Long-Lived Access Token has
    neither, and is never ``limited_by_scopes``.
    """

    sub: str
    auth_method: str
    client_id: str | None = None
    scopes: frozenset[str] = frozenset()
    limited_by_scopes: bool = False


def _token_client_id(claims: dict[str, Any]) -> str | None:
    """Return the OAuth client an access token was issued to, or None.

    RFC 9068 names it in ``client_id``. Providers that predate that claim name it
    in ``azp`` when the token is bound to a resource, and that is used when
    ``client_id`` is absent.
    """
    for claim in ("client_id", "azp"):
        value = claims.get(claim)
        if isinstance(value, str) and value:
            return value
    return None


def _token_scopes(claims: dict[str, Any]) -> frozenset[str]:
    """Return the scopes an access token grants.

    RFC 9068 carries them in ``scope`` as one space-separated string. A list of
    strings is read as well. Anything else grants nothing.
    """
    scope = claims.get("scope")
    if isinstance(scope, str):
        return frozenset(scope.split())
    if isinstance(scope, list):
        return frozenset(item for item in scope if isinstance(item, str) and item)
    return frozenset()


def mask_secrets(value: Any) -> Any:
    """Return a copy of ``value`` with the value of every secret-named key masked.

    Objects are walked at any depth, including objects inside lists. Anything that
    is not an object or a list is returned as is.
    """
    if isinstance(value, dict):
        return {
            key: SECRET_MASK if is_secret_key(key) else mask_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


def mask_tool_arguments(tool: Any, arguments: Any) -> Any:
    """Return ``arguments`` as the audit trail may carry them for ``tool``."""
    masked_key = _VALUE_MASKED_ARGUMENTS.get(tool) if isinstance(tool, str) else None
    if masked_key and isinstance(arguments, dict) and isinstance(arguments.get(masked_key), dict):
        arguments = {**arguments, masked_key: dict.fromkeys(arguments[masked_key], SECRET_MASK)}
    return mask_secrets(arguments)


def truncate_arguments(value: Any, limit: int = ARGUMENTS_PREVIEW_CHARS) -> Any:
    """Shorten ``value`` so its JSON stays near ``limit`` characters, keeping its shape.

    Keys and scalars spend a shared character budget in document order. A string
    longer than what is left is cut and marked with how much was dropped, and once
    the budget is spent the remaining entries of an object or list are replaced by
    a single marker counting them. A value that already fits comes back unchanged.
    """
    if len(dumps(value)) <= limit:
        return value

    remaining = limit

    def shorten(item: Any) -> Any:
        """Return ``item`` cut down to what is left of the budget."""
        nonlocal remaining
        if isinstance(item, dict):
            remaining -= 2
            result: dict[Any, Any] = {}
            entries = list(item.items())
            for index, (key, child) in enumerate(entries):
                if remaining <= 0:
                    result["(truncated)"] = f"{len(entries) - index} more keys"
                    break
                remaining -= len(str(key)) + 4
                result[key] = shorten(child)
            return result
        if isinstance(item, list):
            remaining -= 2
            children: list[Any] = []
            for index, child in enumerate(item):
                if remaining <= 0:
                    children.append(f"(truncated: {len(item) - index} more items)")
                    break
                remaining -= 1
                children.append(shorten(child))
            return children
        if isinstance(item, str):
            keep = max(remaining, 0)
            remaining -= len(item) + 2
            if len(item) > keep:
                return f"{item[:keep]}...(+{len(item) - keep} chars)"
            return item
        remaining -= len(dumps(item))
        return item

    return shorten(value)


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


def _protocol_error(err: ProtocolError, msg_id: Any) -> dict[str, Any]:
    """Build the JSON-RPC error response for a ProtocolError."""
    return {"jsonrpc": "2.0", "error": err.error(), "id": msg_id}


def _in_band(code: int, message: str) -> ProtocolError:
    """Return the error a request ends in that ships with 200, as JSON-RPC errors do."""
    return ProtocolError(code, message, status=200)


def _message_id(message: Any) -> Any:
    """Return the id of a JSON-RPC message, or None when it has none to give."""
    return message.get("id") if isinstance(message, dict) else None


def _is_response(message: dict[str, Any]) -> bool:
    """Return True when a JSON-RPC message is a response rather than a request."""
    return "result" in message or "error" in message


def _accepts_expected_audience(validate_access_token: Any) -> bool:
    """Return True when the provider's validator takes the expected_audience keyword.

    The signature decides, not a failed call: a TypeError raised inside a validator
    that does take the keyword is a provider bug and must not be read as an old
    provider. A validator that cannot be introspected is assumed to take it, so a
    mismatch there fails the token rather than skipping the audience check.
    """
    try:
        parameters = inspect.signature(validate_access_token).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == "expected_audience" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _logged_client(caller: Caller) -> tuple[str | None, str]:
    """Return the caller's client ID cut for the audit trail, and how the log names it.

    The client ID is the caller's text, so it is always logged as a JSON string. A
    Long-Lived Access Token has no client.
    """
    if caller.client_id is None:
        return None, ""
    client_id = caller.client_id[:CLIENT_ID_PREVIEW_CHARS]
    return client_id, f", client {dumps(client_id)}"


def _forbidden(description: str) -> web.Response:
    """Build the 403 for a valid token whose user may not use this endpoint.

    The error code and challenge follow RFC 6750 section 3.1: the token is fine,
    but what it grants is not enough for this resource.
    """
    return web.json_response(
        {"error": "insufficient_scope", "error_description": description},
        status=403,
        headers={
            "WWW-Authenticate": (
                f'Bearer error="insufficient_scope", error_description="{description}"'
            )
        },
    )


def _get_issuer(request: web.Request) -> str | None:
    """Get the OIDC issuer URL from the request, or None if unavailable."""
    try:
        from custom_components.oidc_provider.token_validator import (
            get_issuer_from_request,
        )

        return get_issuer_from_request(request)
    except ImportError:
        return None


def _provider_describes(hass: HomeAssistant) -> bool:
    """Return whether the installed OIDC provider has collected this server's description.

    The provider asks the user what an application may do here only once it has
    collected the description in oidc_provider.py, and from then on settles the access
    of every session for this resource, also one that began before. A provider that
    predates descriptions has no resources module, and one that is not set up has
    collected nothing, so both answer False.

    The answer depends on nothing in the request. The resource a token was issued
    for, and the audience it is checked against, come from the Host and
    X-Forwarded-Host headers the client sends; a client that steers them to a
    resource the provider does not recognise skips the choice, and must not get a
    token this server treats as unlimited for it.

    One case stays open, and no client can bring it about: when the provider fails
    to collect the description in a run, such as when describing times out, a token
    without a scope of this server is not limited in that run. A token whose user
    chose None everywhere carries none, so it gets what the options allow.
    """
    try:
        from custom_components.oidc_provider.resources import described_resources
    except ImportError:
        return False
    try:
        return any(
            getattr(described, "domain", None) == DOMAIN for described in described_resources(hass)
        )
    except Exception:  # noqa: BLE001 - a provider that cannot answer has described nothing
        _LOGGER.debug("Could not ask the OIDC provider what it has collected", exc_info=True)
        return False


def _get_protected_resource_metadata(base_url: str, resource_path: str) -> dict[str, Any]:
    """Generate OAuth 2.0 Protected Resource Metadata (RFC 9728).

    Only openid is advertised. The provider refuses an authorization request
    without it, and grants this server's own scopes from what the user chooses on
    the consent screen whatever the client asks for, so listing them would only
    have clients request scopes that change nothing.
    """
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
        *,
        native_auth_enabled: bool = False,
        paths: list[str] | None = None,
        server_name: str = DEFAULT_SERVER_NAME,
        require_admin: bool = True,
        stateless_protocol: bool = True,
    ) -> None:
        """Initialize the MCP endpoint, optionally narrowing the paths served.

        The options are keyword-only so a stray positional argument, such as
        a Mock where a server object used to go, fails here rather than
        quietly becoming a truthy auth flag.
        """
        self.hass = hass
        self.native_auth_enabled = native_auth_enabled
        self.server_name = server_name
        self.require_admin = require_admin
        self.stateless_protocol = stateless_protocol
        # (user id, reason) pairs already warned about, so a client that keeps
        # retrying with a refused account leaves one warning rather than one per call.
        self._reported_refusals: set[tuple[str, str]] = set()
        self._audience_fallback_reported = False
        if paths is not None:
            self.url, self.extra_urls = paths[0], list(paths[1:])

    @staticmethod
    def _bearer_token(request: web.Request) -> str | None:
        """The bearer token the request presented, or None when it presented none."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        return auth_header[7:]  # Remove "Bearer " prefix

    def _report_audience_fallback(self) -> None:
        """Flag that tokens are validated without an audience check.

        The warning and the repair are raised on the first fallback only; later
        ones leave a debug line.
        """
        if self._audience_fallback_reported:
            _LOGGER.debug("OIDC provider does not support audience validation")
            return
        self._audience_fallback_reported = True
        _LOGGER.warning(
            "The installed OIDC provider does not support audience validation, so access "
            "tokens are validated without checking the audience: a token issued for another "
            "resource on this issuer is accepted. Upgrade hass-oidc-server to %s or newer",
            OIDC_PROVIDER_MIN_AUDIENCE_VERSION,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_OIDC_AUDIENCE_UNSUPPORTED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_OIDC_AUDIENCE_UNSUPPORTED,
            translation_placeholders={"minimum_version": OIDC_PROVIDER_MIN_AUDIENCE_VERSION},
            learn_more_url=OIDC_PROVIDER_RELEASES_URL,
        )

    async def _validate_token(self, request: web.Request) -> ValidatedToken | None:
        """Validate the bearer token via OIDC (if available) then native HA auth.

        An OIDC token is only accepted when it names a subject, since the subject
        is what resolves the caller to a Home Assistant user.
        """
        token = self._bearer_token(request)
        if token is None:
            return None

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
            if _accepts_expected_audience(validate_access_token):
                result = validate_access_token(
                    self.hass, token, expected_issuer, expected_audience=expected_audience
                )
            else:
                # The installed provider predates resource-aware validation, so the
                # token is validated without an audience check.
                self._report_audience_fallback()
                result = validate_access_token(self.hass, token, expected_issuer)
            sub = result.get("sub") if isinstance(result, dict) else None
            if isinstance(sub, str) and sub:
                scopes = _token_scopes(result)
                # A token is limited by its scopes once it carries one of this
                # server's, or once the provider asks users what to grant here. A
                # token from a provider that does neither works as before.
                limited = carries_server_scope(scopes) or _provider_describes(self.hass)
                return ValidatedToken(
                    sub=sub,
                    auth_method=AUTH_METHOD_OIDC,
                    client_id=_token_client_id(result),
                    scopes=scopes,
                    limited_by_scopes=limited,
                )
        except ImportError as e:
            _LOGGER.debug("OIDC provider not available: %s", e)

        # 2. Fall back to native HA auth (Long-Lived Access Tokens)
        if self.native_auth_enabled:
            refresh_token = self.hass.auth.async_validate_access_token(token)
            if refresh_token is not None:
                return ValidatedToken(sub=refresh_token.user.id, auth_method=AUTH_METHOD_NATIVE)

        return None

    async def _authenticate(self, request: web.Request) -> Caller | web.Response:
        """Resolve the request to an authorized caller, or the response refusing it.

        A token that does not validate gets the 401 challenge, and counts toward
        Home Assistant's login attempt protection when the request presented one.
        A request with no token does not count: clients probe unauthenticated to
        discover how to authenticate. A request that is authorized clears the
        address's count, as Home Assistant's own login flow does, so a client that
        presents each expired token once before refreshing is never banned. A
        valid token whose user no longer exists or is deactivated is refused, and
        so is a non-administrator while require_admin is on: Home Assistant itself
        restricts most of what the tools do to administrators.

        A valid OAuth token that is limited by its scopes and grants nothing here
        gets the 401 as well, so the client refreshes it: the provider gives a
        session from before this server described its access the access it names
        for those, and a client whose refreshed token still grants nothing goes
        back to the user to choose. Such a token is valid, so it counts neither
        as a failed login nor as an authorized request, the same as the 403.
        """
        token = await self._validate_token(request)
        if token is None:
            if self._bearer_token(request) is not None:
                await self._record_failed_login(request)
            return self._unauthorized(request)

        user = await self.hass.auth.async_get_user(token.sub)
        if user is None:
            reason, description = "no such user", "The token's user does not exist"
        elif not user.is_active:
            reason, description = "user is deactivated", "The token's user is deactivated"
        elif self.require_admin and not user.is_admin:
            reason = "user is not an administrator"
            description = "This MCP server requires a Home Assistant administrator account"
        elif token.limited_by_scopes and not grant_from_scopes(token.scopes).grants_anything:
            _LOGGER.debug(
                "Answering a token of user %s that grants no access here with 401", token.sub
            )
            return self._unauthorized(request, grants_nothing=True)
        else:
            self._record_successful_login(request)
            return Caller(
                user_id=token.sub,
                auth_method=token.auth_method,
                user=user,
                client_id=token.client_id,
                scopes=token.scopes,
                limited_by_scopes=token.limited_by_scopes,
            )

        first_time = (token.sub, reason) not in self._reported_refusals
        self._reported_refusals.add((token.sub, reason))
        _LOGGER.log(
            logging.WARNING if first_time else logging.DEBUG,
            "Refused MCP request from user %s (%s auth): %s",
            token.sub,
            token.auth_method,
            reason,
        )
        return _forbidden(description)

    @staticmethod
    async def _record_failed_login(request: web.Request) -> None:
        """Count a rejected bearer token toward Home Assistant's IP ban protection.

        The view validates tokens itself, so Home Assistant's own auth middleware
        never sees the failure. Recording it must not change the 401 the caller
        gets, whatever state the http component or the connection is in.
        """
        try:
            await process_wrong_login(request)
        except Exception:
            _LOGGER.debug("Could not record the failed login attempt", exc_info=True)

    @staticmethod
    def _record_successful_login(request: web.Request) -> None:
        """Clear the address's failed login count after an authorized request.

        Only the login flow and the websocket handler reset the count in Home
        Assistant itself; a bearer request never does. Without this, a client whose
        hourly refresh presents the expired token once first would reach the ban
        threshold on expired tokens alone.
        """
        try:
            process_success_login(request)
        except Exception:
            _LOGGER.debug("Could not reset the failed login attempt count", exc_info=True)

    def _unauthorized(self, request: web.Request, *, grants_nothing: bool = False) -> web.Response:
        """Build the 401 that points a client at this resource's metadata.

        The resource_metadata URL is the RFC 9728 path-suffixed form. HA Core
        serves its own metadata at the bare /.well-known/oauth-protected-resource
        and wins that route by registering first, so a client that falls back to
        the root path is told the authorization server is HA itself. The challenge
        names openid as the scope to ask for, since the provider refuses an
        authorization request without it.

        ``grants_nothing`` is for a valid token that grants no access here. Its
        challenge carries error="invalid_token" (RFC 6750), which has the client
        refresh the token before it asks the user again.
        """
        description = "Invalid or missing token"
        base_url = _get_issuer(request)
        if base_url is not None:
            resource_metadata_url = (
                f"{base_url}{RESOURCE_METADATA_PREFIX}{_resource_path(request.path)}"
            )
            www_authenticate = (
                f'Bearer realm="MCP Server", resource_metadata="{resource_metadata_url}",'
                ' scope="openid"'
            )
        else:
            www_authenticate = 'Bearer realm="Home Assistant MCP Server"'
        if grants_nothing:
            description = "The token grants this application no access to this MCP server"
            www_authenticate += f', error="invalid_token", error_description="{description}"'

        return web.json_response(
            {"error": "invalid_token", "error_description": description},
            status=401,
            headers={"WWW-Authenticate": www_authenticate},
        )

    async def get(self, request: web.Request) -> web.Response:
        """Answer GET probes, which clients use to discover how to authenticate.

        This endpoint carries no SSE stream, so an authenticated GET is Method
        Not Allowed, which every revision allows a server without one to answer.
        An unauthenticated one still gets the challenge, because a bare 405 leaves
        the client with no pointer to the resource metadata.
        """
        if not _integration_loaded(self.hass):
            return _service_unavailable()

        caller = await self._authenticate(request)
        if isinstance(caller, web.Response):
            return caller

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

        caller = await self._authenticate(request)
        if isinstance(caller, web.Response):
            return caller

        try:
            # Parse JSON-RPC message. Only decoding failures belong here —
            # aiohttp's own exceptions (an oversized body, a client that hung
            # up mid-read) carry the right status already and must propagate.
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as e:
            _LOGGER.error("Could not parse MCP request body: %s", e)
            return web.json_response(
                _jsonrpc_error(PARSE_ERROR, f"Parse error: {str(e)}"),
                status=400,
            )

        _LOGGER.debug("Received MCP request: %s", body)
        headers = routing_headers(request.headers.items())

        if isinstance(body, list):
            try:
                check_batch(body, headers)
            except ProtocolError as err:
                return web.json_response(_protocol_error(err, None), status=err.status)
            return await self._answer_batch(body, caller, headers)

        try:
            # Process the message directly
            response_data = await self._handle_message(body, caller, headers)

            if response_data is None:
                # Notification - return 202 Accepted
                return web.Response(status=202)

            # Return JSON response
            return web.json_response(response_data)

        except ProtocolError as err:
            return web.json_response(_protocol_error(err, _message_id(body)), status=err.status)
        except Exception as e:
            _LOGGER.error("Error handling MCP request: %s", e, exc_info=True)
            # A JSON-RPC error is a complete response, so it ships with 200.
            # Under a non-2xx status intermediary proxies substitute their own
            # error page and the client never sees what actually went wrong.
            return web.json_response(
                _jsonrpc_error(INTERNAL_ERROR, f"Internal error: {str(e)}", _message_id(body))
            )

    async def _answer_batch(
        self, batch: list[Any], caller: Caller, headers: dict[str, list[str]]
    ) -> web.Response:
        """Answer a JSON-RPC batch, one message at a time.

        Each message is answered as it would be on its own, and a failure in one is
        that message's error rather than the batch's. A batch of notifications only
        is accepted with 202.
        """
        responses = []
        for message in batch:
            try:
                response = await self._handle_message(message, caller, headers)
            except ProtocolError as err:
                response = _protocol_error(err, _message_id(message))
            except Exception as e:
                _LOGGER.error("Error handling MCP request: %s", e, exc_info=True)
                response = _jsonrpc_error(
                    INTERNAL_ERROR, f"Internal error: {str(e)}", _message_id(message)
                )
            if response is not None:
                responses.append(response)
        if not responses:
            return web.Response(status=202)
        return web.json_response(responses)

    async def _handle_message(
        self, message: Any, caller: Caller, headers: dict[str, list[str]] | None = None
    ) -> dict[str, Any] | None:
        """Handle a JSON-RPC message on behalf of an authorized caller.

        Returns the response, or None for a message that gets none: a notification
        is accepted and nothing more, since no notification a client sends asks this
        server to do anything, and so is a response, since the server sends no
        requests for one to answer. An error that ships with 200 is returned as the
        response; one whose HTTP status says something, such as a message outside
        any revision this server has, is raised as ProtocolError before anything runs.

        The caller is the current caller for the whole message, so listing and
        calling see the same access: what tools/list offers is what tools/call
        accepts, and the same holds for resources and prompts. A tool handler finds
        the caller through current_caller(), and what it does as the caller shares
        its context with the audit event.
        """
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and "id" in message and _is_response(message):
                return None
            raise ProtocolError(INVALID_REQUEST, "Invalid Request: not a JSON-RPC request")
        revision = classify(message, headers or {}, stateless=self.stateless_protocol)
        if "id" not in message:
            return None
        try:
            with acting_as(caller) as context:
                result = await self._dispatch(message, revision, caller, context)
        except ProtocolError as err:
            if err.status != 200:
                raise
            return _protocol_error(err, message["id"])
        server_info = {"name": self.server_name, "version": VERSION}
        return {
            "jsonrpc": "2.0",
            "result": finish_result(revision, message["method"], result, server_info),
            "id": message["id"],
        }

    async def _dispatch(
        self, message: dict[str, Any], revision: Revision, caller: Caller, context: Context
    ) -> dict[str, Any]:
        """Return the result of a JSON-RPC request while its caller is the current one.

        An error the request ends in is raised as ProtocolError, which ships with 200
        unless the revision says otherwise.
        """
        method = message["method"]
        params = message.get("params", {})
        if method not in revision.methods:
            raise method_not_found(revision, method)

        if method == "initialize":
            return {
                "protocolVersion": revision.version,
                "capabilities": capabilities(revision),
                "serverInfo": {"name": self.server_name, "version": VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            }

        if method == "server/discover":
            return {
                "supportedVersions": list(STATELESS_VERSIONS),
                "capabilities": capabilities(revision),
                "instructions": SERVER_INSTRUCTIONS,
            }

        if method == "ping":
            return {}

        if method == "tools/list":
            return {"tools": await self._get_tools()}

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})

            try:
                result = await self._call_tool(name, arguments)
            except InvalidToolRequest as err:
                self._audit_tool_call(
                    caller,
                    name,
                    arguments,
                    OUTCOME_INVALID,
                    withheld=isinstance(err, ToolNotAvailable),
                    context=context,
                )
                raise _in_band(INVALID_PARAMS, str(err)) from err
            except Exception:
                self._audit_tool_call(caller, name, arguments, OUTCOME_ERROR, context=context)
                raise

            is_error = isinstance(result, dict) and result.get("isError") is True
            self._audit_tool_call(
                caller,
                name,
                arguments,
                OUTCOME_ERROR if is_error else OUTCOME_OK,
                context=context,
            )
            return result

        if method == "resources/list":
            return get_resources()

        if method == "resources/templates/list":
            return get_resource_templates()

        if method == "resources/read":
            uri = params.get("uri", "")
            try:
                contents = await read_resource(self.hass, uri)
            except ResourceNotAvailable as err:
                self._audit_refused_read(caller, method, uri)
                raise _in_band(INVALID_PARAMS, str(err)) from err
            except ResourceNotFound as err:
                raise _in_band(revision.resource_not_found, str(err)) from err
            return {"contents": contents}

        if method == "prompts/list":
            return {"prompts": get_prompts()}

        if method == "prompts/get":
            name = params.get("name", "")
            arguments = params.get("arguments", {})

            try:
                return await get_prompt(self.hass, name, arguments)
            except InvalidPromptRequest as err:
                if isinstance(err, PromptNotAvailable):
                    self._audit_refused_read(caller, method, name)
                raise _in_band(INVALID_PARAMS, str(err)) from err

        if method == "completion/complete":
            ref = params.get("ref", {})
            argument = params.get("argument", {})
            return {"completion": await complete(self.hass, ref, argument)}

        # A method a revision lists that no branch above answers.
        raise method_not_found(revision, method)

    async def _get_tools(self) -> list[dict[str, Any]]:
        """Get available tools."""
        return get_tool_schemas(self.hass)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool by name."""
        return await call_tool(self.hass, name, arguments)

    def _audit_refused_read(self, caller: Caller, method: str, name: Any) -> None:
        """Log a resources/read or prompts/get the caller's access does not include.

        It is logged at INFO with the outcome invalid, as a call to a withheld tool
        is, but fires no event: reading changes nothing, and the client was told
        which access it needs. The URI or prompt name is the caller's text, so it is
        logged as a JSON string and cut like a tool name.
        """
        _client_id, via = _logged_client(caller)
        _LOGGER.info(
            "MCP %s by user %s (%s auth%s): %s -> %s, not granted",
            method,
            caller.user_id,
            caller.auth_method,
            via,
            dumps(str(name)[:TOOL_NAME_PREVIEW_CHARS]),
            OUTCOME_INVALID,
        )

    def _audit_tool_call(
        self,
        caller: Caller,
        name: Any,
        arguments: Any,
        outcome: str,
        *,
        withheld: bool = False,
        context: Context | None = None,
    ) -> None:
        """Record who called a tool, through which client, with what, and how it ended.

        A tool that can change something is logged at INFO and fired as
        EVENT_TOOL_CALLED under ``context``, the one the call's own changes were
        recorded under, or a new one with the caller as its user. A read-only
        tool is logged at DEBUG only: an event per read would flood the recorder.
        A call to a tool the options or the caller's access withhold is recorded
        like a write whatever the tool does, since the client was never offered
        it. Secret-named argument values are masked and the arguments shortened
        in both.
        """
        read_only = tool_is_read_only(name) and not withheld
        level = logging.DEBUG if read_only else logging.INFO
        if read_only and not _LOGGER.isEnabledFor(level):
            return

        tool = str(name)[:TOOL_NAME_PREVIEW_CHARS]
        # A registered name is known to be plain. Anything else is the caller's
        # text and is logged as a JSON string so a newline in it cannot forge a
        # log line.
        logged_tool = tool if tool in TOOLS else dumps(tool)
        client_id, via = _logged_client(caller)
        summary = truncate_arguments(mask_tool_arguments(name, arguments))
        _LOGGER.log(
            level,
            "MCP tool call by user %s (%s auth%s): %s -> %s, arguments %s",
            caller.user_id,
            caller.auth_method,
            via,
            logged_tool,
            outcome,
            dumps(summary),
        )
        if read_only:
            return

        # The call has already run by now, so a bus that refuses the event is
        # logged rather than turning the call's result into an internal error.
        try:
            self.hass.bus.async_fire(
                EVENT_TOOL_CALLED,
                {
                    "tool": tool,
                    "user_id": caller.user_id,
                    "auth_method": caller.auth_method,
                    "client_id": client_id,
                    "outcome": outcome,
                    "arguments": summary,
                },
                context=context if context is not None else Context(user_id=caller.user_id),
            )
        except Exception:  # noqa: BLE001 - the audit event must not fail the call
            _LOGGER.exception("Could not fire %s for tool %s", EVENT_TOOL_CALLED, tool)


def register_mcp_views(
    hass: HomeAssistant,
    native_auth_enabled: bool,
    server_name: str = DEFAULT_SERVER_NAME,
    require_admin: bool = True,
    stateless_protocol: bool = True,
) -> None:
    """Register the HTTP views this integration serves.

    MCP_PATH is skipped, endpoint and metadata alike, when something else
    already has it bound. Home Assistant's built-in mcp_server registers no GET
    on the endpoint and no metadata at all, so serving either half would answer
    a client's discovery from this integration while its actual traffic went
    elsewhere, sending it to this integration's authorization server for a token
    the other one will reject. MCP_HTTP_PATH is always served.

    A reload updates the view the router already holds instead of registering a
    second one, which would sit behind the first and never be reached. The name a
    client sees, the administrator requirement and whether the stateless revisions
    are served are refreshed the same way.
    """
    if (endpoint := hass.data.get(REGISTERED_ENDPOINT)) is not None:
        endpoint.native_auth_enabled = native_auth_enabled
        endpoint.server_name = server_name
        endpoint.require_admin = require_admin
        endpoint.stateless_protocol = stateless_protocol
        return

    contested = mcp_path_is_contested(hass)
    endpoint_paths = [MCP_HTTP_PATH] if contested else [MCP_PATH, MCP_HTTP_PATH]
    endpoint = MCPEndpointView(
        hass,
        native_auth_enabled=native_auth_enabled,
        paths=endpoint_paths,
        server_name=server_name,
        require_admin=require_admin,
        stateless_protocol=stateless_protocol,
    )
    metadata_paths = [f"{RESOURCE_METADATA_PREFIX}{path}" for path in endpoint_paths]

    router = hass.http.app.router
    before = set(router.routes())
    hass.http.register_view(MCPProtectedResourceMetadataView(hass))
    hass.http.register_view(MCPSubpathProtectedResourceMetadataView(hass, paths=metadata_paths))
    hass.http.register_view(endpoint)

    hass.data[REGISTERED_ROUTES] = {route for route in router.routes() if route not in before}
    hass.data[REGISTERED_ENDPOINT] = endpoint

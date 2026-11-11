"""The MCP protocol revisions this server speaks, and what differs between them.

This module is the one place that knows the revisions. The transport asks it which
revision a message is in, which methods that revision has, what to declare as
capabilities, and how to finish a result for it; handlers produce the same payload
whatever the revision.

Two eras share the endpoint. A handshake revision (2025-11-25 and earlier) opens
with ``initialize``, after which the client names the negotiated revision in the
``MCP-Protocol-Version`` header. A stateless revision (2026-07-28) has no handshake:
every request names its revision and the client's capabilities in ``params._meta``,
and the HTTP headers mirror the revision, the method and the name it acts on. A
request whose ``_meta`` claims a revision is in the era of the revision it names:
the stateless era for 2026-07-28 and anything later, the handshake era for
anything earlier. A request without a claim is in the handshake era. That is how
the TypeScript SDK classifies them.

Nothing is remembered between requests: the server keeps no sessions, so a
handshake-era request is served in the revision its header names.

Origin is not validated here or in the transport; the module docstring of
``http.py`` says why and what would change that.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Resource not found in the handshake revisions. 2026-07-28 uses INVALID_PARAMS.
RESOURCE_NOT_FOUND = -32002

# Error codes the MCP specification defines from 2026-07-28.
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

# Keys a stateless request carries in params._meta, and the one its result carries.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# Request headers, as lowercase names.
HEADER_PROTOCOL_VERSION = "mcp-protocol-version"
HEADER_METHOD = "mcp-method"
HEADER_NAME = "mcp-name"
_ROUTING_HEADERS = (HEADER_PROTOCOL_VERSION, HEADER_METHOD, HEADER_NAME)

# The body field each named method mirrors into Mcp-Name.
_NAMED_BY = MappingProxyType({"tools/call": "name", "prompts/get": "name", "resources/read": "uri"})

_HEADER_SAFE = re.compile(r"[\x20-\x7E\t]*")
_BASE64_SENTINEL = re.compile(r"=\?base64\?(?P<payload>.*)\?=")

# Methods every revision serves.
_SHARED_METHODS = frozenset(
    {
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/templates/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
        "completion/complete",
    }
)
_HANDSHAKE_METHODS = _SHARED_METHODS | {"initialize", "ping"}
_STATELESS_METHODS = _SHARED_METHODS | {"server/discover"}


@dataclass(frozen=True)
class CacheHint:
    """How long a client may treat a result as fresh, and who may share the cached copy."""

    ttl_ms: int
    scope: str


# Every list depends on who asks: the caller's scopes decide what is listed, and so
# do the integration's options, which a reload changes without a notification this
# server could send. For up to five minutes a client may therefore show a list that
# no longer matches what the caller may do; every call is checked regardless. A
# read returns live state and is stale at once. What server/discover returns is the
# same for every caller and changes only with an upgrade or a new server name.
_LIST_HINT = CacheHint(ttl_ms=300_000, scope="private")
_STATELESS_CACHE_HINTS = MappingProxyType(
    {
        "server/discover": CacheHint(ttl_ms=3_600_000, scope="public"),
        "tools/list": _LIST_HINT,
        "prompts/list": _LIST_HINT,
        "resources/list": _LIST_HINT,
        "resources/templates/list": _LIST_HINT,
        "resources/read": CacheHint(ttl_ms=0, scope="private"),
    }
)


@dataclass(frozen=True)
class Revision:
    """One protocol revision, described by what this server does differently in it."""

    version: str
    stateless: bool = False
    batching: bool = False
    declares_completions: bool = True
    resource_not_found: int = RESOURCE_NOT_FOUND
    methods: frozenset[str] = _HANDSHAKE_METHODS
    cache_hints: Mapping[str, CacheHint] = field(default_factory=dict)


# Newest first. Adding a revision means adding it here and describing how it
# differs; see AGENTS.md.
REVISIONS: tuple[Revision, ...] = (
    Revision(
        "2026-07-28",
        stateless=True,
        resource_not_found=INVALID_PARAMS,
        methods=_STATELESS_METHODS,
        cache_hints=_STATELESS_CACHE_HINTS,
    ),
    Revision("2025-11-25"),
    Revision("2025-06-18"),
    Revision("2025-03-26", batching=True),
    Revision("2024-11-05", batching=True, declares_completions=False),
)
_BY_VERSION = {revision.version: revision for revision in REVISIONS}

STATELESS_VERSIONS = tuple(r.version for r in REVISIONS if r.stateless)
HANDSHAKE_VERSIONS = tuple(r.version for r in REVISIONS if not r.stateless)

# The first revision without a handshake. A claimed revision that sorts at or after
# it is in the stateless era, known or not, and one that sorts before it is in the
# handshake era.
_FIRST_STATELESS = STATELESS_VERSIONS[-1]

# The revision of a handshake-era request without the MCP-Protocol-Version header,
# as the transport specification says to assume from 2025-06-18 on.
HEADERLESS = _BY_VERSION["2025-03-26"]


class ProtocolError(Exception):
    """A message the transport answers with a JSON-RPC error before any handler runs."""

    def __init__(self, code: int, message: str, *, status: int = 400, data: Any = None) -> None:
        """Hold the JSON-RPC error and the HTTP status it is sent with."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.data = data

    def error(self) -> dict[str, Any]:
        """Return the JSON-RPC error object."""
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


def negotiate(requested: Any) -> Revision:
    """Return the revision to answer an initialize request with.

    The requested revision when it is a handshake revision this server has, and the
    newest handshake revision otherwise, as the lifecycle specification says.
    """
    revision = _BY_VERSION.get(requested) if isinstance(requested, str) else None
    if revision is not None and not revision.stateless:
        return revision
    return _BY_VERSION[HANDSHAKE_VERSIONS[0]]


def capabilities(revision: Revision) -> dict[str, Any]:
    """Return the capabilities this server declares in a revision."""
    declared: dict[str, Any] = {"tools": {}, "resources": {}, "prompts": {}}
    if revision.declares_completions:
        declared["completions"] = {}
    return declared


def method_not_found(revision: Revision, method: Any) -> ProtocolError:
    """Return the error for a method the revision does not have.

    A stateless revision answers it with 404, which tells a client probing for that
    era that the server speaks it. A handshake revision ships it with 200 like any
    other JSON-RPC error.
    """
    status = 404 if revision.stateless else 200
    return ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}", status=status)


def finish_result(
    revision: Revision, method: str, result: dict[str, Any], server_info: dict[str, Any]
) -> dict[str, Any]:
    """Return a handler's result as the revision defines it.

    A handshake revision gets the result as is. A stateless one gets it marked
    complete, with the server named in _meta and, for a result clients may cache,
    how long it stays fresh and who may share it. The handler's dict is not changed.
    """
    if not revision.stateless:
        return result
    meta = result.get("_meta")
    finished = {
        **result,
        "resultType": "complete",
        "_meta": {**(meta if isinstance(meta, dict) else {}), META_SERVER_INFO: server_info},
    }
    hint = revision.cache_hints.get(method)
    if hint is not None:
        finished["ttlMs"] = hint.ttl_ms
        finished["cacheScope"] = hint.scope
    return finished


def routing_headers(pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """Collect the protocol's request headers by lowercase name, keeping every copy."""
    headers: dict[str, list[str]] = {}
    for name, value in pairs:
        key = name.lower()
        if key in _ROUTING_HEADERS:
            headers.setdefault(key, []).append(value)
    return headers


def classify(message: Mapping[str, Any], headers: Mapping[str, list[str]]) -> Revision:
    """Return the revision a single JSON-RPC message is in, or raise ProtocolError.

    A message whose params._meta claims a revision is in the era of the revision it
    names. A stateless-era message is checked as that era requires: the envelope,
    then the headers against the body, then whether the revision is one this server
    has.

    Any other message is in the handshake era. Initialize negotiates its revision
    from its params and ignores the header, which names the revision negotiated
    before. Every other message is in the revision its claim names, which the
    MCP-Protocol-Version header must agree with when present, or else in the one
    the header names.
    """
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    claimed = None
    if isinstance(meta, dict) and META_PROTOCOL_VERSION in meta:
        claimed = meta[META_PROTOCOL_VERSION]
        if not isinstance(claimed, str):
            raise ProtocolError(INVALID_PARAMS, f"{META_PROTOCOL_VERSION} must be a string")
    claims_stateless = claimed is not None and claimed >= _FIRST_STATELESS

    for name in _ROUTING_HEADERS:
        if len(headers.get(name, ())) > 1:
            raise _header_mismatch(f"The {name} header appears more than once")
    header_version = _header(headers, HEADER_PROTOCOL_VERSION)

    if claims_stateless:
        return _classify_stateless(message, meta, headers, header_version)
    if message.get("method") == "initialize":
        return negotiate(params.get("protocolVersion") if isinstance(params, dict) else None)
    if claimed is not None:
        if header_version is not None and header_version != claimed:
            raise _header_mismatch(
                "The MCP-Protocol-Version header does not match the protocol version in _meta"
            )
        header_version = claimed
    if header_version is None:
        return HEADERLESS
    revision = _BY_VERSION.get(header_version)
    if revision is None:
        raise _unsupported(header_version, HANDSHAKE_VERSIONS)
    if revision.stateless:
        raise ProtocolError(
            INVALID_PARAMS,
            f"A request in revision {header_version} must carry {META_PROTOCOL_VERSION} and "
            f"{META_CLIENT_CAPABILITIES} in params._meta",
        )
    return revision


def check_batch(batch: list[Any], headers: Mapping[str, list[str]]) -> None:
    """Raise ProtocolError unless the request's revision accepts this JSON-RPC batch.

    Only 2025-03-26 and 2024-11-05 accept batches. A batch is in the revision its
    header names, or in the one assumed without it. Initialize must not be part of
    one, and a request that names its revision in _meta is never batched.
    """
    if not batch:
        raise ProtocolError(INVALID_REQUEST, "Invalid Request: the batch is empty")
    version = _header(headers, HEADER_PROTOCOL_VERSION)
    revision = HEADERLESS if version is None else _BY_VERSION.get(version)
    if revision is None:
        raise _unsupported(version, HANDSHAKE_VERSIONS)
    if not revision.batching:
        raise ProtocolError(
            INVALID_REQUEST, f"Revision {revision.version} does not accept JSON-RPC batches"
        )
    for message in batch:
        if not isinstance(message, dict):
            continue
        if message.get("method") == "initialize":
            raise ProtocolError(INVALID_REQUEST, "initialize must not be part of a JSON-RPC batch")
        params = message.get("params")
        meta = params.get("_meta") if isinstance(params, dict) else None
        if isinstance(meta, dict) and META_PROTOCOL_VERSION in meta:
            raise ProtocolError(
                INVALID_REQUEST, "A request that names its revision in _meta cannot be batched"
            )


def _classify_stateless(
    message: Mapping[str, Any],
    meta: Mapping[str, Any],
    headers: Mapping[str, list[str]],
    header_version: str | None,
) -> Revision:
    """Check a message that claims a stateless revision, and return that revision.

    A request must carry the client's capabilities and every header the revision
    requires. The revision defines no header rules for a notification, so one is
    checked only against the headers it does carry, as the TypeScript SDK does.
    """
    version = meta[META_PROTOCOL_VERSION]
    is_request = "id" in message
    if is_request and META_CLIENT_CAPABILITIES not in meta:
        raise ProtocolError(INVALID_PARAMS, f"params._meta is missing {META_CLIENT_CAPABILITIES}")

    _require_match(
        "MCP-Protocol-Version", header_version, version, "protocol version in _meta", is_request
    )
    method = message.get("method")
    _require_match("Mcp-Method", _header(headers, HEADER_METHOD), method, "method", is_request)
    field_name = _NAMED_BY.get(method) if isinstance(method, str) else None
    if field_name is not None:
        value = message["params"].get(field_name)
        if isinstance(value, str):
            raw = _header(headers, HEADER_NAME)
            name = _decoded(raw)
            if raw is not None and name is None:
                raise _header_mismatch("The Mcp-Name header is not valid base64 of UTF-8 text")
            _require_match("Mcp-Name", name, value, field_name, is_request)

    revision = _BY_VERSION.get(version)
    if revision is None:
        raise _unsupported(version, STATELESS_VERSIONS)
    return revision


def _require_match(
    header: str, value: str | None, expected: Any, field_name: str, required: bool
) -> None:
    """Raise HeaderMismatch unless a header equals the body's value.

    An absent header is a mismatch only when it is ``required``.
    """
    if value is None:
        if required:
            raise _header_mismatch(f"The {header} header is missing")
        return
    if value != expected:
        raise _header_mismatch(f"The {header} header does not match the {field_name} in the body")


def _header(headers: Mapping[str, list[str]], name: str) -> str | None:
    """Return the one value of a header, or None when it is absent.

    A value a header cannot carry plainly is refused: one outside visible ASCII is
    sent base64-encoded, so it can only come from a client that did not encode it.
    """
    values = headers.get(name)
    if not values:
        return None
    value = values[0]
    if _HEADER_SAFE.fullmatch(value) is None:
        raise _header_mismatch(f"The {name} header carries characters a header cannot carry")
    return value


def _decoded(value: str | None) -> str | None:
    """Return a header value with the =?base64?...?= encoding undone.

    A value in that form whose payload is not canonical base64 of UTF-8 text
    decodes to None, so it cannot match anything in the body.
    """
    if value is None:
        return None
    match = _BASE64_SENTINEL.fullmatch(value)
    if match is None:
        return value
    payload = match.group("payload")
    try:
        raw = base64.b64decode(payload, validate=True)
        if base64.b64encode(raw).decode("ascii") != payload:
            return None
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _header_mismatch(message: str) -> ProtocolError:
    """Return the HeaderMismatch error."""
    return ProtocolError(HEADER_MISMATCH, message)


def _unsupported(requested: str, supported: tuple[str, ...]) -> ProtocolError:
    """Return the UnsupportedProtocolVersion error listing the revisions that would work.

    The list names the revisions of the era the client asked in: the stateless ones
    for a request claiming a stateless revision, the handshake ones for any other,
    so a client retrying with an entry from it is served.
    """
    return ProtocolError(
        UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        data={"supported": list(supported), "requested": requested},
    )

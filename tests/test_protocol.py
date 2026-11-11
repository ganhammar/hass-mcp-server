"""Tests for the protocol revisions: the protocol module, and the endpoint serving them.

The endpoint tests drive MCPEndpointView.post with a stand-in request, so they run in
the unit suite; tests/test_e2e.py repeats the exchanges through the real view.
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp_clients import (
    CLAUDE_AI_DISCOVER,
    CLAUDE_AI_INITIALIZE,
    CLAUDE_CODE_DISCOVER,
    INITIALIZED,
    STATELESS,
    envelope,
    stateless,
    stateless_headers,
    toolbox,
)

from custom_components.mcp_server_http_transport.const import (
    DOMAIN,
    EVENT_TOOL_CALLED,
    MCP_PATH,
    SERVER_INSTRUCTIONS,
    VERSION,
)
from custom_components.mcp_server_http_transport.http import (
    MCPEndpointView,
    ValidatedToken,
)
from custom_components.mcp_server_http_transport.protocol import (
    HANDSHAKE_VERSIONS,
    HEADER_MISMATCH,
    HEADERLESS,
    INVALID_PARAMS,
    INVALID_REQUEST,
    META_SERVER_INFO,
    METHOD_NOT_FOUND,
    RESOURCE_NOT_FOUND,
    REVISIONS,
    STATELESS_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    ProtocolError,
    Revision,
    capabilities,
    check_batch,
    classify,
    finish_result,
    method_not_found,
    negotiate,
    routing_headers,
)
from custom_components.mcp_server_http_transport.tools import TOOLS

TOKEN = ValidatedToken(sub="user123", auth_method="native")
SERVER_INFO = {"name": "home-assistant-mcp-server", "version": VERSION}


def headers_of(pairs: dict) -> dict[str, list[str]]:
    return routing_headers(pairs.items())


def _code(err: pytest.ExceptionInfo) -> int:
    return err.value.code


# --- The protocol module -------------------------------------------------------------


class TestRevisions:
    def test_revisions_are_listed_newest_first_without_repeats(self):
        versions = [r.version for r in REVISIONS]
        assert versions == sorted(versions, reverse=True)
        assert len(set(versions)) == len(versions)

    def test_the_eras_split_at_2026_07_28(self):
        assert STATELESS_VERSIONS == ("2026-07-28",)
        assert HANDSHAKE_VERSIONS == ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

    def test_a_request_without_the_header_is_assumed_to_be_2025_03_26(self):
        assert HEADERLESS.version == "2025-03-26"

    def test_only_the_revisions_that_define_batches_accept_them(self):
        assert [r.version for r in REVISIONS if r.batching] == ["2025-03-26", "2024-11-05"]

    def test_only_the_stateless_revision_uses_invalid_params_for_a_missing_resource(self):
        codes = {r.version: r.resource_not_found for r in REVISIONS}
        assert codes == {
            "2026-07-28": INVALID_PARAMS,
            "2025-11-25": RESOURCE_NOT_FOUND,
            "2025-06-18": RESOURCE_NOT_FOUND,
            "2025-03-26": RESOURCE_NOT_FOUND,
            "2024-11-05": RESOURCE_NOT_FOUND,
        }

    def test_the_stateless_revision_has_discover_and_no_handshake_or_ping(self):
        stateless_revision, *handshake = REVISIONS
        assert "server/discover" in stateless_revision.methods
        assert not {"initialize", "ping"} & stateless_revision.methods
        for revision in handshake:
            assert {"initialize", "ping"} <= revision.methods
            assert "server/discover" not in revision.methods


class TestNegotiate:
    @pytest.mark.parametrize("version", HANDSHAKE_VERSIONS)
    def test_a_handshake_revision_is_answered_with_itself(self, version):
        assert negotiate(version).version == version

    @pytest.mark.parametrize("requested", ["2026-07-28", "2099-01-01", "1.0.0", None, 20241105])
    def test_anything_else_is_answered_with_the_newest_handshake_revision(self, requested):
        assert negotiate(requested).version == "2025-11-25"


class TestCapabilities:
    def test_2024_11_05_declares_no_completions_capability_it_did_not_define(self):
        assert capabilities(negotiate("2024-11-05")) == {
            "tools": {},
            "resources": {},
            "prompts": {},
        }

    @pytest.mark.parametrize("version", ["2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"])
    def test_later_revisions_declare_completions(self, version):
        revision = next(r for r in REVISIONS if r.version == version)
        assert capabilities(revision) == {
            "tools": {},
            "resources": {},
            "prompts": {},
            "completions": {},
        }


class TestMethodNotFound:
    def test_a_stateless_revision_answers_404(self):
        err = method_not_found(REVISIONS[0], "ping")
        assert (err.code, err.status, err.message) == (
            METHOD_NOT_FOUND,
            404,
            "Method not found: ping",
        )

    def test_a_handshake_revision_answers_200(self):
        assert method_not_found(HEADERLESS, "x").status == 200


class TestFinishResult:
    def test_a_handshake_revision_gets_the_result_as_is(self):
        result = {"tools": []}
        assert finish_result(HEADERLESS, "tools/list", result, SERVER_INFO) is result

    def test_a_stateless_result_is_complete_and_names_the_server(self):
        result = {"content": [], "isError": True, "_meta": {"com.example/x": 1}}

        finished = finish_result(REVISIONS[0], "tools/call", result, SERVER_INFO)

        assert finished == {
            "content": [],
            "isError": True,
            "resultType": "complete",
            "_meta": {"com.example/x": 1, META_SERVER_INFO: SERVER_INFO},
        }
        assert result == {"content": [], "isError": True, "_meta": {"com.example/x": 1}}

    @pytest.mark.parametrize(
        ("method", "ttl", "scope"),
        [
            ("server/discover", 3_600_000, "public"),
            ("tools/list", 300_000, "private"),
            ("prompts/list", 300_000, "private"),
            ("resources/list", 300_000, "private"),
            ("resources/templates/list", 300_000, "private"),
            ("resources/read", 0, "private"),
        ],
    )
    def test_a_cacheable_result_says_how_long_and_for_whom(self, method, ttl, scope):
        finished = finish_result(REVISIONS[0], method, {}, SERVER_INFO)
        assert (finished["ttlMs"], finished["cacheScope"]) == (ttl, scope)

    @pytest.mark.parametrize("method", ["tools/call", "prompts/get", "completion/complete"])
    def test_other_results_carry_no_cache_hint(self, method):
        finished = finish_result(REVISIONS[0], method, {}, SERVER_INFO)
        assert "ttlMs" not in finished and "cacheScope" not in finished


class TestRoutingHeaders:
    def test_names_are_matched_without_case_and_every_copy_is_kept(self):
        pairs = [
            ("mcp-protocol-version", "a"),
            ("MCP-Protocol-Version", "b"),
            ("Mcp-Method", "tools/list"),
            ("Authorization", "Bearer x"),
        ]
        assert routing_headers(pairs) == {
            "mcp-protocol-version": ["a", "b"],
            "mcp-method": ["tools/list"],
        }


class TestClassifyHandshake:
    def test_a_request_without_the_header_is_in_2025_03_26(self):
        assert classify({"method": "tools/list", "id": 1}, {}) is HEADERLESS

    @pytest.mark.parametrize("version", HANDSHAKE_VERSIONS)
    def test_a_request_is_in_the_revision_its_header_names(self, version):
        headers = headers_of({"MCP-Protocol-Version": version})
        assert classify({"method": "tools/list", "id": 1}, headers).version == version

    def test_an_empty_meta_does_not_make_a_request_stateless(self):
        message = {"method": "tools/list", "id": 1, "params": {"_meta": {}}}
        assert classify(message, {}) is HEADERLESS

    def test_an_unknown_header_is_refused_with_the_handshake_revisions(self):
        with pytest.raises(ProtocolError) as err:
            classify({"method": "tools/list", "id": 1}, headers_of({"MCP-Protocol-Version": "x"}))
        assert (_code(err), err.value.status) == (UNSUPPORTED_PROTOCOL_VERSION, 400)
        assert err.value.data == {"supported": list(HANDSHAKE_VERSIONS), "requested": "x"}

    def test_a_stateless_header_without_the_envelope_is_invalid_params(self):
        with pytest.raises(ProtocolError) as err:
            classify(
                {"method": "tools/list", "id": 1}, headers_of({"MCP-Protocol-Version": STATELESS})
            )
        assert (_code(err), err.value.status) == (INVALID_PARAMS, 400)

    def test_initialize_negotiates_from_its_params_and_ignores_the_header(self):
        message = {"method": "initialize", "id": 1, "params": {"protocolVersion": "2025-06-18"}}
        headers = headers_of({"MCP-Protocol-Version": STATELESS})
        assert classify(message, headers).version == "2025-06-18"

    def test_initialize_without_params_gets_the_newest_handshake_revision(self):
        assert classify({"method": "initialize", "id": 1}, {}).version == "2025-11-25"

    def test_a_header_sent_twice_is_a_mismatch(self):
        headers = routing_headers([("MCP-Protocol-Version", "2025-11-25")] * 2)
        with pytest.raises(ProtocolError) as err:
            classify({"method": "tools/list", "id": 1}, headers)
        assert _code(err) == HEADER_MISMATCH

    def test_a_header_value_outside_visible_ascii_is_a_mismatch(self):
        headers = headers_of({"MCP-Protocol-Version": "2025-11-25\x00"})
        with pytest.raises(ProtocolError) as err:
            classify({"method": "tools/list", "id": 1}, headers)
        assert _code(err) == HEADER_MISMATCH


class TestClassifyStateless:
    def test_a_well_formed_request_is_in_its_revision(self):
        message = stateless("tools/call", name="get_state")
        assert classify(message, headers_of(stateless_headers(message))) is REVISIONS[0]

    def test_a_version_that_is_not_a_string_is_invalid_params(self):
        message = stateless("tools/list")
        message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = 2026
        with pytest.raises(ProtocolError) as err:
            classify(message, {})
        assert (_code(err), err.value.status) == (INVALID_PARAMS, 400)

    def test_missing_client_capabilities_are_invalid_params(self):
        message = stateless("tools/list")
        del message["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
        with pytest.raises(ProtocolError) as err:
            classify(message, headers_of(stateless_headers(message)))
        assert (_code(err), err.value.status) == (INVALID_PARAMS, 400)
        assert "clientCapabilities" in err.value.message

    @pytest.mark.parametrize(
        ("change", "expected"),
        [
            ({"MCP-Protocol-Version": None}, "The MCP-Protocol-Version header is missing"),
            ({"MCP-Protocol-Version": "2025-11-25"}, "MCP-Protocol-Version header does not match"),
            ({"Mcp-Method": None}, "The Mcp-Method header is missing"),
            ({"Mcp-Method": "tools/list"}, "Mcp-Method header does not match"),
            ({"Mcp-Name": None}, "The Mcp-Name header is missing"),
            ({"Mcp-Name": "set_state"}, "Mcp-Name header does not match the name"),
            ({"Mcp-Name": "=?base64?!!?="}, "not valid base64"),
            ({"Mcp-Name": "=?base64?QR==?="}, "not valid base64"),
            ({"Mcp-Name": "=?base64?/w==?="}, "not valid base64"),
        ],
    )
    def test_headers_must_mirror_the_body(self, change, expected):
        message = stateless("tools/call", name="get_state")
        pairs = {k: v for k, v in {**stateless_headers(message), **change}.items() if v is not None}
        with pytest.raises(ProtocolError) as err:
            classify(message, headers_of(pairs))
        assert (_code(err), err.value.status) == (HEADER_MISMATCH, 400)
        assert expected in err.value.message

    def test_a_base64_name_is_decoded_before_it_is_compared(self):
        message = stateless("prompts/get", name="état")
        pairs = {**stateless_headers(message), "Mcp-Name": "=?base64?w6l0YXQ=?="}
        assert classify(message, headers_of(pairs)) is REVISIONS[0]

    def test_resources_read_mirrors_its_uri(self):
        message = stateless("resources/read", uri="hass://config")
        assert classify(message, headers_of(stateless_headers(message))) is REVISIONS[0]
        pairs = {**stateless_headers(message), "Mcp-Name": "hass://areas"}
        with pytest.raises(ProtocolError) as err:
            classify(message, headers_of(pairs))
        assert "uri" in err.value.message

    def test_a_call_without_a_name_is_left_to_the_handler(self):
        message = stateless("tools/call")
        assert classify(message, headers_of(stateless_headers(message))) is REVISIONS[0]

    @pytest.mark.parametrize("version", ["2099-01-01", "2025-11-25"])
    def test_a_revision_this_server_does_not_serve_statelessly_is_unsupported(self, version):
        message = stateless("tools/list")
        message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = version
        with pytest.raises(ProtocolError) as err:
            classify(message, headers_of(stateless_headers(message)))
        assert (_code(err), err.value.status) == (UNSUPPORTED_PROTOCOL_VERSION, 400)
        assert err.value.error() == {
            "code": UNSUPPORTED_PROTOCOL_VERSION,
            "message": "Unsupported protocol version",
            "data": {"supported": ["2026-07-28"], "requested": version},
        }


class TestCheckBatch:
    BATCH = [{"jsonrpc": "2.0", "method": "tools/list", "id": 1}]

    @pytest.mark.parametrize("pairs", [{}, {"MCP-Protocol-Version": "2024-11-05"}])
    def test_a_batch_in_a_revision_that_has_them_passes(self, pairs):
        check_batch([*self.BATCH, "not a message"], headers_of(pairs))

    def test_an_empty_batch_is_invalid(self):
        with pytest.raises(ProtocolError) as err:
            check_batch([], {})
        assert (_code(err), err.value.status) == (INVALID_REQUEST, 400)

    @pytest.mark.parametrize("version", ["2025-06-18", "2025-11-25", "2026-07-28"])
    def test_a_revision_without_batches_refuses_one(self, version):
        with pytest.raises(ProtocolError) as err:
            check_batch(self.BATCH, headers_of({"MCP-Protocol-Version": version}))
        assert (_code(err), err.value.status) == (INVALID_REQUEST, 400)
        assert version in err.value.message

    def test_an_unknown_header_is_unsupported(self):
        with pytest.raises(ProtocolError) as err:
            check_batch(self.BATCH, headers_of({"MCP-Protocol-Version": "x"}))
        assert _code(err) == UNSUPPORTED_PROTOCOL_VERSION

    def test_initialize_is_never_batched(self):
        with pytest.raises(ProtocolError) as err:
            check_batch([{"jsonrpc": "2.0", "method": "initialize", "id": 1}], {})
        assert "initialize" in err.value.message

    def test_a_stateless_request_is_never_batched(self):
        with pytest.raises(ProtocolError) as err:
            check_batch([stateless("tools/list")], {})
        assert _code(err) == INVALID_REQUEST


def test_a_protocol_error_without_data_leaves_data_out():
    assert ProtocolError(INVALID_REQUEST, "x").error() == {"code": INVALID_REQUEST, "message": "x"}


# --- The endpoint --------------------------------------------------------------------


@pytest.fixture
def hass(list_every_tool):
    hass = Mock()
    hass.data = {DOMAIN: {"entry_id": Mock()}}
    hass.auth.async_get_user = AsyncMock(
        return_value=Mock(id="user123", is_active=True, is_admin=True)
    )
    hass.states.get = Mock(return_value=None)
    list_every_tool(hass)
    return hass


@pytest.fixture
def view(hass):
    return MCPEndpointView(hass)


@pytest.fixture
def get_state():
    """get_state as a stub: what is under test is how the call reaches it and comes back."""
    handler = AsyncMock(return_value={"content": [{"type": "text", "text": "above_horizon"}]})
    with patch.dict(TOOLS, {"get_state": {**TOOLS["get_state"], "handler": handler}}):
        yield handler


async def post(view, body, headers=None, *, token=TOKEN):
    """POST a body with a bearer token and return (status, decoded body or None)."""
    request = Mock(path=MCP_PATH, host="ha.example.com")
    request.headers = {"Authorization": "Bearer t", **(headers or {})}
    request.json = AsyncMock(return_value=body)
    with patch.object(view, "_validate_token", return_value=token):
        response = await view.post(request)
    body = json.loads(response.body) if response.body else None
    return response.status, body


async def post_stateless(view, message):
    return await post(view, message, stateless_headers(message))


class TestCapturedSequences:
    """The handshakes the three Anthropic clients were captured sending, replayed."""

    @staticmethod
    def _assert_discovered(status, body, msg_id):
        assert status == 200
        assert body["id"] == msg_id
        result = body["result"]
        assert result["resultType"] == "complete"
        assert result["supportedVersions"] == ["2026-07-28"]
        assert result["capabilities"] == {
            "tools": {},
            "resources": {},
            "prompts": {},
            "completions": {},
        }
        assert result["instructions"] == SERVER_INSTRUCTIONS
        assert result["_meta"] == {META_SERVER_INFO: SERVER_INFO}
        assert (result["ttlMs"], result["cacheScope"]) == (3_600_000, "public")

    async def _replay_handshake(self, view, discover, initialize, then):
        """The captured order: the probe, the 2025-11-25 handshake, then the requests."""
        status, body = await post_stateless(view, discover)
        self._assert_discovered(status, body, discover["id"])

        status, body = await post(view, initialize)
        assert status == 200
        assert body["result"]["protocolVersion"] == "2025-11-25"
        assert "completions" in body["result"]["capabilities"]
        assert "resultType" not in body["result"]

        negotiated = {"MCP-Protocol-Version": "2025-11-25"}
        assert await post(view, INITIALIZED, negotiated) == (202, None)
        answers = []
        for message in then:
            status, body = await post(view, message, negotiated)
            assert status == 200
            assert "resultType" not in body["result"]
            answers.append(body["result"])
        return answers

    async def test_claude_ai(self, view):
        listed = [
            {"jsonrpc": "2.0", "id": 2, "method": method, "params": {"_meta": {}}}
            for method in ("tools/list", "prompts/list", "resources/list")
        ]

        tools, prompts, resources = await self._replay_handshake(
            view, CLAUDE_AI_DISCOVER, CLAUDE_AI_INITIALIZE, listed
        )

        assert len(tools["tools"]) == len(TOOLS)
        assert prompts["prompts"]
        assert resources["resources"]

    async def test_anthropictoolbox(self, view):
        listed = [{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": {}}}]

        (tools,) = await self._replay_handshake(
            view, toolbox(CLAUDE_AI_DISCOVER), toolbox(CLAUDE_AI_INITIALIZE), listed
        )

        assert len(tools["tools"]) == len(TOOLS)

    async def test_claude_code(self, view, get_state):
        initialize = {
            **CLAUDE_AI_INITIALIZE,
            "params": {**CLAUDE_AI_INITIALIZE["params"], "clientInfo": {"name": "claude-code"}},
        }
        requests = [
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_state", "arguments": {"entity_id": "sun.sun"}},
            },
        ]

        resources, call = await self._replay_handshake(
            view, CLAUDE_CODE_DISCOVER, initialize, requests
        )

        assert resources["resources"]
        assert call == {"content": [{"type": "text", "text": "above_horizon"}]}
        get_state.assert_awaited_once()

    @pytest.mark.parametrize(
        "discover", [CLAUDE_AI_DISCOVER, CLAUDE_CODE_DISCOVER, toolbox(CLAUDE_AI_DISCOVER)]
    )
    async def test_after_the_probe_a_turn_is_one_request(self, view, discover, get_state):
        """A client that keeps the 2026-07-28 it found sends the call straight away."""
        meta = discover["params"]["_meta"]
        call = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_state", "arguments": {"entity_id": "sun.sun"}, "_meta": meta},
        }

        status, body = await post_stateless(view, call)

        assert status == 200
        assert body["result"]["resultType"] == "complete"
        assert body["result"]["_meta"] == {META_SERVER_INFO: SERVER_INFO}
        assert "isError" not in body["result"]

    async def test_a_probe_without_the_headers_is_a_mismatch_the_client_falls_back_from(self, view):
        status, body = await post(view, CLAUDE_AI_DISCOVER)

        assert status == 400
        assert body["error"]["code"] == HEADER_MISMATCH
        assert body["id"] == CLAUDE_AI_DISCOVER["id"]
        status, body = await post(view, CLAUDE_AI_INITIALIZE)
        assert (status, body["result"]["protocolVersion"]) == (200, "2025-11-25")


class TestHandshake:
    @pytest.mark.parametrize("version", HANDSHAKE_VERSIONS)
    async def test_each_revision_is_answered_with_itself(self, view, version):
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": version, "capabilities": {}},
        }

        status, body = await post(view, message)

        assert status == 200
        result = body["result"]
        assert result["protocolVersion"] == version
        assert result["serverInfo"] == SERVER_INFO
        assert result["instructions"] == SERVER_INSTRUCTIONS
        assert ("completions" in result["capabilities"]) is (version != "2024-11-05")
        assert "resultType" not in result and "_meta" not in result

    async def test_a_2024_11_05_client_gets_what_it_always_got(self, view):
        message = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        message["params"] = {"protocolVersion": "2024-11-05"}

        _, body = await post(view, message)

        assert body == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": SERVER_INFO,
                "instructions": SERVER_INSTRUCTIONS,
            },
        }

    async def test_a_request_for_2026_07_28_gets_the_newest_handshake_revision(self, view):
        message = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        message["params"] = {"protocolVersion": STATELESS}

        _, body = await post(view, message)

        assert body["result"]["protocolVersion"] == "2025-11-25"

    @pytest.mark.parametrize("version", [None, *HANDSHAKE_VERSIONS])
    async def test_ping_is_answered_with_an_empty_result(self, view, version):
        headers = {"MCP-Protocol-Version": version} if version else {}

        status, body = await post(view, {"jsonrpc": "2.0", "id": 7, "method": "ping"}, headers)

        assert (status, body) == (200, {"jsonrpc": "2.0", "id": 7, "result": {}})

    async def test_discover_without_the_envelope_is_an_unknown_method_as_before(self, view):
        status, body = await post(view, {"jsonrpc": "2.0", "id": 1, "method": "server/discover"})

        assert status == 200
        assert body["error"]["code"] == METHOD_NOT_FOUND

    async def test_an_unsupported_header_is_400(self, view):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

        status, body = await post(view, message, {"MCP-Protocol-Version": "2024-01-01"})

        assert status == 400
        assert body["id"] == 1
        assert body["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
        assert body["error"]["data"]["supported"] == list(HANDSHAKE_VERSIONS)

    async def test_a_stateless_header_without_the_envelope_is_400(self, view):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

        status, body = await post(view, message, {"MCP-Protocol-Version": STATELESS})

        assert (status, body["error"]["code"]) == (400, INVALID_PARAMS)

    async def test_a_notification_with_an_unsupported_header_is_400_without_an_id(self, view):
        status, body = await post(view, INITIALIZED, {"MCP-Protocol-Version": "x"})

        assert status == 400
        assert body["id"] is None

    async def test_a_missing_resource_is_32002(self, view, hass):
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "resources/read",
            "params": {"uri": "hass://entity/light.gone"},
        }

        status, body = await post(view, message, {"MCP-Protocol-Version": "2025-11-25"})

        assert status == 200
        assert body["error"] == {"code": -32002, "message": "Entity light.gone not found"}

    async def test_resource_templates_are_listed(self, view):
        message = {"jsonrpc": "2.0", "id": 1, "method": "resources/templates/list"}

        _, body = await post(view, message)

        uris = [t["uriTemplate"] for t in body["result"]["resourceTemplates"]]
        assert "hass://entity/{entity_id}" in uris
        assert set(body["result"]) == {"resourceTemplates"}


class TestStateless:
    async def test_discover_answers_without_a_handshake(self, view):
        status, body = await post_stateless(view, stateless("server/discover"))

        TestCapturedSequences._assert_discovered(status, body, 1)

    async def test_tools_list_is_cacheable_per_caller_and_lists_what_a_handshake_lists(self, view):
        _, legacy = await post(view, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        _, first = await post_stateless(view, stateless("tools/list"))
        _, second = await post_stateless(view, stateless("tools/list"))

        result = first["result"]
        assert result["tools"] == legacy["result"]["tools"]
        assert (result["ttlMs"], result["cacheScope"]) == (300_000, "private")
        assert result["resultType"] == "complete"
        assert first == second

    async def test_tools_are_listed_in_registration_order(self, view):
        _, body = await post_stateless(view, stateless("tools/list"))

        assert [tool["name"] for tool in body["result"]["tools"]] == list(TOOLS)

    @pytest.mark.parametrize(
        "method", ["prompts/list", "resources/list", "resources/templates/list"]
    )
    async def test_every_list_is_cacheable_per_caller(self, view, method):
        _, body = await post_stateless(view, stateless(method))

        assert (body["result"]["ttlMs"], body["result"]["cacheScope"]) == (300_000, "private")

    async def test_a_read_is_stale_at_once(self, view):
        contents = [{"uri": "hass://config", "mimeType": "application/json", "text": "{}"}]
        with patch(
            "custom_components.mcp_server_http_transport.http.read_resource",
            AsyncMock(return_value=contents),
        ):
            _, body = await post_stateless(view, stateless("resources/read", uri="hass://config"))

        result = body["result"]
        assert result["contents"][0]["uri"] == "hass://config"
        assert (result["ttlMs"], result["cacheScope"]) == (0, "private")

    async def test_a_missing_resource_is_invalid_params(self, view):
        message = stateless("resources/read", uri="hass://nowhere")

        status, body = await post_stateless(view, message)

        assert status == 200
        assert body["error"] == {
            "code": INVALID_PARAMS,
            "message": "Unknown resource: hass://nowhere",
        }

    async def test_a_call_is_complete_and_audited_as_in_a_handshake(self, view, hass):
        handler = AsyncMock(return_value={"content": [{"type": "text", "text": "done"}]})
        tool = {**TOOLS["call_service"], "handler": handler}
        message = stateless(
            "tools/call", name="call_service", arguments={"domain": "light", "service": "x"}
        )

        with patch.dict(TOOLS, {"call_service": tool}):
            status, body = await post_stateless(view, message)

        assert status == 200
        assert body["result"] == {
            "content": [{"type": "text", "text": "done"}],
            "resultType": "complete",
            "_meta": {META_SERVER_INFO: SERVER_INFO},
        }
        (event,) = [c for c in hass.bus.async_fire.call_args_list if c.args[0] == EVENT_TOOL_CALLED]
        assert event.args[1]["outcome"] == "ok"
        assert event.args[1]["tool"] == "call_service"

    async def test_a_call_the_caller_got_wrong_is_still_invalid_params(self, view, hass):
        message = stateless("tools/call", name="no_such_tool", arguments={})

        status, body = await post_stateless(view, message)

        assert (status, body["error"]["code"]) == (200, INVALID_PARAMS)
        (event,) = [c for c in hass.bus.async_fire.call_args_list if c.args[0] == EVENT_TOOL_CALLED]
        assert event.args[1]["outcome"] == "invalid"

    async def test_a_limited_token_is_listed_only_what_it_was_granted(self, view):
        token = ValidatedToken(
            sub="user123",
            auth_method="oidc",
            client_id="app",
            scopes=frozenset({"openid", "mcp:entities:read"}),
            limited_by_scopes=True,
        )
        message = stateless("tools/list")

        _, legacy = await post(
            view, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=token
        )
        _, body = await post(view, message, stateless_headers(message), token=token)

        names = [tool["name"] for tool in body["result"]["tools"]]
        assert names == [tool["name"] for tool in legacy["result"]["tools"]]
        assert "get_state" in names
        assert "call_service" not in names

    async def test_prompts_and_completions_answer(self, view):
        _, listed = await post_stateless(view, stateless("prompts/list"))
        name = listed["result"]["prompts"][0]["name"]
        _, completed = await post_stateless(
            view,
            stateless(
                "completion/complete",
                ref={"type": "ref/prompt", "name": name},
                argument={"name": "x", "value": ""},
            ),
        )

        assert completed["result"]["resultType"] == "complete"
        assert "completion" in completed["result"]

    async def test_a_prompt_the_caller_got_wrong_is_invalid_params(self, view):
        message = stateless("prompts/get", name="no_such_prompt")

        status, body = await post_stateless(view, message)

        assert (status, body["error"]["code"]) == (200, INVALID_PARAMS)

    @pytest.mark.parametrize("method", ["initialize", "ping", "logging/setLevel", "x/y"])
    async def test_a_removed_or_unknown_method_is_404(self, view, method):
        status, body = await post_stateless(view, stateless(method))

        assert status == 404
        assert body["error"] == {"code": METHOD_NOT_FOUND, "message": f"Method not found: {method}"}
        assert body["id"] == 1

    async def test_a_header_mismatch_is_400_with_the_request_id(self, view):
        message = stateless("tools/list", msg_id="abc")

        status, body = await post(view, message, {**stateless_headers(message), "Mcp-Method": "x"})

        assert status == 400
        assert body["id"] == "abc"
        assert body["error"]["code"] == HEADER_MISMATCH

    async def test_an_unsupported_revision_is_400_and_never_validates_more(self, view):
        message = stateless("tools/list")
        message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"

        status, body = await post_stateless(view, message)

        assert status == 400
        assert body["error"]["data"] == {"supported": ["2026-07-28"], "requested": "2099-01-01"}

    async def test_a_notification_is_accepted(self, view):
        message = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}
        message["params"]["_meta"] = envelope()

        assert await post(view, message, stateless_headers(message)) == (202, None)

    async def test_a_method_a_revision_lists_but_nothing_answers_is_not_found(self, view):
        revision = Revision("2099-01-01", stateless=True, methods=frozenset({"x/y"}))

        with pytest.raises(ProtocolError) as err:
            await view._dispatch({"method": "x/y"}, revision, Mock(), Mock())

        assert (err.value.code, err.value.status) == (METHOD_NOT_FOUND, 404)


class TestMessageShape:
    async def test_a_response_is_accepted_and_dropped(self, view):
        message = {"jsonrpc": "2.0", "id": 1, "result": {}}

        assert await post(view, message) == (202, None)

    @pytest.mark.parametrize("body", ["text", 42, {"jsonrpc": "2.0", "id": 1}, {"method": 5}])
    async def test_anything_but_a_message_is_an_invalid_request(self, view, body):
        status, answer = await post(view, body)

        assert status == 400
        assert answer["error"]["code"] == INVALID_REQUEST


class TestBatch:
    async def test_a_batch_without_the_header_is_answered_message_by_message(self, view):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            INITIALIZED,
            {"jsonrpc": "2.0", "id": 2, "method": "nope"},
            "junk",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": {}}},
        ]

        status, body = await post(view, batch)

        assert status == 200
        assert body[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
        assert body[1]["error"]["code"] == METHOD_NOT_FOUND
        assert body[2] == {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": INVALID_REQUEST,
                "message": "Invalid Request: not a JSON-RPC request",
            },
        }
        assert body[3]["id"] == 3 and body[3]["result"]["tools"]
        assert len(body) == 4

    async def test_a_batch_of_notifications_is_accepted(self, view):
        headers = {"MCP-Protocol-Version": "2024-11-05"}

        assert await post(view, [INITIALIZED, INITIALIZED], headers) == (202, None)

    async def test_a_failing_message_does_not_take_the_batch_down(self, view):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]

        with patch.object(view, "_get_tools", AsyncMock(side_effect=RuntimeError("boom"))):
            status, body = await post(view, batch)

        assert status == 200
        assert body[0]["error"] == {"code": -32603, "message": "Internal error: boom"}
        assert body[1]["result"] == {}

    async def test_a_header_sent_twice_fails_each_message(self, view):
        request = Mock(path=MCP_PATH, host="ha.example.com")
        request.headers = Mock()
        request.headers.get = Mock(return_value=None)
        request.headers.items = Mock(
            return_value=[
                ("Authorization", "Bearer t"),
                ("MCP-Protocol-Version", "2025-03-26"),
                ("MCP-Protocol-Version", "2025-03-26"),
            ]
        )
        request.json = AsyncMock(return_value=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])

        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(request)

        assert json.loads(response.body)[0]["error"]["code"] == HEADER_MISMATCH

    @pytest.mark.parametrize(
        ("headers", "code"),
        [
            ({"MCP-Protocol-Version": "2025-11-25"}, INVALID_REQUEST),
            ({"MCP-Protocol-Version": "2023-01-01"}, UNSUPPORTED_PROTOCOL_VERSION),
        ],
    )
    async def test_a_batch_its_revision_does_not_take_is_400(self, view, headers, code):
        status, body = await post(view, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers)

        assert status == 400
        assert (body["id"], body["error"]["code"]) == (None, code)


class TestOriginIsNotValidated:
    """The endpoint takes only a bearer token, so Origin decides nothing; see http.py."""

    @staticmethod
    def _request(origin, host="hem.example.se", authorization="Bearer t"):
        request = Mock(path=MCP_PATH, host=host)
        request.headers = {"Origin": origin}
        if authorization:
            request.headers["Authorization"] = authorization
        request.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        return request

    @pytest.mark.parametrize(
        "origin", ["https://hem.example.se", "null", "http://attacker.example"]
    )
    async def test_a_request_with_a_token_is_served_whatever_its_origin(self, view, origin):
        with patch.object(view, "_validate_token", return_value=TOKEN):
            response = await view.post(self._request(origin))

        assert response.status == 200

    async def test_a_request_without_a_token_gets_the_challenge(self, view):
        response = await view.post(self._request("https://hem.example.se", authorization=None))

        assert response.status == 401
        assert "WWW-Authenticate" in response.headers


class TestFailures:
    async def test_an_unexpected_failure_is_an_internal_error_with_200(self, view):
        with patch.object(view, "_get_tools", AsyncMock(side_effect=RuntimeError("boom"))):
            status, body = await post(view, {"jsonrpc": "2.0", "id": 9, "method": "tools/list"})

        assert status == 200
        assert body == {
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": "Internal error: boom"},
            "id": 9,
        }

    @pytest.mark.parametrize(
        ("version", "code"), [("2025-11-25", RESOURCE_NOT_FOUND), (STATELESS, INVALID_PARAMS)]
    )
    async def test_a_missing_dashboard_is_a_missing_resource(self, view, version, code):
        from custom_components.mcp_server_http_transport.dashboard_manager import (
            DashboardNotFound,
        )

        message = (
            stateless("resources/read", uri="hass://dashboard/gone")
            if version == STATELESS
            else {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "hass://dashboard/gone"},
            }
        )
        headers = (
            stateless_headers(message)
            if version == STATELESS
            else {"MCP-Protocol-Version": version}
        )

        with patch(
            "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
            AsyncMock(side_effect=DashboardNotFound("Dashboard 'gone' not found")),
        ):
            status, body = await post(view, message, headers)

        assert status == 200
        assert body["error"] == {"code": code, "message": "Dashboard 'gone' not found"}

    async def test_a_dashboard_that_exists_is_read(self, view):
        with patch(
            "custom_components.mcp_server_http_transport.dashboard_manager.get_dashboard_config",
            AsyncMock(return_value={"views": []}),
        ):
            _, body = await post_stateless(
                view, stateless("resources/read", uri="hass://dashboard/home")
            )

        assert body["result"]["contents"] == [
            {"uri": "hass://dashboard/home", "mimeType": "application/json", "text": '{"views":[]}'}
        ]

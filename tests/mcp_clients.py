"""MCP requests as the Anthropic clients send them, shared by the protocol tests.

The captured requests are the ones a live instance received, in the order each client
sent them. The headers are the ones a 2026-07-28 client derives from the body, as the
TypeScript SDK those clients use does.
"""

import json

STATELESS = "2026-07-28"

# Requests captured from a live instance, in the order each client sent them.
# claude.ai's initialize capabilities were elided in the capture.
CLAUDE_AI_DISCOVER = {
    "jsonrpc": "2.0",
    "id": 530885140,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {
                "name": "Anthropic/ClaudeAI",
                "version": "1.0.0",
            },
            "io.modelcontextprotocol/clientCapabilities": {
                "extensions": {
                    "io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}
                }
            },
        }
    },
}
CLAUDE_AI_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 530885141,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "Anthropic/ClaudeAI", "version": "1.0.0"},
        "_meta": {},
    },
}
CLAUDE_CODE_DISCOVER = {
    "id": "server-discover-probe-1",
    "jsonrpc": "2.0",
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/clientCapabilities": {
                "elicitation": {"form": {}, "url": {}},
                "extensions": {},
                "roots": {"listChanged": True},
            },
            "io.modelcontextprotocol/clientInfo": {"name": "claude-code", "version": "2.1.322"},
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        }
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def toolbox(message: dict) -> dict:
    """The same message as Anthropic/Toolbox sends it."""
    copy = json.loads(json.dumps(message))
    params = copy["params"]
    if "clientInfo" in params:
        params["clientInfo"]["name"] = "Anthropic/Toolbox"
    meta = params.get("_meta", {})
    if "io.modelcontextprotocol/clientInfo" in meta:
        meta["io.modelcontextprotocol/clientInfo"]["name"] = "Anthropic/Toolbox"
    return copy


def envelope(**extra) -> dict:
    """params._meta as a 2026-07-28 request carries it."""
    return {
        "io.modelcontextprotocol/protocolVersion": STATELESS,
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
        **extra,
    }


def stateless(method: str, msg_id=1, **params) -> dict:
    """A 2026-07-28 request."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": {**params, "_meta": envelope()},
    }


def stateless_headers(message: dict) -> dict:
    """The headers a 2026-07-28 client derives from the body, as the TypeScript SDK does."""
    meta = message["params"]["_meta"]
    headers = {
        "MCP-Protocol-Version": meta["io.modelcontextprotocol/protocolVersion"],
        "Mcp-Method": message["method"],
    }
    params = message["params"]
    name = params.get("uri") if message["method"] == "resources/read" else params.get("name")
    if isinstance(name, str):
        headers["Mcp-Name"] = name
    return headers

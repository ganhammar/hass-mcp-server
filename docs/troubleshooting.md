# Troubleshooting

[Back to the README](../README.md)

## Another integration also serves /api/mcp

Home Assistant's built-in **Model Context Protocol Server** integration (`mcp_server`) serves its streamable HTTP transport on `/api/mcp`, the same path this integration uses. It has done so since Home Assistant 2025.11.

Home Assistant cannot share a path between two integrations: whichever registered it first at startup answers every request there, and the other is silently unreachable on that path. Nothing is logged, both integrations keep showing as healthy, and an MCP client connects successfully to whichever server won, so the only visible symptom is the client getting the wrong set of tools.

This integration therefore also answers on **`/api/mcp_http`**, which nothing else claims. If `/api/mcp` is already taken when this integration starts, it stays off that path entirely and serves only `/api/mcp_http`. Either way it raises a repair naming the conflict. Point your MCP client at `https://your-home-assistant/api/mcp_http`.

The repair is the reliable signal, and it is raised on every version. Failing that, look for **Model Context Protocol Server** under Settings > Devices & Services. A `405` from the probe below confirms it as well, but only from 2026.8 onwards, which is when the built-in server added its per-API paths; on 2025.11 to 2026.7 the probe returns `404` while the conflict is real, so a `404` proves nothing on its own. The `401` challenge on `/api/mcp` is no help either, because both servers use the same realm.

```bash
curl -o /dev/null -w '%{http_code}\n' https://your-home-assistant/api/mcp/assist
```

Deleting the built-in integration's config entry does not release the path on its own: Home Assistant binds HTTP routes at startup and cannot unbind them, so restart afterwards.

## Repeated failed logins and IP bans

A request to the MCP endpoint that presents a bearer token Home Assistant rejects counts as a failed login attempt, the same as a wrong password on the login page. When the [`http` integration](https://www.home-assistant.io/integrations/http/#ip_ban_enabled) has `ip_ban_enabled` on, an address that reaches `login_attempts_threshold` failures is banned, and each failure also raises a persistent notification. A request with no `Authorization` header does not count, because MCP clients probe the endpoint without a token to find out how to authenticate, and neither does a valid token that is refused for other reasons, such as a non-administrator account. An authorized request clears the address's count again, so a client that presents an expired token once before refreshing is never banned for that. Expect one warning and one "Login attempt failed" notification per connected OAuth client each time its access token expires, which is hourly with hass-oidc-server; that matches what Home Assistant does for an expired token on its own API.

## Tokens are validated without an audience check

If the installed OIDC provider (hass-oidc-server) is older than 1.5.0, it cannot bind an access token to the resource it was issued for. This integration then still accepts its tokens, but without checking the audience, so a token issued for another resource on the same Home Assistant is accepted too. It logs a warning once and raises a repair named **OIDC provider does not check the token audience**.

Update hass-oidc-server to 1.5.0 or newer and restart Home Assistant; the repair is gone after the restart unless the check is still unavailable.

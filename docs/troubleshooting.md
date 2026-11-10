# Troubleshooting

[Back to the README](../README.md)

## Adding the integration fails with "The OIDC Provider integration is required"

Without long-lived access tokens enabled, the server needs the OIDC Provider for sign-in. Install [OIDC Provider](https://github.com/ganhammar/hass-oidc-server) through HACS, restart, and add it under Settings > Devices & Services before adding MCP Server. If you only want long-lived access tokens, check **Enable native Home Assistant authentication** in the form instead.

## The client cannot connect

- **Claude on the web** connects from Claude's servers, so Home Assistant must have an HTTPS address that works from outside your network. Open that address from a phone on mobile data to check.
- **A `403`** means the token is valid but its user is not an administrator. See [Security and auditing](security.md#authorization).
- **The wrong tools show up**, or none: you may be talking to Home Assistant's built-in MCP server. Use `/api/mcp_http`, see below.

## Another integration also serves /api/mcp

Home Assistant's built-in **Model Context Protocol Server** integration (`mcp_server`) serves its streamable HTTP transport on `/api/mcp`, the same path this integration uses. It has done so since Home Assistant 2025.11.

Home Assistant cannot share a path between two integrations: whichever registered it first at startup answers every request there, and the other is silently unreachable on that path. Nothing is logged, both integrations keep showing as healthy, and an MCP client connects successfully to whichever server won, so the only visible symptom is the client getting the wrong set of tools.

This integration therefore also answers on **`/api/mcp_http`**, which nothing else claims. If `/api/mcp` is already taken when this integration starts, it stays off that path entirely and serves only `/api/mcp_http`. Either way it raises a repair naming the conflict. Point your MCP client at `https://your-home-assistant/api/mcp_http`.

The repair is the reliable signal. You can also look for **Model Context Protocol Server** under Settings > Devices & Services.

Deleting the built-in integration's config entry does not release the path on its own: Home Assistant binds HTTP routes at startup and cannot unbind them, so restart afterwards.

## Repeated failed logins and IP bans

A request to the MCP endpoint with a bearer token that Home Assistant rejects counts as a failed login attempt, the same as a wrong password on the login page. When the [`http` integration](https://www.home-assistant.io/integrations/http/#ip_ban_enabled) has `ip_ban_enabled` on, an address that reaches `login_attempts_threshold` failures is banned, and each failure also raises a persistent notification.

What does not count: a request with no `Authorization` header, because clients probe the endpoint that way to find out how to sign in, and a valid token that is refused for another reason, such as a non-administrator account. An authorized request clears the address's count, so a client that presents an expired token once before refreshing is never banned for it.

Expect one warning and one "Login attempt failed" notification per connected OAuth client each time its access token expires, which is hourly with the OIDC Provider. Home Assistant does the same for an expired token on its own API.

## Tokens are validated without an audience check

If the installed OIDC provider (hass-oidc-server) is older than 1.5.0, it cannot bind an access token to the resource it was issued for. This integration then still accepts its tokens, but without checking the audience, so a token issued for another resource on the same Home Assistant is accepted too. It logs a warning once and raises a repair named **OIDC provider does not check the token audience**.

Update hass-oidc-server to 1.5.0 or newer and restart Home Assistant; the repair is gone after the restart unless the check is still unavailable.

# Connecting a client

[Back to the README](../README.md)

## Authentication methods

The integration supports two authentication methods:

- **OAuth 2.0** (default): Required for browser-based clients like Claude. Requires [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server) to be installed and configured.
- **Long-Lived Access Tokens** (opt-in): For local agents and custom MCP clients that can't run an OAuth browser flow. No extra dependencies. Must be enabled in the integration settings.

You can use both methods at the same time. When both are active, the server tries OAuth first and falls back to the Long-Lived Access Token.

## Claude on the web (OAuth)

This requires the [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server) integration to be installed.

Claude connects from its own servers, not from your browser, so your Home Assistant has to be reachable from the internet over HTTPS.

The MCP server uses OAuth 2.0 Dynamic Client Registration (DCR), which allows Claude to automatically register itself without manual client setup.

1. In Claude (claude.ai):
   - Open Profile (bottom left corner)
   - Click Settings (gear icon)
   - Navigate to "Connectors"
   - Click "Add custom connector"
   - Enter your MCP server URL: `https://your-home-assistant.com/api/mcp`, or `https://your-home-assistant.com/api/mcp_http` if Home Assistant's built-in MCP Server integration is also installed (see [Another integration also serves /api/mcp](troubleshooting.md#another-integration-also-serves-apimcp))
   - Click "Connect"

2. Claude will automatically:
   - Discover your Home Assistant's OAuth endpoints
   - Register itself as an OAuth client
   - Redirect you to Home Assistant for authentication
   - Request access to your Home Assistant data

3. In Home Assistant:
   - Log in if not already authenticated
   - Review the permissions requested by Claude
   - Click "Authorize" to grant access

That's it! Claude will now be able to interact with your Home Assistant instance through the MCP server.

## Long-lived access tokens

For local agents or MCP clients that can't run an OAuth browser flow, you can authenticate with a Home Assistant Long-Lived Access Token. This must be enabled first.

1. Enable native authentication: Settings > Devices & Services > MCP Server > Configure > check "Enable native Home Assistant authentication"
2. Create a token: go to your Home Assistant user profile > Long-Lived Access Tokens > Create Token
3. Configure your MCP client to send the token as a Bearer header to `http://your-home-assistant:8123/api/mcp` (or `/api/mcp_http`, see [Another integration also serves /api/mcp](troubleshooting.md#another-integration-also-serves-apimcp))

# Connecting a client

[Back to the README](../README.md)

The server answers on `https://your-home-assistant/api/mcp_http`. Clients sign in as a Home Assistant user in one of two ways, and both can be on at once:

- **OAuth**, the default, for clients that sign in through a browser, such as Claude. It needs the [OIDC Provider](https://github.com/ganhammar/hass-oidc-server) integration.
- **Long-lived access tokens**, for local agents and clients that cannot run a browser sign-in. Off until you enable it.

## Claude on the web

Claude connects from its own servers, not from your browser, so Home Assistant has to be reachable from the internet over HTTPS.

1. In Claude, open Settings > Connectors > Add custom connector and enter `https://your-home-assistant/api/mcp_http`.
2. Claude sends you to Home Assistant. Sign in, check what is asking for access, choose what it may do, and approve.

The consent screen offers four levels: Look only, Control devices, Edit the setup and Administer. Look only is preselected the first time, and your last choice for the same application after that. **Choose in detail** sets each area on its own instead, for example everything except cameras. What each level and area allows, and what it does not cover, is in [Choosing what an application may do](security.md#choosing-what-an-application-may-do). This needs the OIDC Provider release after 1.8.0; an older one only asks whether to approve.

To change what a connected application may do, sign it out under Connected applications (`https://your-home-assistant/oidc_login`) and connect it again. A connection made before you updated keeps what it could do until then, which is Administer.

If your OIDC Provider was set up before its version 1.8.0, turn on **Allow client metadata documents** in its options first. That is how Claude identifies itself.

## Long-lived access tokens

1. Under Settings > Devices & Services > MCP Server > Configure, check **Enable native Home Assistant authentication**.
2. In Home Assistant, open your profile > Security > Long-lived access tokens and create a token.
3. Give your client the URL and the token as a bearer token. In Claude Code, for example:

```bash
claude mcp add --transport http home-assistant https://your-home-assistant/api/mcp_http --header "Authorization: Bearer YOUR_TOKEN"
```

On your own network the address can be local, such as `http://homeassistant.local:8123/api/mcp_http`.

A long-lived access token is not asked what it may do: it can use every tool the options allow, as the user it belongs to. Use read-only mode and the disabled tools to limit it.

## The other path, /api/mcp

The server also answers on `/api/mcp`, unless Home Assistant's built-in Model Context Protocol Server has taken that path. `/api/mcp_http` always works, which is why these instructions use it. See [Troubleshooting](troubleshooting.md#another-integration-also-serves-apimcp).

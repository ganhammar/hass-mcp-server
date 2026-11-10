# MCP Server for Home Assistant

Let an AI assistant such as Claude work with your Home Assistant: read what is going on, explain why something happened, and change automations, dashboards and devices for you. It is a custom integration that speaks the Model Context Protocol over HTTP, so it works from anywhere your Home Assistant is reachable, including Claude on the web.

Things you can ask once it is connected:

- "Why didn't the hallway automation run last night?"
- "Which automations and dashboards still use `light.kitchen`? I want to rename it."
- "Add a card for the new heat pump to the energy view."
- "Is anything wrong with my system right now?"
- "Create a scene for movie night and an automation that starts it when the TV turns on."

## Why this one

- **Works remotely, not only at home.** It uses MCP's streamable HTTP transport with OAuth 2.0 and dynamic client registration, which is what browser-based clients such as Claude require. Long-lived access tokens work too, for local agents and custom clients.
- **You decide how far it reaches.** Read-only mode, a switch for every single tool, and separate opt-ins for the groups that reach furthest: config files, camera images, templates and integration management. Only administrators can connect unless you say otherwise.
- **You can see what it did.** Every tool call that changes something is logged and fires an event you can build a notification on.
- **It covers the whole instance.** 117 tools across entities, automations, scripts and scenes, dashboards, helpers, areas, devices and labels, integrations, statistics, traces and diagnostics, plus resources and ready-made prompts.
- **It is tested against Home Assistant itself.** The full test suite runs on every supported version, from 2025.6 to 2026.10, on each change.

## Quick start

1. **Install.** In HACS, search for "MCP Server", download it and restart Home Assistant. To install by hand, copy `custom_components/mcp_server_http_transport` into your `custom_components` folder instead.
2. **Add the integration.** Go to Settings > Devices & Services > Add Integration and search for "MCP Server". For Claude on the web, also install [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server), which provides the OAuth sign-in.
3. **Connect your client.** In Claude, open Settings > Connectors > Add custom connector and enter `https://your-home-assistant/api/mcp`. Claude registers itself and sends you to Home Assistant to approve it.

[Connecting a client](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/connecting.md) has the full steps, including long-lived access tokens.

### Another integration also serves /api/mcp

Home Assistant's built-in Model Context Protocol Server uses the same `/api/mcp` path. If you have both, point your client at `/api/mcp_http`, which only this integration serves. [Troubleshooting](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/troubleshooting.md#another-integration-also-serves-apimcp) explains how to tell and what to do.

## What it can do

| Area | Examples |
|---|---|
| Entities and state | Read and search entities, call any service, history, logbook and long-term statistics |
| Automations, scripts and scenes | List, create, update and delete them, and search their config for a reference |
| Traces | See step by step why an automation or script ran, did not run, or took the wrong branch |
| Dashboards | Read an outline, change a single card with a patch, or create and remove whole dashboards |
| Helpers | Create and manage toggles, counters, timers, schedules and the other helper types |
| Areas, floors, devices and labels | Organize the instance: create areas and floors, move and rename devices, label things |
| Integrations (opt-in) | Add an integration, change its options, reload, disable or remove it |
| Diagnostics | System status, repair issues, system health, backups, the error log, config validation |
| Config files (opt-in) | Read and edit YAML files, with a backup before every change |
| Cameras and images (opt-in) | Capture a camera frame or read a saved image for the assistant to look at |
| KNX | Manage KNX entities and read recent telegrams, when KNX is set up |

The [full reference](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/tools.md) lists every tool, resource and prompt.

## Staying in control

An assistant with write access to your home deserves limits, so the limits are part of the product:

- **Read-only mode** offers only the tools that read. Nothing can be changed.
- **Disabled tools** switches off individual tools, such as `restart_ha`.
- **Opt-in groups** stay off until you enable them, and a tool that is off is not even shown to the client.
- **The audit log and event** record who called what, with secrets masked.

See [Configuration](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/configuration.md) and [Security and auditing](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/security.md).

## Documentation

- [Connecting a client](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/connecting.md): Claude on the web, long-lived access tokens
- [Configuration](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/configuration.md): every option, limiting the tools, integration management
- [Security and auditing](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/security.md): who may connect, the audit log and event
- [Tools, resources and prompts](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/tools.md): the full reference
- [FAQ](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/faq.md): worked examples of common tasks
- [Troubleshooting](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/troubleshooting.md): path conflicts, IP bans, token audience

## License

MIT

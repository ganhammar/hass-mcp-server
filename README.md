# MCP Server for Home Assistant

Let an AI assistant such as Claude work with your Home Assistant: read what is going on, explain why something happened, and change automations, dashboards and devices for you. It is a custom integration that gives the assistant a set of tools for your Home Assistant, and it works wherever Home Assistant is reachable, including Claude on the web.

Things you can ask once it is connected:

- "Are any lights still on upstairs?"
- "Why didn't the hallway automation run last night?"
- "Which automations and dashboards still use `light.kitchen`? I want to rename it."
- "Add a card for the new heat pump to the energy view."
- "Is anything wrong with my system right now?"
- "Create a scene for movie night and an automation that starts it when the TV turns on."

## Why this one

- **It administers the whole instance**, not only the entities exposed to Assist as Home Assistant's built-in MCP Server does: 117 tools for automations, dashboards, the registries, integrations, traces and diagnostics. See [how the two differ](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/faq.md#how-is-this-different-from-home-assistants-built-in-mcp-server).
- **Everyone signs in as themselves.** Clients authenticate through Home Assistant as a real user, with OAuth or a long-lived access token, so there is no shared secret URL and every change is logged with who made it.
- **You decide how far it reaches.** When an app connects, you choose what it may do, from Look only to Administer or area by area. On top of that come read-only mode, a switch for every tool, and opt-ins for the groups that reach furthest. A tool that is off is not even shown to the client.
- **It is tested on each change** against Home Assistant 2025.6, the oldest supported version, and three newer releases up to 2026.10.

## Quick start

**You need:** Home Assistant 2025.6 or newer, HACS, an administrator account and, for Claude on the web, an HTTPS address for Home Assistant that can be reached from the internet.

1. **Install.** In HACS, download "MCP Server" and "OIDC Provider", then restart Home Assistant. The OIDC Provider handles the sign-in for Claude; skip it if you will only use long-lived access tokens.
2. **Add the integrations.** Under Settings > Devices & Services > Add Integration, add "OIDC Provider" first, then "MCP Server (HTTP Transport)".
3. **Connect Claude.** In Claude, open Settings > Connectors > Add custom connector and enter `https://your-home-assistant/api/mcp_http`. Claude sends you to Home Assistant to sign in, choose what Claude may do, and approve.

Choose Look only, or start in read-only mode, if you want to see what it does before letting it change anything. [Connecting a client](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/connecting.md) covers other clients and long-lived access tokens.

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
| Diagnostics | System status, repair issues, system health, the list of backups, the error log, config validation |
| Config files (opt-in) | Read and edit YAML files, with a snapshot of the top-level files before every change |
| Cameras and images (opt-in) | Capture a camera frame or read a saved image for the assistant to look at |
| KNX | Manage KNX entities and read recent telegrams, when KNX is set up |

The [full reference](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/tools.md) lists every tool, resource and prompt. A few tools rely on Home Assistant internals and can break on an update; they are marked there.

## Staying in control

- **Access per app.** The consent screen asks what each app may do: Look only, Control devices, Edit the setup or Administer, or None, Read or Full per area. It needs OIDC Provider 1.9.0 or newer. A connection from before keeps Administer until you change it with Change access under Connected applications, and in read-only mode it is held at Read.
- **Read-only mode** offers only the tools that read, to every connection. Nothing can be changed.
- **Disabled tools** switches off individual tools, such as `restart_ha`.
- **Opt-in groups** stay off until you enable them: config files, cameras and images, templates, AppDaemon apps and integration management.
- **The audit log and event** record who called what, with secrets masked.
- **Only administrators can connect**, unless you decide otherwise.

See [Configuration](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/configuration.md) and [Security and auditing](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/security.md).

## Documentation

- [Connecting a client](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/connecting.md): Claude on the web, long-lived access tokens
- [Configuration](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/configuration.md): every option, limiting the tools, the opt-in groups
- [Security and auditing](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/security.md): who may connect, what each app may do, the audit log and event
- [Tools, resources and prompts](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/tools.md): the full reference
- [FAQ](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/faq.md): what it is, what it can do, and staying safe
- [Troubleshooting](https://github.com/ganhammar/hass-mcp-server/blob/main/docs/troubleshooting.md): setup problems, path conflicts, IP bans

**Updating and removing.** HACS shows updates as they are released. To remove it, delete the integration under Settings > Devices & Services, remove it in HACS, restart, and remove the connector in your client.

**Help.** Questions and bugs go to the [issue tracker](https://github.com/ganhammar/hass-mcp-server/issues).

## License

[MIT](https://github.com/ganhammar/hass-mcp-server/blob/main/LICENSE)

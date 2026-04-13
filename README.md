# MCP Server for Home Assistant (HTTP Transport)

A Home Assistant Custom Component that provides an MCP (Model Context Protocol) server using **HTTP transport**, allowing AI assistants like Claude to interact with your Home Assistant instance.

**Why HTTP transport with OAuth?** This project was built primarily to support MCP Streamable HTTP transport, enabling web-based clients like Claude that require OIDC and OAuth 2.0 Dynamic Client Registration (RFC 7591). Since Home Assistant already has an [official MCP integration](https://www.home-assistant.io/integrations/mcp_server/) that supports SSE transport, there wasn't a need to duplicate that. For local or custom client setups, Long-Lived Access Token authentication can be enabled as an alternative.

## Features

- 🌐 **HTTP transport** (not SSE) - works remotely, not just locally
- 🔐 **OAuth 2.0 authentication** with Dynamic Client Registration (via [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server))
- 🔑 **Long-Lived Access Token** authentication (opt-in) for local and custom client setups
- 🏠 Full Home Assistant API access (entities, services, areas, devices, history, statistics)
- 🔧 Easy HACS installation
- 📝 CRUD management of automations, scenes, and scripts
- 📋 Lovelace dashboard management (list, get/save/delete config, create/update/delete dashboards)
- 🩺 System administration tools (error log, config validation, restart, system status)
- 📊 Resources, prompts, and completions for richer AI interactions
- 🧹 Optimization prompts for auditing automations, naming conventions, and scheduling

## Prerequisites

The integration supports two authentication methods:

- **OAuth 2.0** (default): Required for browser-based clients like Claude. Requires [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server) to be installed and configured.
- **Long-Lived Access Tokens** (opt-in): For local agents and custom MCP clients that can't run an OAuth browser flow. No extra dependencies. Must be enabled in the integration settings.

You can use both methods at the same time. When both are active, the server tries OAuth first and falls back to the Long-Lived Access Token.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
1. Search for "MCP Server"
1. Click "Download"
1. Restart Home Assistant
1. Configure the integration (see Configuration section below)

### Manual Installation

1. Copy the `custom_components/mcp_server_http_transport` folder to your Home Assistant `custom_components` directory
1. Restart Home Assistant
1. Configure the integration (see Configuration section below)

## Configuration

1. Go to Settings > Devices & Services
1. Click "Add Integration"
1. Search for "MCP Server"
1. Choose your authentication method:
   - Leave "Enable native Home Assistant authentication" **unchecked** for OAuth-only (requires [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server))
   - **Check** it to allow Long-Lived Access Tokens (can be used alongside OAuth or on its own)

You can change this setting later via Settings > Devices & Services > MCP Server > Configure.

## Usage with Claude in Browser (OAuth)

This requires the [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server) integration to be installed.

The MCP server uses OAuth 2.0 Dynamic Client Registration (DCR), which allows Claude to automatically register itself without manual client setup.

1. In Claude (claude.ai):
   - Open Profile (bottom left corner)
   - Click Settings (gear icon)
   - Navigate to "Connectors"
   - Click "Add custom connector"
   - Enter your MCP server URL: `https://your-home-assistant.com/api/mcp`
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

## Usage with Long-Lived Access Tokens

For local agents or MCP clients that can't run an OAuth browser flow, you can authenticate with a Home Assistant Long-Lived Access Token. This must be enabled first.

1. Enable native authentication: Settings > Devices & Services > MCP Server > Configure > check "Enable native Home Assistant authentication"
2. Create a token: go to your Home Assistant user profile > Long-Lived Access Tokens > Create Token
3. Configure your MCP client to send the token as a Bearer header to `http://your-home-assistant:8123/api/mcp`

## MCP Capabilities

### Tools

| Tool | Description |
|------|-------------|
| `get_state` | Get the current state of any entity (optional `fields` to limit attributes) |
| `call_service` | Call any Home Assistant service |
| `list_entities` | List all entities, with optional `domain`, `detailed`, and `fields` parameters |
| `get_config` | Get Home Assistant configuration (version, location, units, timezone) |
| `list_areas` | List all areas |
| `list_devices` | List devices, optionally filtered by area |
| `list_services` | List available services, optionally filtered by domain |
| `render_template` | Evaluate a Jinja2 template |
| `get_history` | Get state history of an entity over a time range |
| `list_automations` | List all automations with full configuration |
| `get_automation_config` | Get full configuration of a single automation |
| `create_automation` | Create a new automation |
| `update_automation` | Update an existing automation |
| `delete_automation` | Delete an automation |
| `list_scenes` | List all scenes with full configuration |
| `get_scene_config` | Get full configuration of a single scene |
| `create_scene` | Create a new scene |
| `update_scene` | Update an existing scene |
| `delete_scene` | Delete a scene |
| `list_scripts` | List all scripts with full configuration |
| `get_script_config` | Get full configuration of a single script |
| `create_script` | Create a new script |
| `update_script` | Update an existing script |
| `delete_script` | Delete a script |
| `list_dashboards` | List all Lovelace dashboards with metadata |
| `get_dashboard_config` | Get full dashboard configuration (views/cards) |
| `save_dashboard_config` | Save (replace) full dashboard configuration |
| `delete_dashboard_config` | Reset a dashboard configuration to empty |
| `create_dashboard` | Create a new Lovelace dashboard (experimental) |
| `update_dashboard` | Update dashboard metadata (experimental) |
| `delete_dashboard` | Delete a dashboard and its config (experimental) |
| `search_entities` | Search entities by friendly name, device class, domain, or area |
| `fire_event` | Fire a custom event on the Home Assistant event bus |
| `get_logbook` | Fetch logbook entries for an entity or time range |
| `get_error_log` | Fetch the Home Assistant error log (last N lines) |
| `restart_ha` | Restart Home Assistant (requires explicit confirmation) |
| `get_system_status` | System overview: version, domain counts, entity totals, problem entities |
| `get_domain_stats` | Aggregate stats for a single domain (count, state breakdown, examples) |
| `check_config` | Validate Home Assistant configuration without restarting |
| `get_statistics` | Fetch long-term statistics (energy, climate) with configurable period |
| `list_integrations` | List installed integrations and their status |
| `list_labels` | List all labels for cross-domain grouping |
| `batch_get_state` | Get state for multiple entities in one call (max 50) |

### Resources

| URI | Description |
|-----|-------------|
| `hass://config` | Home Assistant configuration |
| `hass://areas` | All areas |
| `hass://devices` | All registered devices |
| `hass://services` | All available services by domain |
| `hass://floors` | All configured floors |
| `hass://entities` | All entities organized by domain |
| `hass://labels` | All labels |
| `hass://integrations` | Installed integrations with status |
| `hass://entity/{entity_id}` | State and attributes of a specific entity |
| `hass://dashboard/{url_path}` | Full configuration of a specific dashboard |
| `hass://entities/domain/{domain}` | Entities filtered by a specific domain |

### Prompts

| Prompt | Description |
|--------|-------------|
| `troubleshoot_device` | Diagnose issues with a specific entity |
| `daily_summary` | Summarize recent activity across all entities |
| `automation_review` | Review an automation's config for issues and improvements |
| `energy_report` | Summarize energy consumption data over a time range |
| `setup_guide` | Guided troubleshooting for an entity in a problem state |
| `automation_builder` | Step-by-step guided automation creation |
| `automation_debugger` | Debug why an automation is not firing or misbehaving |
| `automation_audit` | Audit all automations for conflicts, redundancies, and anti-patterns |
| `schedule_optimizer` | Analyze automation schedules and suggest timing improvements |
| `naming_conventions` | Scan entity names for inconsistencies and suggest standardization |
| `dashboard_builder` | Suggest a Lovelace dashboard layout for given entities or area |
| `change_validator` | Pre-flight check after creating or modifying configurations |
| `security_review` | Scan for security issues in entities, integrations, and configuration |

### Completions

Autocompletion is supported for `entity_id`, `entity_ids`, `domain`, `service`, `area_id`, `url_path`, `automation_id`, `scene_id`, script `key`, `trigger_type`, `period`, and `config_type` arguments.

## FAQ

<details>
<summary>How do I list all automations, scenes, or scripts?</summary>

Use the dedicated list tools to get full configurations:

```
list_automations()   // all automations with triggers, conditions, actions
list_scenes()        // all scenes with entity states
list_scripts()       // all scripts with sequences
```

To get the configuration of a single item:

```
get_automation_config(automation_id="abc-123")
get_scene_config(scene_id="def-456")
get_script_config(key="morning_routine")
```

You can also use `list_entities(domain="automation")` to get entity states, but the tools above return the full YAML configuration.
</details>

<details>
<summary>How do I create an automation?</summary>

Use `create_automation` with a standard HA automation config:

```json
create_automation(config={
  "alias": "Turn on lights at sunset",
  "trigger": [{"platform": "sun", "event": "sunset"}],
  "action": [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}]
})
```

The same pattern applies to scenes and scripts. Scripts use a `key` parameter instead of an auto-generated ID:

```json
create_script(key="morning_routine", config={
  "alias": "Morning Routine",
  "sequence": [{"service": "light.turn_on", "target": {"entity_id": "light.bedroom"}}]
})
```
</details>

<details>
<summary>How do I call a service like turning on a light?</summary>

Use `call_service` with the domain, service, and optionally an entity and extra data:

```json
call_service(domain="light", service="turn_on", entity_id="light.living_room", data={"brightness": 200})
```

To discover what services are available:

```
list_services()                    // all services
list_services(domain="light")      // just light services
```
</details>

<details>
<summary>How do I manage Lovelace dashboards?</summary>

Use `list_dashboards` to see all dashboards, then `get_dashboard_config` and `save_dashboard_config` to read and modify their content. Use `url_path="default"` for the main Overview dashboard:

```json
get_dashboard_config(url_path="default")
save_dashboard_config(url_path="default", config={"views": [{"title": "Home", "cards": [...]}]})
```

To create or delete dashboards themselves, use the experimental `create_dashboard` and `delete_dashboard` tools. These use internal HA APIs and may break with future HA updates.
</details>

<details>
<summary>What does "experimental" mean for dashboard tools?</summary>

The `create_dashboard`, `update_dashboard`, and `delete_dashboard` tools use internal Home Assistant APIs (`DashboardsCollection`) that are not publicly exposed. They replicate side effects (panel registration, dashboards dict updates) that HA normally handles internally. These may break with HA updates that change internal behavior. The config-level tools (`list_dashboards`, `get/save/delete_dashboard_config`) use stable public APIs.
</details>

## License

MIT

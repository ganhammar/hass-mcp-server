[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/ganhammar-hass-mcp-server-badge.png)](https://mseep.ai/app/ganhammar-hass-mcp-server)

# MCP Server for Home Assistant (HTTP Transport)

A Home Assistant Custom Component that provides an MCP (Model Context Protocol) server using **HTTP transport**, allowing AI assistants like Claude to interact with your Home Assistant instance.

**Note:** Unlike other Home Assistant MCP servers that use SSE (Server-Sent Events), this implementation uses HTTP transport with OAuth 2.0 authentication, making it suitable for remote access and integration with services like Claude in browser.

## Features

- 🌐 **HTTP transport** (not SSE) - works remotely, not just locally
- 🏠 Full Home Assistant API access
- 🔧 Easy HACS installation
- 📊 Access to entities, states, and services

## Prerequisites

This plugin requires [hass-oidc-auth](https://github.com/ganhammar/hass-oidc-auth) to be installed and configured for OIDC authentication.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
1. Search for "MCP Server"
1. Click "Download"
1. Restart Home Assistant
1. Configure the integration (see Configuration section below)

### Manual Installation

1. Copy the `custom_components/mcp_server` folder to your Home Assistant `custom_components` directory
1. Restart Home Assistant
1. Configure the integration (see Configuration section below)

## Configuration

1. Go to Settings → Devices & Services
1. Click "Add Integration"
1. Search for "MCP Server"
1. Follow the configuration steps

## Usage with Claude in Browser

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

### Available Tools

- `get_state`: Get the current state of any Home Assistant entity
- `call_service`: Call any Home Assistant service (turn on lights, etc.)
- `list_entities`: List all entities, optionally filtered by domain

## License

MIT

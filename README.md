[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/ganhammar-hass-mcp-server-badge.png)](https://mseep.ai/app/ganhammar-hass-mcp-server)

# MCP Server for Home Assistant (HTTP Transport)

A Home Assistant Custom Component that provides an MCP (Model Context Protocol) server using **HTTP transport**, allowing AI assistants like Claude to interact with your Home Assistant instance.

**Note:** Unlike other Home Assistant MCP servers that use SSE (Server-Sent Events), this implementation uses HTTP transport with OAuth 2.0 authentication, making it suitable for remote access and integration with services like Claude Desktop.

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
2. Go to "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add `https://github.com/ganhammar/hass-mcp-server` as an Integration
6. Click "Install"
7. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/mcp_server` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "MCP Server"
4. Follow the configuration steps

## Usage with Claude Desktop

The MCP server uses OAuth 2.0 Dynamic Client Registration (DCR), which allows Claude to automatically register itself without manual client setup.

1. In Claude Desktop:
   - Open Settings → Connectors
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

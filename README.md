# MCP Server for Home Assistant

A Home Assistant Custom Component that provides an MCP (Model Context Protocol) server, allowing AI assistants like Claude to interact with your Home Assistant instance.

## Features

- 🔐 Secure OIDC authentication
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

1. First, register an OIDC client in Home Assistant:
   - Go to Developer Tools → Actions
   - Call the `oidc_provider.register_client` action:
     ```yaml
     client_id: claude-desktop
     client_name: Claude Desktop
     redirect_uris:
       - https://claude.ai/oauth/callback
     ```
   - Save the generated `client_secret`

2. Configure Claude Desktop as a custom connector:
   - Open Claude Desktop settings
   - Navigate to Connectors
   - Click "Add custom connector"
   - Enter your MCP server URL: `https://your-home-assistant.com/api/mcp`
   - Configure OAuth:
     - Client ID: `claude-desktop`
     - Client Secret: (from step 1)
     - Authorization URL: `https://your-home-assistant.com/auth/oidc/authorize`
     - Token URL: `https://your-home-assistant.com/auth/oidc/token`

## License

MIT

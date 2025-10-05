"""MCP Server for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from mcp.server import Server

from .const import DOMAIN
from .http import MCPEndpointView

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the MCP Server component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MCP Server from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create MCP server
    server = Server("home-assistant-mcp-server")
    hass.data[DOMAIN]["server"] = server

    # Register HTTP endpoint
    hass.http.register_view(MCPEndpointView(hass, server))

    _LOGGER.info("MCP Server initialized at /api/mcp")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].clear()
    return True

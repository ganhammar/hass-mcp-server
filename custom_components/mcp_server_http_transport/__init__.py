"""MCP Server for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from mcp.server import Server

from .const import DOMAIN
from .http import (
    MCPEndpointView,
    MCPProtectedResourceMetadataView,
    MCPSubpathProtectedResourceMetadataView,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


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

    # Register HTTP endpoints
    hass.http.register_view(MCPProtectedResourceMetadataView())
    hass.http.register_view(MCPSubpathProtectedResourceMetadataView())
    hass.http.register_view(MCPEndpointView(hass, server))

    _LOGGER.info("MCP Server initialized at /api/mcp")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].clear()
    return True

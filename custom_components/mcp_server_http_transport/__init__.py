"""MCP Server for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from mcp.server import Server

from .const import CONF_CONFIG_FILE_ACCESS, CONF_NATIVE_AUTH, DOMAIN
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

    native_auth_enabled = entry.data.get(CONF_NATIVE_AUTH, False)
    config_file_access_enabled = entry.data.get(CONF_CONFIG_FILE_ACCESS, False)

    hass.data[DOMAIN]["config_file_access"] = config_file_access_enabled

    # Create MCP server
    server = Server("home-assistant-mcp-server")
    hass.data[DOMAIN]["server"] = server

    # Register HTTP endpoints. The views are gated on hass.data[DOMAIN] so
    # requests stop being served the moment async_unload_entry clears it
    # (HA has no public register_view reverse — see #37).
    hass.http.register_view(MCPProtectedResourceMetadataView(hass))
    hass.http.register_view(MCPSubpathProtectedResourceMetadataView(hass))
    hass.http.register_view(MCPEndpointView(hass, server, native_auth_enabled))

    _LOGGER.info("MCP Server initialized at /api/mcp (native_auth=%s)", native_auth_enabled)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].clear()
    return True

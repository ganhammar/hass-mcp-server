"""MCP Server for Home Assistant."""

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .server import HomeAssistantMCPServer

_LOGGER = logging.getLogger(__name__)

DOMAIN = "mcp_server"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the MCP Server component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MCP Server from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    server = HomeAssistantMCPServer(hass)
    hass.data[DOMAIN]["server"] = server

    # Start the MCP server in the background
    task = asyncio.create_task(server.run("", 0))
    hass.data[DOMAIN]["task"] = task

    _LOGGER.info("MCP Server initialized")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    task = hass.data[DOMAIN].get("task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    hass.data[DOMAIN].clear()
    return True

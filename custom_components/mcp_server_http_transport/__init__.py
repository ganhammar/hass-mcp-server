"""MCP Server for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started
from mcp.server import Server

from .const import (
    CONF_CAMERA_IMAGE_ACCESS,
    CONF_CONFIG_FILE_ACCESS,
    CONF_IMAGE_FILE_ACCESS,
    CONF_NATIVE_AUTH,
    DOMAIN,
    ISSUE_ENDPOINT_CONFLICT,
    MCP_HTTP_PATH,
    MCP_PATH,
)
from .http import mcp_path_is_contested, register_mcp_views, serves_mcp_path

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
    camera_image_access_enabled = entry.data.get(CONF_CAMERA_IMAGE_ACCESS, False)
    image_file_access_enabled = entry.data.get(CONF_IMAGE_FILE_ACCESS, False)

    hass.data[DOMAIN]["config_file_access"] = config_file_access_enabled
    hass.data[DOMAIN]["camera_image_access"] = camera_image_access_enabled
    hass.data[DOMAIN]["image_file_access"] = image_file_access_enabled

    # Create MCP server
    server = Server("home-assistant-mcp-server")
    hass.data[DOMAIN]["server"] = server

    # Register HTTP endpoints. The views are gated on hass.data[DOMAIN] so
    # requests stop being served the moment async_unload_entry clears it
    # (HA has no public register_view reverse — see #37).
    register_mcp_views(hass, server, native_auth_enabled)

    _LOGGER.info(
        "MCP Server initialized at %s (native_auth=%s)",
        f"{MCP_PATH} and {MCP_HTTP_PATH}" if serves_mcp_path(hass) else MCP_HTTP_PATH,
        native_auth_enabled,
    )

    @callback
    def _async_component_loaded(event: Event) -> None:
        """Re-check after any component loads.

        An integration claims its paths from its own async_setup, so one that
        loads after this entry, at boot or when the user adds it, would
        otherwise take MCP_PATH with nothing said until the next restart.
        """
        _async_report_endpoint_conflict(hass)

    entry.async_on_unload(async_at_started(hass, _async_report_endpoint_conflict))
    entry.async_on_unload(hass.bus.async_listen(EVENT_COMPONENT_LOADED, _async_component_loaded))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


@callback
def _async_report_endpoint_conflict(hass: HomeAssistant) -> None:
    """Raise a repair issue while another integration also has MCP_PATH bound.

    Safe to call repeatedly: the issue is the state of the conflict, and the
    warning goes out only when the conflict is newly seen.
    """
    if not mcp_path_is_contested(hass):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ENDPOINT_CONFLICT)
        return

    if ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ENDPOINT_CONFLICT) is None:
        _LOGGER.warning(
            "Another integration also has POST %s bound. Home Assistant routes that path "
            "to whichever integration registered it first, so point MCP clients at %s, "
            "which only this integration serves",
            MCP_PATH,
            MCP_HTTP_PATH,
        )

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ENDPOINT_CONFLICT,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_ENDPOINT_CONFLICT,
        translation_placeholders={"path": MCP_PATH, "dedicated_path": MCP_HTTP_PATH},
        learn_more_url=(
            "https://github.com/ganhammar/hass-mcp-server" "#another-integration-also-serves-apimcp"
        ),
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Any conflict issue stays up: the routes registered for this entry keep
    MCP_PATH bound (answering 503) until Home Assistant restarts, so unloading
    makes the conflict worse rather than resolving it.
    """
    hass.data[DOMAIN].clear()
    return True

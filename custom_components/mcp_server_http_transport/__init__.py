"""MCP Server for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
from .http import mcp_path_is_contested, register_mcp_views

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
    contested = register_mcp_views(hass, server, native_auth_enabled)

    _LOGGER.info(
        "MCP Server initialized at %s (native_auth=%s)",
        MCP_HTTP_PATH if contested else f"{MCP_PATH} and {MCP_HTTP_PATH}",
        native_auth_enabled,
    )

    # Another integration can claim MCP_PATH after this one has set up, so the
    # conflict is reported once Home Assistant has started rather than here.
    entry.async_on_unload(async_at_started(hass, _async_report_endpoint_conflict))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


@callback
def _async_report_endpoint_conflict(hass: HomeAssistant) -> None:
    """Raise a repair issue while another integration also serves MCP_PATH."""
    if not mcp_path_is_contested(hass):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ENDPOINT_CONFLICT)
        return

    _LOGGER.warning(
        "Another integration also serves POST %s. Home Assistant routes that path to "
        "whichever integration registered it first, so point MCP clients at %s, which "
        "only this integration serves",
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
    """Unload a config entry."""
    hass.data[DOMAIN].clear()
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ENDPOINT_CONFLICT)
    return True

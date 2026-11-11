"""Config flow for MCP Server."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_APPDAEMON_APPS_ROOT,
    CONF_APPDAEMON_FILE_ACCESS,
    CONF_CAMERA_IMAGE_ACCESS,
    CONF_CONFIG_FILE_ACCESS,
    CONF_DISABLED_TOOLS,
    CONF_IMAGE_FILE_ACCESS,
    CONF_INTEGRATION_MANAGEMENT,
    CONF_NATIVE_AUTH,
    CONF_READ_ONLY,
    CONF_REQUIRE_ADMIN,
    CONF_SERVER_NAME,
    CONF_STATELESS_PROTOCOL,
    CONF_TEMPLATE_RENDERING,
    DEFAULT_APPDAEMON_APPS_ROOT,
    DEFAULT_SERVER_NAME,
    DOMAIN,
    validate_appdaemon_apps_root,
    validate_server_name,
)
from .tools import TOOLS

_LOGGER = logging.getLogger(__name__)

_TOOL_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=sorted(TOOLS),
        multiple=True,
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NATIVE_AUTH, default=False): bool,
        vol.Optional(CONF_REQUIRE_ADMIN, default=True): bool,
        vol.Optional(CONF_READ_ONLY, default=False): bool,
        vol.Optional(CONF_DISABLED_TOOLS, default=list): _TOOL_SELECTOR,
        vol.Optional(CONF_CONFIG_FILE_ACCESS, default=False): bool,
        vol.Optional(CONF_CAMERA_IMAGE_ACCESS, default=False): bool,
        vol.Optional(CONF_IMAGE_FILE_ACCESS, default=False): bool,
        vol.Optional(CONF_TEMPLATE_RENDERING, default=False): bool,
        vol.Optional(CONF_INTEGRATION_MANAGEMENT, default=False): bool,
        vol.Optional(CONF_APPDAEMON_FILE_ACCESS, default=False): bool,
        vol.Optional(CONF_APPDAEMON_APPS_ROOT, default=DEFAULT_APPDAEMON_APPS_ROOT): str,
        vol.Optional(CONF_SERVER_NAME, default=DEFAULT_SERVER_NAME): str,
        vol.Optional(CONF_STATELESS_PROTOCOL, default=True): bool,
    }
)


class MCPServerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MCP Server."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = {
                **user_input,
                CONF_APPDAEMON_APPS_ROOT: user_input.get(
                    CONF_APPDAEMON_APPS_ROOT, DEFAULT_APPDAEMON_APPS_ROOT
                ),
            }
            native_auth = user_input.get(CONF_NATIVE_AUTH, False)

            if user_input.get(CONF_APPDAEMON_FILE_ACCESS, False):
                try:
                    validate_appdaemon_apps_root(
                        user_input.get(CONF_APPDAEMON_APPS_ROOT, DEFAULT_APPDAEMON_APPS_ROOT)
                    )
                except ValueError:
                    errors[CONF_APPDAEMON_APPS_ROOT] = "invalid_appdaemon_apps_root"

            # A blank name leaves a client with nothing to key its tool
            # namespace on, so it is rejected before the entry is created.
            try:
                user_input[CONF_SERVER_NAME] = validate_server_name(
                    user_input.get(CONF_SERVER_NAME, DEFAULT_SERVER_NAME)
                )
            except ValueError:
                errors[CONF_SERVER_NAME] = "invalid_server_name"

            # OIDC provider is only required when native auth is disabled
            if errors:
                pass
            elif (
                not native_auth and "oidc_provider" not in self.hass.config_entries.async_domains()
            ):
                errors["base"] = "oidc_provider_required"
            else:
                return self.async_create_entry(title="MCP Server", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "oidc_provider_url": "https://github.com/ganhammar/hass-oidc-server"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return MCPServerOptionsFlowHandler()


class MCPServerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle MCP Server options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        current_native_auth = self.config_entry.data.get(CONF_NATIVE_AUTH, False)
        current_require_admin = self.config_entry.data.get(CONF_REQUIRE_ADMIN, True)
        current_read_only = self.config_entry.data.get(CONF_READ_ONLY, False)
        # A name stored for a tool that no longer exists is not offered back.
        current_disabled_tools = [
            name for name in self.config_entry.data.get(CONF_DISABLED_TOOLS, ()) if name in TOOLS
        ]
        current_config_file_access = self.config_entry.data.get(CONF_CONFIG_FILE_ACCESS, False)
        current_camera_image_access = self.config_entry.data.get(CONF_CAMERA_IMAGE_ACCESS, False)
        current_image_file_access = self.config_entry.data.get(CONF_IMAGE_FILE_ACCESS, False)
        current_template_rendering = self.config_entry.data.get(CONF_TEMPLATE_RENDERING, False)
        current_integration_management = self.config_entry.data.get(
            CONF_INTEGRATION_MANAGEMENT, False
        )
        current_appdaemon_file_access = self.config_entry.data.get(
            CONF_APPDAEMON_FILE_ACCESS, False
        )
        current_appdaemon_apps_root = self.config_entry.data.get(
            CONF_APPDAEMON_APPS_ROOT, DEFAULT_APPDAEMON_APPS_ROOT
        )
        current_server_name = self.config_entry.data.get(CONF_SERVER_NAME, DEFAULT_SERVER_NAME)
        current_stateless_protocol = self.config_entry.data.get(CONF_STATELESS_PROTOCOL, True)

        if user_input is not None:
            submitted_appdaemon_apps_root = user_input.get(
                CONF_APPDAEMON_APPS_ROOT, current_appdaemon_apps_root
            )
            submitted_appdaemon_file_access = user_input.get(
                CONF_APPDAEMON_FILE_ACCESS, current_appdaemon_file_access
            )
            if submitted_appdaemon_file_access:
                try:
                    validate_appdaemon_apps_root(submitted_appdaemon_apps_root)
                except ValueError:
                    errors[CONF_APPDAEMON_APPS_ROOT] = "invalid_appdaemon_apps_root"

            submitted_server_name = user_input.get(CONF_SERVER_NAME, DEFAULT_SERVER_NAME)
            try:
                submitted_server_name = validate_server_name(submitted_server_name)
            except ValueError:
                errors[CONF_SERVER_NAME] = "invalid_server_name"

            native_auth = user_input.get(CONF_NATIVE_AUTH, False)

            if (
                not errors
                and not native_auth
                and "oidc_provider" not in self.hass.config_entries.async_domains()
            ):
                errors["base"] = "oidc_provider_required"
            elif not errors:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        **user_input,
                        CONF_APPDAEMON_APPS_ROOT: submitted_appdaemon_apps_root,
                        CONF_SERVER_NAME: submitted_server_name,
                    },
                )
                return self.async_create_entry(title="", data={})

        values = user_input or {}
        current_native_auth = values.get(CONF_NATIVE_AUTH, current_native_auth)
        current_require_admin = values.get(CONF_REQUIRE_ADMIN, current_require_admin)
        current_read_only = values.get(CONF_READ_ONLY, current_read_only)
        current_disabled_tools = values.get(CONF_DISABLED_TOOLS, current_disabled_tools)
        current_config_file_access = values.get(CONF_CONFIG_FILE_ACCESS, current_config_file_access)
        current_camera_image_access = values.get(
            CONF_CAMERA_IMAGE_ACCESS, current_camera_image_access
        )
        current_image_file_access = values.get(CONF_IMAGE_FILE_ACCESS, current_image_file_access)
        current_template_rendering = values.get(CONF_TEMPLATE_RENDERING, current_template_rendering)
        current_integration_management = values.get(
            CONF_INTEGRATION_MANAGEMENT, current_integration_management
        )
        current_appdaemon_file_access = values.get(
            CONF_APPDAEMON_FILE_ACCESS, current_appdaemon_file_access
        )
        current_appdaemon_apps_root = values.get(
            CONF_APPDAEMON_APPS_ROOT, current_appdaemon_apps_root
        )
        current_server_name = values.get(CONF_SERVER_NAME, current_server_name)
        current_stateless_protocol = values.get(CONF_STATELESS_PROTOCOL, current_stateless_protocol)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NATIVE_AUTH, default=current_native_auth): bool,
                    vol.Optional(CONF_REQUIRE_ADMIN, default=current_require_admin): bool,
                    vol.Optional(CONF_READ_ONLY, default=current_read_only): bool,
                    vol.Optional(
                        CONF_DISABLED_TOOLS, default=current_disabled_tools
                    ): _TOOL_SELECTOR,
                    vol.Optional(CONF_CONFIG_FILE_ACCESS, default=current_config_file_access): bool,
                    vol.Optional(
                        CONF_CAMERA_IMAGE_ACCESS, default=current_camera_image_access
                    ): bool,
                    vol.Optional(CONF_IMAGE_FILE_ACCESS, default=current_image_file_access): bool,
                    vol.Optional(CONF_TEMPLATE_RENDERING, default=current_template_rendering): bool,
                    vol.Optional(
                        CONF_INTEGRATION_MANAGEMENT, default=current_integration_management
                    ): bool,
                    vol.Optional(
                        CONF_APPDAEMON_FILE_ACCESS, default=current_appdaemon_file_access
                    ): bool,
                    vol.Optional(
                        CONF_APPDAEMON_APPS_ROOT, default=current_appdaemon_apps_root
                    ): str,
                    vol.Optional(
                        CONF_SERVER_NAME, description={"suggested_value": current_server_name}
                    ): str,
                    vol.Optional(CONF_STATELESS_PROTOCOL, default=current_stateless_protocol): bool,
                }
            ),
            errors=errors,
        )

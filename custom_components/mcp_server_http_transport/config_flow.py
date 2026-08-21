"""Config flow for MCP Server."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_APPDAEMON_APPS_ROOT,
    CONF_APPDAEMON_FILE_ACCESS,
    CONF_CAMERA_IMAGE_ACCESS,
    CONF_CONFIG_FILE_ACCESS,
    CONF_IMAGE_FILE_ACCESS,
    CONF_NATIVE_AUTH,
    DEFAULT_APPDAEMON_APPS_ROOT,
    DOMAIN,
    validate_appdaemon_apps_root,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NATIVE_AUTH, default=False): bool,
        vol.Optional(CONF_CONFIG_FILE_ACCESS, default=False): bool,
        vol.Optional(CONF_CAMERA_IMAGE_ACCESS, default=False): bool,
        vol.Optional(CONF_IMAGE_FILE_ACCESS, default=False): bool,
        vol.Optional(CONF_APPDAEMON_FILE_ACCESS, default=False): bool,
        vol.Optional(CONF_APPDAEMON_APPS_ROOT, default=DEFAULT_APPDAEMON_APPS_ROOT): str,
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

            try:
                validate_appdaemon_apps_root(
                    user_input.get(CONF_APPDAEMON_APPS_ROOT, DEFAULT_APPDAEMON_APPS_ROOT)
                )
            except ValueError:
                errors["base"] = "invalid_appdaemon_apps_root"

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
                "oidc_provider_url": "https://github.com/ganhammar/hass-oidc-provider"
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
        current_config_file_access = self.config_entry.data.get(CONF_CONFIG_FILE_ACCESS, False)
        current_camera_image_access = self.config_entry.data.get(CONF_CAMERA_IMAGE_ACCESS, False)
        current_image_file_access = self.config_entry.data.get(CONF_IMAGE_FILE_ACCESS, False)
        current_appdaemon_file_access = self.config_entry.data.get(
            CONF_APPDAEMON_FILE_ACCESS, False
        )
        current_appdaemon_apps_root = self.config_entry.data.get(
            CONF_APPDAEMON_APPS_ROOT, DEFAULT_APPDAEMON_APPS_ROOT
        )

        if user_input is not None:
            submitted_appdaemon_apps_root = user_input.get(
                CONF_APPDAEMON_APPS_ROOT, current_appdaemon_apps_root
            )
            try:
                validate_appdaemon_apps_root(submitted_appdaemon_apps_root)
            except ValueError:
                errors["base"] = "invalid_appdaemon_apps_root"

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
                    },
                )
                return self.async_create_entry(title="", data={})

        values = user_input or {}
        current_native_auth = values.get(CONF_NATIVE_AUTH, current_native_auth)
        current_config_file_access = values.get(CONF_CONFIG_FILE_ACCESS, current_config_file_access)
        current_camera_image_access = values.get(
            CONF_CAMERA_IMAGE_ACCESS, current_camera_image_access
        )
        current_image_file_access = values.get(CONF_IMAGE_FILE_ACCESS, current_image_file_access)
        current_appdaemon_file_access = values.get(
            CONF_APPDAEMON_FILE_ACCESS, current_appdaemon_file_access
        )
        current_appdaemon_apps_root = values.get(
            CONF_APPDAEMON_APPS_ROOT, current_appdaemon_apps_root
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NATIVE_AUTH, default=current_native_auth): bool,
                    vol.Optional(CONF_CONFIG_FILE_ACCESS, default=current_config_file_access): bool,
                    vol.Optional(
                        CONF_CAMERA_IMAGE_ACCESS, default=current_camera_image_access
                    ): bool,
                    vol.Optional(CONF_IMAGE_FILE_ACCESS, default=current_image_file_access): bool,
                    vol.Optional(
                        CONF_APPDAEMON_FILE_ACCESS, default=current_appdaemon_file_access
                    ): bool,
                    vol.Optional(
                        CONF_APPDAEMON_APPS_ROOT, default=current_appdaemon_apps_root
                    ): str,
                }
            ),
            errors=errors,
        )

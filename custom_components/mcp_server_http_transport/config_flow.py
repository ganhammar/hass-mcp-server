"""Config flow for MCP Server."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_NATIVE_AUTH, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NATIVE_AUTH, default=False): bool,
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
            native_auth = user_input.get(CONF_NATIVE_AUTH, False)

            # OIDC provider is only required when native auth is disabled
            if not native_auth and "oidc_provider" not in self.hass.config_entries.async_domains():
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

        if user_input is not None:
            native_auth = user_input.get(CONF_NATIVE_AUTH, False)

            if not native_auth and "oidc_provider" not in self.hass.config_entries.async_domains():
                errors["base"] = "oidc_provider_required"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, **user_input},
                )
                return self.async_create_entry(title="", data={})

        current_native_auth = self.config_entry.data.get(CONF_NATIVE_AUTH, False)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NATIVE_AUTH, default=current_native_auth): bool,
                }
            ),
            errors=errors,
        )

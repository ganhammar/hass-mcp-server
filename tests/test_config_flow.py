"""Test config flow for MCP Server integration."""

from unittest.mock import Mock, patch

from homeassistant import data_entry_flow

from custom_components.mcp_server_http_transport.config_flow import (
    MCPServerConfigFlow,
    MCPServerOptionsFlowHandler,
)
from custom_components.mcp_server_http_transport.const import CONF_NATIVE_AUTH


class TestMCPServerConfigFlow:
    """Test the MCP Server config flow."""

    async def test_user_flow_creates_entry_with_oidc(self):
        """Test user flow creates config entry when OIDC provider exists."""
        mock_hass = Mock()
        mock_hass.config_entries = Mock()
        mock_hass.config_entries.async_domains = Mock(return_value=["oidc_provider"])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input={CONF_NATIVE_AUTH: False})

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "MCP Server"
        assert result["data"][CONF_NATIVE_AUTH] is False

    async def test_user_flow_creates_entry_with_native_auth(self):
        """Test user flow creates entry with native auth, no OIDC required."""
        mock_hass = Mock()
        mock_hass.config_entries = Mock()
        mock_hass.config_entries.async_domains = Mock(return_value=[])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input={CONF_NATIVE_AUTH: True})

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_NATIVE_AUTH] is True

    async def test_user_flow_error_when_no_oidc_and_native_disabled(self):
        """Test user flow shows error when OIDC missing and native auth disabled."""
        mock_hass = Mock()
        mock_hass.config_entries = Mock()
        mock_hass.config_entries.async_domains = Mock(return_value=[])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input={CONF_NATIVE_AUTH: False})

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"]["base"] == "oidc_provider_required"

    async def test_user_flow_shows_form_when_no_input(self):
        """Test user flow shows form when no input provided."""
        mock_hass = Mock()
        mock_hass.config_entries = Mock()
        mock_hass.config_entries.async_domains = Mock(return_value=["oidc_provider"])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input=None)

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["data_schema"] is not None

    async def test_user_flow_form_has_native_auth_field(self):
        """Test user flow form includes native_auth_enabled field."""
        mock_hass = Mock()
        mock_hass.config_entries = Mock()
        mock_hass.config_entries.async_domains = Mock(return_value=["oidc_provider"])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input=None)

        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_NATIVE_AUTH in schema_keys

    async def test_version_is_set(self):
        """Test config flow version is set."""
        flow = MCPServerConfigFlow()
        assert flow.VERSION == 1

    def test_async_get_options_flow_returns_options_flow(self):
        """Test async_get_options_flow returns options flow instance."""
        with patch.object(MCPServerOptionsFlowHandler, "__init__", return_value=None):
            mock_config_entry = Mock()
            options_flow = MCPServerConfigFlow.async_get_options_flow(mock_config_entry)

            assert isinstance(options_flow, MCPServerOptionsFlowHandler)


class TestMCPServerOptionsFlow:
    """Test the MCP Server options flow."""

    def _create_flow(self, data=None):
        """Create an options flow with mocked internals."""
        mock_config_entry = Mock()
        mock_config_entry.data = data or {}
        mock_config_entry.entry_id = "test_entry"

        flow = MCPServerOptionsFlowHandler.__new__(MCPServerOptionsFlowHandler)
        flow.hass = Mock()
        flow.hass.config_entries = Mock()
        flow.hass.config_entries.async_get_known_entry.return_value = mock_config_entry
        flow._config_entry = mock_config_entry
        flow.handler = mock_config_entry.entry_id
        return flow

    async def test_init_step_shows_form(self):
        """Test init step shows form."""
        flow = self._create_flow()
        result = await flow.async_step_init(user_input=None)

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_init_step_shows_current_value(self):
        """Test init step shows current native_auth_enabled value."""
        flow = self._create_flow(data={CONF_NATIVE_AUTH: True})
        result = await flow.async_step_init(user_input=None)

        schema_keys = {str(k): k for k in result["data_schema"].schema}
        assert schema_keys[CONF_NATIVE_AUTH].default() is True

    async def test_init_step_updates_entry_data(self):
        """Test init step merges user input into config entry data."""
        flow = self._create_flow(data={CONF_NATIVE_AUTH: False})

        result = await flow.async_step_init(user_input={CONF_NATIVE_AUTH: True})

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        flow.hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = flow.hass.config_entries.async_update_entry.call_args
        assert call_kwargs[1]["data"][CONF_NATIVE_AUTH] is True

    async def test_init_step_defaults_to_false(self):
        """Test init step defaults native_auth_enabled to False."""
        flow = self._create_flow(data={})
        result = await flow.async_step_init(user_input=None)

        schema_keys = {str(k): k for k in result["data_schema"].schema}
        assert schema_keys[CONF_NATIVE_AUTH].default() is False

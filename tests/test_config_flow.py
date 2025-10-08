"""Test config flow for MCP Server integration."""

from unittest.mock import Mock, patch

from homeassistant import data_entry_flow

from custom_components.mcp_server.config_flow import (
    MCPServerConfigFlow,
    MCPServerOptionsFlowHandler,
)


class TestMCPServerConfigFlow:
    """Test the MCP Server config flow."""

    async def test_user_flow_creates_entry(self):
        """Test user flow creates config entry when OIDC provider exists."""
        mock_hass = Mock()
        mock_hass.config = Mock()
        mock_hass.config.entries = Mock()
        mock_hass.config.entries.async_domains = Mock(return_value=["oidc_provider"])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input={})

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "MCP Server"
        assert result["data"] == {}

    async def test_user_flow_shows_form_when_no_input(self):
        """Test user flow shows form when no input provided."""
        mock_hass = Mock()
        mock_hass.config = Mock()
        mock_hass.config.entries = Mock()
        mock_hass.config.entries.async_domains = Mock(return_value=["oidc_provider"])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input=None)

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["data_schema"] is not None

    async def test_user_flow_aborts_when_oidc_provider_missing(self):
        """Test user flow aborts when OIDC provider is not installed."""
        mock_hass = Mock()
        mock_hass.config = Mock()
        mock_hass.config.entries = Mock()
        mock_hass.config.entries.async_domains = Mock(return_value=[])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input={})

        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "oidc_provider_required"

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

    async def test_user_flow_form_has_empty_schema(self):
        """Test user flow form has empty data schema."""
        mock_hass = Mock()
        mock_hass.config = Mock()
        mock_hass.config.entries = Mock()
        mock_hass.config.entries.async_domains = Mock(return_value=["oidc_provider"])

        flow = MCPServerConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user(user_input=None)

        # Verify the schema is empty (no user input required)
        assert result["data_schema"].schema == {}


class TestMCPServerOptionsFlow:
    """Test the MCP Server options flow."""

    async def test_init_step_shows_form(self):
        """Test init step shows form."""
        mock_config_entry = Mock()

        flow = MCPServerOptionsFlowHandler.__new__(MCPServerOptionsFlowHandler)
        flow._config_entry = mock_config_entry

        result = await flow.async_step_init(user_input=None)

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

    async def test_init_step_with_user_input_creates_entry(self):
        """Test init step with user input creates entry."""
        mock_config_entry = Mock()

        flow = MCPServerOptionsFlowHandler.__new__(MCPServerOptionsFlowHandler)
        flow._config_entry = mock_config_entry

        result = await flow.async_step_init(user_input={})

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == ""
        assert result["data"] == {}

    async def test_init_step_form_has_empty_schema(self):
        """Test init step form has empty data schema."""
        mock_config_entry = Mock()

        flow = MCPServerOptionsFlowHandler.__new__(MCPServerOptionsFlowHandler)
        flow._config_entry = mock_config_entry

        result = await flow.async_step_init(user_input=None)

        # Verify the schema is empty (no user input required)
        assert result["data_schema"].schema == {}

    def test_options_flow_stores_config_entry(self):
        """Test options flow stores config entry."""
        mock_config_entry = Mock()

        # Use internal attribute to avoid deprecated setter
        flow = MCPServerOptionsFlowHandler.__new__(MCPServerOptionsFlowHandler)
        flow._config_entry = mock_config_entry

        assert flow.config_entry == mock_config_entry

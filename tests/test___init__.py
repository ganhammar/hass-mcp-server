"""Test __init__.py for MCP Server integration."""

from unittest.mock import Mock, patch

from custom_components.mcp_server import (
    DOMAIN,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)


class TestAsyncSetup:
    """Test async_setup function."""

    async def test_async_setup_initializes_domain_data(self, mock_hass):
        """Test async_setup initializes domain data."""
        result = await async_setup(mock_hass, {})

        assert result is True
        assert DOMAIN in mock_hass.data
        assert mock_hass.data[DOMAIN] == {}


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @patch("custom_components.mcp_server.Server")
    @patch("custom_components.mcp_server.MCPEndpointView")
    @patch("custom_components.mcp_server.MCPProtectedResourceMetadataView")
    @patch("custom_components.mcp_server.MCPSubpathProtectedResourceMetadataView")
    async def test_async_setup_entry_initializes_server(
        self,
        mock_subpath_view,
        mock_metadata_view,
        mock_endpoint_view,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test async_setup_entry initializes MCP server."""
        mock_server = Mock()
        mock_server_class.return_value = mock_server

        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is True
        assert DOMAIN in mock_hass.data
        assert "server" in mock_hass.data[DOMAIN]
        assert mock_hass.data[DOMAIN]["server"] == mock_server
        mock_server_class.assert_called_once_with("home-assistant-mcp-server")

    @patch("custom_components.mcp_server.Server")
    @patch("custom_components.mcp_server.MCPEndpointView")
    @patch("custom_components.mcp_server.MCPProtectedResourceMetadataView")
    @patch("custom_components.mcp_server.MCPSubpathProtectedResourceMetadataView")
    async def test_async_setup_entry_registers_views(
        self,
        mock_subpath_view,
        mock_metadata_view,
        mock_endpoint_view,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test async_setup_entry registers HTTP views."""
        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is True
        assert mock_hass.http.register_view.call_count == 3

    @patch("custom_components.mcp_server.Server")
    @patch("custom_components.mcp_server.MCPEndpointView")
    @patch("custom_components.mcp_server.MCPProtectedResourceMetadataView")
    @patch("custom_components.mcp_server.MCPSubpathProtectedResourceMetadataView")
    async def test_async_setup_entry_registers_protected_resource_views(
        self,
        mock_subpath_view_class,
        mock_metadata_view_class,
        mock_endpoint_view,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test async_setup_entry registers protected resource metadata views."""
        mock_metadata_view = Mock()
        mock_subpath_view = Mock()
        mock_metadata_view_class.return_value = mock_metadata_view
        mock_subpath_view_class.return_value = mock_subpath_view

        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is True
        mock_metadata_view_class.assert_called_once()
        mock_subpath_view_class.assert_called_once()

    @patch("custom_components.mcp_server.Server")
    @patch("custom_components.mcp_server.MCPEndpointView")
    @patch("custom_components.mcp_server.MCPProtectedResourceMetadataView")
    @patch("custom_components.mcp_server.MCPSubpathProtectedResourceMetadataView")
    async def test_async_setup_entry_registers_endpoint_view(
        self,
        mock_subpath_view,
        mock_metadata_view,
        mock_endpoint_view_class,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test async_setup_entry registers endpoint view with hass and server."""
        mock_server = Mock()
        mock_server_class.return_value = mock_server
        mock_endpoint_view = Mock()
        mock_endpoint_view_class.return_value = mock_endpoint_view

        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is True
        mock_endpoint_view_class.assert_called_once_with(mock_hass, mock_server)


class TestAsyncUnloadEntry:
    """Test async_unload_entry function."""

    async def test_async_unload_entry_clears_data(self, mock_hass, mock_config_entry):
        """Test async_unload_entry clears domain data."""
        mock_hass.data[DOMAIN] = {"server": Mock()}

        result = await async_unload_entry(mock_hass, mock_config_entry)

        assert result is True
        assert len(mock_hass.data[DOMAIN]) == 0

"""Test __init__.py for MCP Server integration."""

from unittest.mock import AsyncMock, Mock, patch

from custom_components.mcp_server_http_transport import (
    DOMAIN,
    _async_report_endpoint_conflict,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.mcp_server_http_transport.const import (
    ISSUE_ENDPOINT_CONFLICT,
    MCP_HTTP_PATH,
    MCP_PATH,
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

    @patch("custom_components.mcp_server_http_transport.Server")
    @patch("custom_components.mcp_server_http_transport.register_mcp_views", return_value=False)
    async def test_async_setup_entry_initializes_server(
        self,
        mock_register_views,
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

    @patch("custom_components.mcp_server_http_transport.Server")
    @patch("custom_components.mcp_server_http_transport.register_mcp_views", return_value=False)
    async def test_async_setup_entry_registers_views(
        self,
        mock_register_views,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test async_setup_entry registers the HTTP views."""
        mock_server = Mock()
        mock_server_class.return_value = mock_server

        result = await async_setup_entry(mock_hass, mock_config_entry)

        assert result is True
        mock_register_views.assert_called_once_with(mock_hass, mock_server, False)

    @patch("custom_components.mcp_server_http_transport.Server")
    @patch("custom_components.mcp_server_http_transport.register_mcp_views", return_value=False)
    async def test_async_setup_entry_passes_native_auth_enabled(
        self,
        mock_register_views,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test async_setup_entry passes native_auth_enabled on to registration."""
        mock_config_entry.data = {"native_auth_enabled": True}
        mock_server = Mock()
        mock_server_class.return_value = mock_server

        await async_setup_entry(mock_hass, mock_config_entry)

        mock_register_views.assert_called_once_with(mock_hass, mock_server, True)

    @patch("custom_components.mcp_server_http_transport.Server")
    @patch("custom_components.mcp_server_http_transport.register_mcp_views", return_value=False)
    async def test_async_setup_entry_image_access_defaults_off(
        self,
        mock_register_views,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test camera and image file access default to disabled in hass.data."""
        await async_setup_entry(mock_hass, mock_config_entry)

        assert mock_hass.data[DOMAIN]["camera_image_access"] is False
        assert mock_hass.data[DOMAIN]["image_file_access"] is False

    @patch("custom_components.mcp_server_http_transport.Server")
    @patch("custom_components.mcp_server_http_transport.register_mcp_views", return_value=False)
    async def test_async_setup_entry_wires_image_access_flags(
        self,
        mock_register_views,
        mock_server_class,
        mock_hass,
        mock_config_entry,
    ):
        """Test camera and image file access flags are read into hass.data."""
        mock_config_entry.data = {
            "camera_image_access_enabled": True,
            "image_file_access_enabled": True,
        }

        await async_setup_entry(mock_hass, mock_config_entry)

        assert mock_hass.data[DOMAIN]["camera_image_access"] is True
        assert mock_hass.data[DOMAIN]["image_file_access"] is True


class TestUpdateListener:
    """Test config entry update listener."""

    async def test_update_listener_reloads_entry(self, mock_hass, mock_config_entry):
        """Test _async_update_listener triggers a reload."""
        from custom_components.mcp_server_http_transport import _async_update_listener

        mock_hass.config_entries = Mock()
        mock_hass.config_entries.async_reload = AsyncMock()

        await _async_update_listener(mock_hass, mock_config_entry)

        mock_hass.config_entries.async_reload.assert_called_once_with(mock_config_entry.entry_id)


class TestAsyncUnloadEntry:
    """Test async_unload_entry function."""

    @patch("custom_components.mcp_server_http_transport.ir")
    async def test_async_unload_entry_clears_data(self, mock_ir, mock_hass, mock_config_entry):
        """Test async_unload_entry clears domain data."""
        mock_hass.data[DOMAIN] = {"server": Mock()}

        result = await async_unload_entry(mock_hass, mock_config_entry)

        assert result is True
        assert len(mock_hass.data[DOMAIN]) == 0

    @patch("custom_components.mcp_server_http_transport.ir")
    async def test_async_unload_entry_drops_the_conflict_issue(
        self, mock_ir, mock_hass, mock_config_entry
    ):
        """An unloaded integration no longer conflicts with anything."""
        mock_hass.data[DOMAIN] = {"server": Mock()}

        await async_unload_entry(mock_hass, mock_config_entry)

        mock_ir.async_delete_issue.assert_called_once_with(
            mock_hass, DOMAIN, ISSUE_ENDPOINT_CONFLICT
        )


class TestEndpointConflictIssue:
    """Test the repair raised when another integration also serves /api/mcp."""

    @patch("custom_components.mcp_server_http_transport.ir")
    @patch("custom_components.mcp_server_http_transport.mcp_path_is_contested", return_value=True)
    def test_conflict_raises_issue(self, mock_contested, mock_ir, mock_hass):
        """A contested path raises a repair naming both paths."""
        _async_report_endpoint_conflict(mock_hass)

        mock_ir.async_create_issue.assert_called_once()
        args, kwargs = mock_ir.async_create_issue.call_args
        assert args == (mock_hass, DOMAIN, ISSUE_ENDPOINT_CONFLICT)
        assert kwargs["translation_key"] == ISSUE_ENDPOINT_CONFLICT
        assert kwargs["translation_placeholders"] == {
            "path": MCP_PATH,
            "dedicated_path": MCP_HTTP_PATH,
        }
        assert kwargs["severity"] is mock_ir.IssueSeverity.WARNING
        assert kwargs["is_fixable"] is False

    @patch("custom_components.mcp_server_http_transport.ir")
    @patch("custom_components.mcp_server_http_transport.mcp_path_is_contested", return_value=False)
    def test_no_conflict_clears_issue(self, mock_contested, mock_ir, mock_hass):
        """An uncontested path clears any issue left from an earlier run."""
        _async_report_endpoint_conflict(mock_hass)

        mock_ir.async_create_issue.assert_not_called()
        mock_ir.async_delete_issue.assert_called_once_with(
            mock_hass, DOMAIN, ISSUE_ENDPOINT_CONFLICT
        )

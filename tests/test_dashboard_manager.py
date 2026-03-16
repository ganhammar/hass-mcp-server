"""Tests for dashboard_manager helpers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.dashboard_manager import (
    _register_panel,
    _resolve_url_path,
    create_dashboard,
    delete_dashboard,
    delete_dashboard_config,
    get_dashboard_config,
    list_dashboards,
    save_dashboard_config,
    update_dashboard,
)

# The actual value of homeassistant.components.lovelace.const.LOVELACE_DATA
LOVELACE_KEY = "lovelace"

# Patch targets at source modules (lazy imports resolve from these)
_COLLECTION_CLS = "homeassistant.components.lovelace.dashboard.DashboardsCollection"
_STORAGE_CLS = "homeassistant.components.lovelace.dashboard.LovelaceStorage"
_FRONTEND = "homeassistant.components.frontend"
_REGISTER_PANEL = "custom_components.mcp_server_http_transport.dashboard_manager._register_panel"


def _make_hass(dashboards: dict) -> Mock:
    """Create a mock hass with a lovelace data dict."""
    hass = Mock()
    lovelace_data = Mock()
    lovelace_data.dashboards = dashboards
    hass.data = {LOVELACE_KEY: lovelace_data}
    return hass


class TestResolveUrlPath:
    """Tests for _resolve_url_path."""

    def test_default_maps_to_none(self):
        assert _resolve_url_path("default") is None

    def test_custom_path_passes_through(self):
        assert _resolve_url_path("my-dashboard") == "my-dashboard"

    def test_empty_string_passes_through(self):
        assert _resolve_url_path("") == ""


class TestRegisterPanel:
    """Tests for _register_panel."""

    def test_registers_panel_with_sidebar(self):
        hass = Mock()
        config = {
            "title": "My Dash",
            "icon": "mdi:flash",
            "require_admin": False,
            "show_in_sidebar": True,
        }

        with patch(_FRONTEND) as mock_frontend:
            _register_panel(hass, "my-dash", config)

        mock_frontend.async_register_built_in_panel.assert_called_once_with(
            hass,
            "lovelace",
            frontend_url_path="my-dash",
            require_admin=False,
            config={"mode": "storage"},
            update=False,
            sidebar_title="My Dash",
            sidebar_icon="mdi:flash",
        )

    def test_registers_panel_without_sidebar(self):
        hass = Mock()
        config = {"title": "Hidden", "show_in_sidebar": False}

        with patch(_FRONTEND) as mock_frontend:
            _register_panel(hass, "hidden", config)

        call_kwargs = mock_frontend.async_register_built_in_panel.call_args[1]
        assert "sidebar_title" not in call_kwargs
        assert "sidebar_icon" not in call_kwargs

    def test_registers_panel_with_update_flag(self):
        hass = Mock()
        config = {"title": "Updated"}

        with patch(_FRONTEND) as mock_frontend:
            _register_panel(hass, "dash", config, update=True)

        call_kwargs = mock_frontend.async_register_built_in_panel.call_args[1]
        assert call_kwargs["update"] is True

    def test_swallows_registration_exception(self):
        hass = Mock()
        config = {"title": "Broken"}

        with patch(_FRONTEND) as mock_frontend:
            mock_frontend.async_register_built_in_panel.side_effect = Exception("boom")
            _register_panel(hass, "broken", config)  # should not raise


class TestListDashboards:
    """Tests for list_dashboards."""

    async def test_returns_metadata_for_all_dashboards(self):
        default_dashboard = Mock()
        default_dashboard.config = {"mode": "storage", "title": "Home", "icon": "mdi:home"}

        custom_dashboard = Mock()
        custom_dashboard.config = {
            "mode": "storage",
            "title": "Energy",
            "icon": "mdi:flash",
            "show_in_sidebar": True,
            "require_admin": False,
        }

        hass = _make_hass({None: default_dashboard, "energy": custom_dashboard})
        result = await list_dashboards(hass)

        assert len(result) == 2
        url_paths = [d["url_path"] for d in result]
        assert "default" in url_paths
        assert "energy" in url_paths

    async def test_handles_dashboard_with_none_config(self):
        dashboard = Mock()
        dashboard.config = None

        hass = _make_hass({None: dashboard})
        result = await list_dashboards(hass)

        assert len(result) == 1
        assert result[0]["url_path"] == "default"
        assert result[0]["mode"] == "storage"
        assert "title" not in result[0]

    async def test_returns_empty_list_when_no_dashboards(self):
        hass = _make_hass({})
        result = await list_dashboards(hass)
        assert result == []


class TestGetDashboardConfig:
    """Tests for get_dashboard_config."""

    async def test_loads_config_for_existing_dashboard(self):
        dashboard = AsyncMock()
        dashboard.async_load.return_value = {"views": [{"title": "Home"}]}

        hass = _make_hass({None: dashboard})
        result = await get_dashboard_config(hass, "default")

        assert result == {"views": [{"title": "Home"}]}
        dashboard.async_load.assert_called_once_with(force=False)

    async def test_loads_config_for_custom_dashboard(self):
        dashboard = AsyncMock()
        dashboard.async_load.return_value = {"views": [{"title": "Energy"}]}

        hass = _make_hass({"energy": dashboard})
        result = await get_dashboard_config(hass, "energy")

        assert result == {"views": [{"title": "Energy"}]}

    async def test_raises_for_nonexistent_dashboard(self):
        hass = _make_hass({})
        with pytest.raises(ValueError, match="not found"):
            await get_dashboard_config(hass, "nonexistent")

    async def test_returns_empty_dict_when_config_is_none(self):
        dashboard = AsyncMock()
        dashboard.async_load.return_value = None

        hass = _make_hass({None: dashboard})
        result = await get_dashboard_config(hass, "default")

        assert result == {}

    async def test_raises_on_load_failure(self):
        dashboard = AsyncMock()
        dashboard.async_load.side_effect = Exception("IO error")

        hass = _make_hass({None: dashboard})
        with pytest.raises(ValueError, match="Failed to load config"):
            await get_dashboard_config(hass, "default")


class TestSaveDashboardConfig:
    """Tests for save_dashboard_config."""

    async def test_saves_config_successfully(self):
        dashboard = AsyncMock()
        hass = _make_hass({"energy": dashboard})

        new_config = {"views": [{"title": "New Energy"}]}
        await save_dashboard_config(hass, "energy", new_config)

        dashboard.async_save.assert_called_once_with(new_config)

    async def test_raises_for_nonexistent_dashboard(self):
        hass = _make_hass({})
        with pytest.raises(ValueError, match="not found"):
            await save_dashboard_config(hass, "nonexistent", {})

    async def test_raises_on_save_failure(self):
        dashboard = AsyncMock()
        dashboard.async_save.side_effect = Exception("Write error")

        hass = _make_hass({"energy": dashboard})
        with pytest.raises(ValueError, match="Failed to save config"):
            await save_dashboard_config(hass, "energy", {})


class TestDeleteDashboardConfig:
    """Tests for delete_dashboard_config."""

    async def test_deletes_config_successfully(self):
        dashboard = AsyncMock()
        hass = _make_hass({"energy": dashboard})

        await delete_dashboard_config(hass, "energy")
        dashboard.async_delete.assert_called_once()

    async def test_raises_for_nonexistent_dashboard(self):
        hass = _make_hass({})
        with pytest.raises(ValueError, match="not found"):
            await delete_dashboard_config(hass, "nonexistent")

    async def test_raises_on_delete_failure(self):
        dashboard = AsyncMock()
        dashboard.async_delete.side_effect = Exception("Delete error")

        hass = _make_hass({"energy": dashboard})
        with pytest.raises(ValueError, match="Failed to delete config"):
            await delete_dashboard_config(hass, "energy")


class TestCreateDashboard:
    """Tests for create_dashboard."""

    async def test_creates_dashboard_successfully(self):
        hass = _make_hass({})

        mock_collection = AsyncMock()
        created_item = {
            "id": "abc123",
            "url_path": "my-dash",
            "title": "My Dashboard",
            "require_admin": False,
            "show_in_sidebar": True,
        }
        mock_collection.async_create_item.return_value = created_item

        mock_storage = Mock()

        with (
            patch(_COLLECTION_CLS, return_value=mock_collection),
            patch(_STORAGE_CLS, return_value=mock_storage),
            patch(_REGISTER_PANEL) as mock_reg,
        ):
            result = await create_dashboard(hass, "my-dash", "My Dashboard")

        assert result["url_path"] == "my-dash"
        assert result["title"] == "My Dashboard"
        mock_collection.async_create_item.assert_called_once()
        assert hass.data[LOVELACE_KEY].dashboards["my-dash"] == mock_storage
        mock_reg.assert_called_once_with(hass, "my-dash", created_item)

    async def test_rejects_default_url_path(self):
        hass = Mock()
        with pytest.raises(ValueError, match="Cannot create the default dashboard"):
            await create_dashboard(hass, "default", "Default")

    async def test_passes_icon_when_provided(self):
        hass = _make_hass({})

        mock_collection = AsyncMock()
        mock_collection.async_create_item.return_value = {
            "id": "abc",
            "url_path": "dash",
            "title": "Dash",
            "icon": "mdi:flash",
        }

        with (
            patch(_COLLECTION_CLS, return_value=mock_collection),
            patch(_STORAGE_CLS),
            patch(_REGISTER_PANEL),
        ):
            await create_dashboard(hass, "dash", "Dash", icon="mdi:flash")

        call_data = mock_collection.async_create_item.call_args[0][0]
        assert call_data["icon"] == "mdi:flash"


class TestUpdateDashboard:
    """Tests for update_dashboard."""

    async def test_updates_dashboard_successfully(self):
        dashboard_obj = Mock()
        hass = _make_hass({"my-dash": dashboard_obj})

        mock_collection = AsyncMock()
        mock_collection.data = {
            "abc123": {"url_path": "my-dash", "title": "Old Title"},
        }
        updated_item = {"url_path": "my-dash", "title": "New Title"}
        mock_collection.async_update_item.return_value = updated_item

        with (
            patch(_COLLECTION_CLS, return_value=mock_collection),
            patch(_REGISTER_PANEL) as mock_reg,
        ):
            result = await update_dashboard(hass, "my-dash", title="New Title")

        assert result["title"] == "New Title"
        mock_collection.async_update_item.assert_called_once_with("abc123", {"title": "New Title"})
        mock_reg.assert_called_once_with(hass, "my-dash", updated_item, update=True)

    async def test_rejects_default_url_path(self):
        hass = Mock()
        with pytest.raises(ValueError, match="Cannot update the default dashboard"):
            await update_dashboard(hass, "default", title="X")

    async def test_raises_when_not_found_in_collection(self):
        hass = _make_hass({})

        mock_collection = AsyncMock()
        mock_collection.data = {}

        with (
            patch(_COLLECTION_CLS, return_value=mock_collection),
            pytest.raises(ValueError, match="not found in collection"),
        ):
            await update_dashboard(hass, "nonexistent", title="X")


class TestDeleteDashboard:
    """Tests for delete_dashboard."""

    async def test_deletes_dashboard_successfully(self):
        dashboard_obj = AsyncMock()
        hass = _make_hass({"my-dash": dashboard_obj})

        mock_collection = AsyncMock()
        mock_collection.data = {
            "abc123": {"url_path": "my-dash", "title": "My Dashboard"},
        }

        with (
            patch(_COLLECTION_CLS, return_value=mock_collection),
            patch(_FRONTEND) as mock_frontend,
        ):
            await delete_dashboard(hass, "my-dash")

        mock_collection.async_delete_item.assert_called_once_with("abc123")
        mock_frontend.async_remove_panel.assert_called_once_with(hass, "my-dash")
        assert "my-dash" not in hass.data[LOVELACE_KEY].dashboards
        dashboard_obj.async_delete.assert_called_once()

    async def test_rejects_default_url_path(self):
        hass = Mock()
        with pytest.raises(ValueError, match="Cannot delete the default dashboard"):
            await delete_dashboard(hass, "default")

    async def test_raises_when_not_found_in_collection(self):
        hass = _make_hass({})

        mock_collection = AsyncMock()
        mock_collection.data = {}

        with (
            patch(_COLLECTION_CLS, return_value=mock_collection),
            pytest.raises(ValueError, match="not found in collection"),
        ):
            await delete_dashboard(hass, "nonexistent")

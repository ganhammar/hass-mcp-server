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
    patch_dashboard_config,
    save_dashboard_config,
    summarize_dashboard_config,
    summarize_view,
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


class TestPatchDashboardConfig:
    """Tests for patch_dashboard_config."""

    @staticmethod
    def _dashboard_with(config: dict) -> AsyncMock:
        dashboard = AsyncMock()
        dashboard.async_load.return_value = config
        return dashboard

    async def test_saves_only_the_patched_result(self):
        dashboard = self._dashboard_with(
            {"views": [{"title": "Home", "cards": [{"type": "tile", "entity": "light.a"}]}]}
        )
        hass = _make_hass({"energy": dashboard})

        result = await patch_dashboard_config(
            hass,
            "energy",
            [{"op": "replace", "path": "/views/0/cards/0/entity", "value": "light.b"}],
        )

        assert result["views"][0]["cards"][0]["entity"] == "light.b"
        dashboard.async_save.assert_called_once_with(result)

    async def test_moves_a_card_between_views(self):
        dashboard = self._dashboard_with(
            {
                "views": [
                    {"title": "Living", "cards": [{"type": "tile", "entity": "fan.purifier"}]},
                    {"title": "Bedroom", "cards": []},
                ]
            }
        )
        hass = _make_hass({None: dashboard})

        result = await patch_dashboard_config(
            hass,
            "default",
            [{"op": "move", "from": "/views/0/cards/0", "path": "/views/1/cards/-"}],
        )

        assert result["views"][0]["cards"] == []
        assert result["views"][1]["cards"][0]["entity"] == "fan.purifier"

    async def test_does_not_save_when_an_operation_fails(self):
        dashboard = self._dashboard_with({"views": [{"title": "Home", "cards": []}]})
        hass = _make_hass({"energy": dashboard})

        with pytest.raises(ValueError, match="test failed"):
            await patch_dashboard_config(
                hass,
                "energy",
                [{"op": "test", "path": "/views/0/title", "value": "Away"}],
            )

        dashboard.async_save.assert_not_called()

    async def test_does_not_mutate_the_loaded_config(self):
        loaded = {"views": [{"title": "Home", "cards": [{"type": "tile"}]}]}
        dashboard = self._dashboard_with(loaded)
        hass = _make_hass({"energy": dashboard})

        await patch_dashboard_config(hass, "energy", [{"op": "remove", "path": "/views/0/cards/0"}])

        assert loaded["views"][0]["cards"] == [{"type": "tile"}]

    async def test_rejects_a_patch_that_produces_a_non_object(self):
        dashboard = self._dashboard_with({"views": []})
        hass = _make_hass({"energy": dashboard})

        with pytest.raises(ValueError, match="must be an object"):
            await patch_dashboard_config(
                hass, "energy", [{"op": "replace", "path": "", "value": []}]
            )

        dashboard.async_save.assert_not_called()

    async def test_raises_for_nonexistent_dashboard(self):
        hass = _make_hass({})
        with pytest.raises(ValueError, match="not found"):
            await patch_dashboard_config(
                hass, "nonexistent", [{"op": "remove", "path": "/views/0"}]
            )


class TestSummarizeDashboardConfig:
    """Tests for summarize_dashboard_config and its helpers."""

    def test_summarizes_views_and_cards_with_pointers(self):
        config = {
            "views": [
                {
                    "title": "Living Room",
                    "path": "living",
                    "icon": "mdi:sofa",
                    "cards": [
                        {"type": "tile", "entity": "light.sofa", "name": "Sofa"},
                        {"type": "tile", "entity": "fan.air_purifier"},
                    ],
                }
            ]
        }

        result = summarize_dashboard_config(config)

        assert result["view_count"] == 1
        view = result["views"][0]
        assert view["pointer"] == "/views/0"
        assert view["title"] == "Living Room"
        assert view["path"] == "living"
        assert view["card_count"] == 2
        assert view["cards"][1]["pointer"] == "/views/0/cards/1"
        assert view["cards"][1]["entity"] == "fan.air_purifier"
        assert view["cards"][0]["label"] == "Sofa"

    def test_omits_card_options(self):
        config = {
            "views": [
                {
                    "title": "Home",
                    "cards": [
                        {
                            "type": "tile",
                            "entity": "light.a",
                            "features": [{"type": "light-brightness"}],
                            "vertical": True,
                        }
                    ],
                }
            ]
        }

        card = summarize_dashboard_config(config)["views"][0]["cards"][0]

        assert card["type"] == "tile"
        assert "features" not in card
        assert "vertical" not in card

    def test_summarizes_sections_view(self):
        config = {
            "views": [
                {
                    "title": "Home",
                    "type": "sections",
                    "sections": [
                        {
                            "type": "grid",
                            "title": "Lights",
                            "cards": [{"type": "tile", "entity": "light.a"}],
                        }
                    ],
                }
            ]
        }

        view = summarize_dashboard_config(config)["views"][0]

        assert view["section_count"] == 1
        section = view["sections"][0]
        assert section["pointer"] == "/views/0/sections/0"
        assert section["title"] == "Lights"
        assert section["cards"][0]["pointer"] == "/views/0/sections/0/cards/0"

    def test_summarizes_nested_stack_cards(self):
        config = {
            "views": [
                {
                    "title": "Home",
                    "cards": [
                        {
                            "type": "vertical-stack",
                            "cards": [
                                {"type": "tile", "entity": "light.a"},
                                {"type": "tile", "entity": "light.b"},
                            ],
                        }
                    ],
                }
            ]
        }

        stack = summarize_dashboard_config(config)["views"][0]["cards"][0]

        assert stack["card_count"] == 2
        assert stack["cards"][1]["pointer"] == "/views/0/cards/0/cards/1"
        assert stack["cards"][1]["entity"] == "light.b"

    def test_truncates_beyond_the_nesting_limit(self):
        card: dict = {"type": "tile", "entity": "light.deep"}
        for _ in range(8):
            card = {"type": "vertical-stack", "cards": [card]}

        result = summarize_dashboard_config({"views": [{"title": "Home", "cards": [card]}]})

        node = result["views"][0]["cards"][0]
        depth = 0
        while "cards" in node:
            node = node["cards"][0]
            depth += 1
        assert node["truncated"] is True
        assert depth == 6

    def test_caps_the_entities_it_lists(self):
        entities = [f"light.l{i}" for i in range(14)]
        config = {
            "views": [{"title": "Home", "cards": [{"type": "entities", "entities": entities}]}]
        }

        card = summarize_dashboard_config(config)["views"][0]["cards"][0]

        assert card["entity_count"] == 14
        assert len(card["entities"]) == 10

    def test_reads_entity_ids_from_row_objects(self):
        config = {
            "views": [
                {
                    "title": "Home",
                    "cards": [
                        {
                            "type": "entities",
                            "entities": [{"entity": "light.a", "name": "A"}, "light.b"],
                        }
                    ],
                }
            ]
        }

        card = summarize_dashboard_config(config)["views"][0]["cards"][0]
        assert card["entities"] == ["light.a", "light.b"]

    def test_summarizes_badges(self):
        config = {
            "views": [{"title": "Home", "badges": [{"type": "entity", "entity": "person.me"}]}]
        }

        view = summarize_dashboard_config(config)["views"][0]

        assert view["badge_count"] == 1
        assert view["badges"][0]["pointer"] == "/views/0/badges/0"

    def test_handles_config_without_views(self):
        assert summarize_dashboard_config({}) == {"views": [], "view_count": 0}

    def test_handles_malformed_entries(self):
        config = {
            "views": [
                "not-a-view",
                {"title": "Home", "cards": ["not-a-card"], "sections": ["not-a-section"]},
            ]
        }

        result = summarize_dashboard_config(config)

        assert result["views"][0]["value"] == "not-a-view"
        assert result["views"][1]["cards"][0]["value"] == "not-a-card"
        assert result["views"][1]["sections"][0]["value"] == "not-a-section"

    def test_rejects_non_object_config(self):
        with pytest.raises(ValueError, match="must be an object"):
            summarize_dashboard_config([])

    def test_summarize_view_uses_the_given_pointer(self):
        view = {"title": "Bedroom", "cards": [{"type": "tile", "entity": "light.bed"}]}

        result = summarize_view(view, ["views", "3"], 3)

        assert result["pointer"] == "/views/3"
        assert result["index"] == 3
        assert result["cards"][0]["pointer"] == "/views/3/cards/0"


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

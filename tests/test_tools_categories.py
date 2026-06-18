"""Tests for category definition CRUD tools and shared assignment helpers."""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from custom_components.mcp_server_http_transport.tools.categories import (
    create_category,
    delete_category,
    list_categories,
    resolve_category_id,
    write_entity_category,
)

CR_GET = "custom_components.mcp_server_http_transport.tools.categories.cr.async_get"
ER_GET = "custom_components.mcp_server_http_transport.tools.categories.er.async_get"


def _make_category(category_id: str = "c1", name: str = "Lighting") -> Mock:
    """Create a mock CategoryEntry."""
    entry = Mock(
        category_id=category_id,
        icon="mdi:lightbulb",
        created_at=datetime(2024, 1, 1),
        modified_at=datetime(2024, 1, 1),
    )
    entry.name = name  # `name` is a reserved Mock constructor kwarg; set it explicitly.
    return entry


class TestCreateCategory:
    """Tests for the create_category tool."""

    async def test_create_category_valid(self):
        """create_category calls async_create with the scope, name, icon."""
        reg = Mock()
        reg.async_create.return_value = _make_category()

        with patch(CR_GET, return_value=reg):
            result = await create_category(
                Mock(),
                {"scope": "automation", "name": "Lighting", "icon": "mdi:lightbulb"},
            )

        reg.async_create.assert_called_once_with(
            scope="automation", name="Lighting", icon="mdi:lightbulb"
        )
        assert "c1" in result["content"][0]["text"]

    async def test_create_category_bad_scope(self):
        """create_category rejects unknown scopes and never touches the registry."""
        reg = Mock()

        with patch(CR_GET, return_value=reg):
            result = await create_category(Mock(), {"scope": "light", "name": "X"})

        assert "Unknown scope" in result["content"][0]["text"]
        reg.async_create.assert_not_called()

    async def test_create_category_duplicate(self):
        """create_category surfaces a duplicate-name ValueError."""
        reg = Mock()
        reg.async_create.side_effect = ValueError("The name 'X' is already in use")

        with patch(CR_GET, return_value=reg):
            result = await create_category(Mock(), {"scope": "scene", "name": "X"})

        assert "already in use" in result["content"][0]["text"]


class TestListCategories:
    """Tests for the list_categories tool."""

    async def test_list_categories(self):
        """list_categories returns JSON entries with ISO timestamps."""
        reg = Mock()
        reg.async_list_categories.return_value = [_make_category()]

        with patch(CR_GET, return_value=reg):
            result = await list_categories(Mock(), {"scope": "entity"})

        reg.async_list_categories.assert_called_once_with(scope="entity")
        body = json.loads(result["content"][0]["text"])
        assert body[0]["category_id"] == "c1"
        assert body[0]["name"] == "Lighting"
        assert body[0]["icon"] == "mdi:lightbulb"
        assert body[0]["created_at"].startswith("2024-01-01")

    async def test_list_categories_bad_scope(self):
        """list_categories rejects unknown scopes."""
        with patch(CR_GET, return_value=Mock()):
            result = await list_categories(Mock(), {"scope": "device"})

        assert "Unknown scope" in result["content"][0]["text"]


class TestDeleteCategory:
    """Tests for the delete_category tool."""

    async def test_delete_category(self):
        """delete_category calls async_delete with scope and category_id."""
        reg = Mock()

        with patch(CR_GET, return_value=reg):
            result = await delete_category(Mock(), {"scope": "script", "category_id": "c1"})

        reg.async_delete.assert_called_once_with(scope="script", category_id="c1")
        assert "Deleted category c1" in result["content"][0]["text"]


class TestResolveCategoryId:
    """Tests for the resolve_category_id helper."""

    def test_resolve_case_insensitive(self):
        """resolve_category_id matches names case-insensitively."""
        reg = Mock()
        reg.async_list_categories.return_value = [_make_category()]

        with patch(CR_GET, return_value=reg):
            assert resolve_category_id(Mock(), "entity", "lIgHtInG") == "c1"

    def test_resolve_missing(self):
        """resolve_category_id raises with a create_category hint when absent."""
        reg = Mock()
        reg.async_list_categories.return_value = []

        with patch(CR_GET, return_value=reg):
            with pytest.raises(ValueError, match="create_category"):
                resolve_category_id(Mock(), "entity", "Nope")


class TestWriteEntityCategory:
    """Tests for the write_entity_category helper."""

    def test_write_sets_scope_preserving_others(self):
        """Setting a category keeps pre-existing other-scope keys intact."""
        ent_reg = Mock()
        ent_reg.async_get.return_value = Mock(categories={"automation": "x"})

        with patch(ER_GET, return_value=ent_reg):
            write_entity_category(Mock(), "input_boolean.flag", "entity", "c1")

        _, kwargs = ent_reg.async_update_entity.call_args
        assert kwargs["categories"] == {"automation": "x", "entity": "c1"}

    def test_write_clears_scope(self):
        """Passing None removes only that scope's key."""
        ent_reg = Mock()
        ent_reg.async_get.return_value = Mock(categories={"automation": "x", "entity": "c1"})

        with patch(ER_GET, return_value=ent_reg):
            write_entity_category(Mock(), "input_boolean.flag", "entity", None)

        _, kwargs = ent_reg.async_update_entity.call_args
        assert kwargs["categories"] == {"automation": "x"}

    def test_write_entity_missing(self):
        """Missing entity raises a clear ValueError."""
        ent_reg = Mock()
        ent_reg.async_get.return_value = None

        with patch(ER_GET, return_value=ent_reg):
            with pytest.raises(ValueError, match="not found"):
                write_entity_category(Mock(), "input_boolean.gone", "entity", "c1")

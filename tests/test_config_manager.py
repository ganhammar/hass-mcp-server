"""Tests for config_manager YAML helpers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.config_manager import (
    _load_yaml_dict,
    _load_yaml_list,
    read_dict_entries,
    read_dict_entry,
    read_list_entries,
    read_list_entry,
)


class TestLoadYamlList:
    """Tests for _load_yaml_list."""

    def test_returns_empty_list_when_file_missing(self, tmp_path):
        """Test returns empty list when file does not exist."""
        result = _load_yaml_list(str(tmp_path / "nonexistent.yaml"))
        assert result == []

    def test_returns_empty_list_when_data_is_none(self, tmp_path):
        """Test returns empty list when YAML loads as None."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=None,
            ),
        ):
            result = _load_yaml_list("/fake/path.yaml")
        assert result == []

    def test_returns_list_when_data_is_list(self, tmp_path):
        """Test returns the list when YAML loads as a list."""
        expected = [{"id": "1", "alias": "Test"}]
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=expected,
            ),
        ):
            result = _load_yaml_list("/fake/path.yaml")
        assert result == expected

    def test_returns_empty_list_when_data_is_not_list(self, tmp_path):
        """Test returns empty list when YAML loads as non-list type."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value="a string",
            ),
        ):
            result = _load_yaml_list("/fake/path.yaml")
        assert result == []


class TestLoadYamlDict:
    """Tests for _load_yaml_dict."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        """Test returns empty dict when file does not exist."""
        result = _load_yaml_dict(str(tmp_path / "nonexistent.yaml"))
        assert result == {}

    def test_returns_empty_dict_when_data_is_none(self):
        """Test returns empty dict when YAML loads as None."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=None,
            ),
        ):
            result = _load_yaml_dict("/fake/path.yaml")
        assert result == {}

    def test_returns_dict_when_data_is_dict(self):
        """Test returns the dict when YAML loads as a dict."""
        expected = {"script_1": {"alias": "Test"}}
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=expected,
            ),
        ):
            result = _load_yaml_dict("/fake/path.yaml")
        assert result == expected

    def test_returns_empty_dict_when_data_is_not_dict(self):
        """Test returns empty dict when YAML loads as non-dict type."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=[1, 2, 3],
            ),
        ):
            result = _load_yaml_dict("/fake/path.yaml")
        assert result == {}


# --- Read helper tests ---


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = Mock()
    hass.config.path = Mock(return_value="/config/test.yaml")

    async def run_fn(fn, *args):
        return fn(*args) if args else fn()

    hass.async_add_executor_job = AsyncMock(side_effect=run_fn)
    return hass


class TestReadListEntries:
    """Tests for read_list_entries."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_file_missing(self, mock_hass):
        """Test returns empty list when file does not exist."""
        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            return_value=[],
        ):
            result = await read_list_entries(mock_hass, "automations.yaml")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_entries(self, mock_hass):
        """Test returns full list of entries."""
        expected = [
            {"id": "abc-123", "alias": "Auto One"},
            {"id": "abc-456", "alias": "Auto Two"},
        ]
        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            return_value=expected,
        ):
            result = await read_list_entries(mock_hass, "automations.yaml")
        assert result == expected


class TestReadListEntry:
    """Tests for read_list_entry."""

    @pytest.mark.asyncio
    async def test_returns_matching_entry(self, mock_hass):
        """Test returns entry matching the given ID."""
        entries = [
            {"id": "abc-123", "alias": "Auto One"},
            {"id": "abc-456", "alias": "Auto Two"},
        ]
        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            return_value=entries,
        ):
            result = await read_list_entry(mock_hass, "automations.yaml", "abc-456")
        assert result["alias"] == "Auto Two"

    @pytest.mark.asyncio
    async def test_raises_value_error_when_not_found(self, mock_hass):
        """Test raises ValueError when ID is not found."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
                return_value=[],
            ),
            pytest.raises(ValueError, match="not found"),
        ):
            await read_list_entry(mock_hass, "automations.yaml", "nonexistent")


class TestReadDictEntries:
    """Tests for read_dict_entries."""

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_file_missing(self, mock_hass):
        """Test returns empty dict when file does not exist."""
        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
            return_value={},
        ):
            result = await read_dict_entries(mock_hass, "scripts.yaml")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_all_entries(self, mock_hass):
        """Test returns full dict of entries."""
        expected = {
            "morning": {"alias": "Morning Routine"},
            "evening": {"alias": "Evening Routine"},
        }
        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
            return_value=expected,
        ):
            result = await read_dict_entries(mock_hass, "scripts.yaml")
        assert result == expected


class TestReadDictEntry:
    """Tests for read_dict_entry."""

    @pytest.mark.asyncio
    async def test_returns_matching_entry(self, mock_hass):
        """Test returns entry matching the given key."""
        entries = {
            "morning": {"alias": "Morning Routine"},
            "evening": {"alias": "Evening Routine"},
        }
        with patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
            return_value=entries,
        ):
            result = await read_dict_entry(mock_hass, "scripts.yaml", "evening")
        assert result["alias"] == "Evening Routine"

    @pytest.mark.asyncio
    async def test_raises_value_error_when_not_found(self, mock_hass):
        """Test raises ValueError when key is not found."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager._load_yaml_dict",
                return_value={},
            ),
            pytest.raises(ValueError, match="not found"),
        ):
            await read_dict_entry(mock_hass, "scripts.yaml", "nonexistent")

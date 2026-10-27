"""Tests for config_manager YAML helpers."""

import stat
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

from custom_components.mcp_server_http_transport.config_manager import (
    _copy_aside,
    _load_yaml_dict,
    _load_yaml_list,
    _save_yaml,
    read_dict_entries,
    read_dict_entry,
    read_list_entries,
    read_list_entry,
    update_list_entry,
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


class TestSaveYaml:
    """Tests for _save_yaml."""

    def test_replaces_file_atomically_and_keeps_mode(self, tmp_path):
        path = tmp_path / "automations.yaml"
        path.write_text("- id: a\n", encoding="utf-8")
        path.chmod(0o600)

        _save_yaml(str(path), [{"id": "b"}])

        assert yaml.safe_load(path.read_text(encoding="utf-8")) == [{"id": "b"}]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert [p.name for p in tmp_path.iterdir()] == ["automations.yaml"]

    def test_failed_dump_leaves_file_untouched(self, tmp_path):
        path = tmp_path / "automations.yaml"
        path.write_text("- id: a\n", encoding="utf-8")

        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager."
                "yaml_dumper.save_yaml",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            _save_yaml(str(path), [{"id": "b"}])

        assert path.read_text(encoding="utf-8") == "- id: a\n"
        assert [p.name for p in tmp_path.iterdir()] == ["automations.yaml"]


class TestUpdateListEntry:
    """Tests for update_list_entry's expected, backup and reload options."""

    @pytest.fixture
    def hass(self, tmp_path):
        hass = Mock()
        hass.config.path = Mock(side_effect=lambda name: str(tmp_path / name))
        hass.services.async_call = AsyncMock()

        async def run(fn, *args):
            return fn(*args)

        hass.async_add_executor_job = AsyncMock(side_effect=run)
        (tmp_path / "automations.yaml").write_text("- id: a\n  alias: Old\n", encoding="utf-8")
        return hass

    async def test_writes_and_reloads_by_default(self, hass, tmp_path):
        backup = await update_list_entry(
            hass, "automations.yaml", "a", {"alias": "New"}, "automation"
        )

        assert backup is None
        saved = yaml.safe_load((tmp_path / "automations.yaml").read_text(encoding="utf-8"))
        assert saved == [{"id": "a", "alias": "New"}]
        hass.services.async_call.assert_awaited_once_with("automation", "reload", blocking=True)

    async def test_reload_can_be_skipped(self, hass):
        await update_list_entry(
            hass, "automations.yaml", "a", {"alias": "New"}, "automation", reload=False
        )

        hass.services.async_call.assert_not_awaited()

    async def test_expected_refuses_a_changed_entry(self, hass, tmp_path):
        with pytest.raises(ValueError, match="changed since it was read"):
            await update_list_entry(
                hass,
                "automations.yaml",
                "a",
                {"alias": "New"},
                "automation",
                expected={"id": "a", "alias": "Older"},
            )

        assert (tmp_path / "automations.yaml").read_text(
            encoding="utf-8"
        ) == "- id: a\n  alias: Old\n"
        hass.services.async_call.assert_not_awaited()

    async def test_backup_returns_a_copy_of_the_original(self, hass, tmp_path):
        backup = await update_list_entry(
            hass, "automations.yaml", "a", {"alias": "New"}, "automation", backup=True
        )

        assert backup is not None
        assert Path(backup).parent == tmp_path
        assert Path(backup).read_text(encoding="utf-8") == "- id: a\n  alias: Old\n"
        assert "New" in (tmp_path / "automations.yaml").read_text(encoding="utf-8")

    async def test_missing_entry_raises(self, hass):
        with pytest.raises(ValueError, match="not found"):
            await update_list_entry(hass, "automations.yaml", "zzz", {"alias": "New"}, "automation")

    async def test_failed_write_removes_the_backup(self, hass, tmp_path):
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager._save_yaml",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError),
        ):
            await update_list_entry(
                hass, "automations.yaml", "a", {"alias": "New"}, "automation", backup=True
            )

        assert [p.name for p in tmp_path.iterdir()] == ["automations.yaml"]
        hass.services.async_call.assert_not_awaited()


class TestCopyAside:
    """Tests for _copy_aside."""

    def test_failed_copy_leaves_no_temp_file(self, tmp_path):
        path = tmp_path / "automations.yaml"
        path.write_text("- id: a\n", encoding="utf-8")

        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.shutil.copy2",
                side_effect=OSError("denied"),
            ),
            pytest.raises(OSError),
        ):
            _copy_aside(str(path))

        assert [p.name for p in tmp_path.iterdir()] == ["automations.yaml"]

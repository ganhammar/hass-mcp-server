"""Tests for config file access tools."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools.config_files import (
    backup_config_files,
    delete_config_file,
    get_config_file,
    list_config_files,
    save_config_file,
)


def _make_hass(config_dir: Path, *, config_file_access: bool = True) -> Mock:
    hass = Mock()
    hass.config.config_dir = str(config_dir)
    hass.data = {DOMAIN: {"config_file_access": config_file_access}}
    return hass


class TestDisabledByDefault:
    async def test_list_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await list_config_files(hass, {})
        assert "disabled" in result["content"][0]["text"].lower()

    async def test_get_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await get_config_file(hass, {"filename": "automations.yaml"})
        assert "disabled" in result["content"][0]["text"].lower()

    async def test_save_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await save_config_file(hass, {"filename": "test.yaml", "content": "x: 1"})
        assert "disabled" in result["content"][0]["text"].lower()
        assert not (tmp_path / "test.yaml").exists()

    async def test_delete_disabled(self, tmp_path):
        (tmp_path / "custom.yaml").write_text("x: 1")
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await delete_config_file(hass, {"filename": "custom.yaml"})
        assert "disabled" in result["content"][0]["text"].lower()
        assert (tmp_path / "custom.yaml").exists()

    async def test_backup_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await backup_config_files(hass, {})
        assert "disabled" in result["content"][0]["text"].lower()


class TestListConfigFiles:
    async def test_list_returns_yaml_files(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("[]")
        (tmp_path / "scripts.yml").write_text("[]")
        (tmp_path / "not_yaml.txt").write_text("ignored")
        hass = _make_hass(tmp_path)

        result = await list_config_files(hass, {})
        files = json.loads(result["content"][0]["text"])

        assert "automations.yaml" in files
        assert "scripts.yml" in files
        assert "not_yaml.txt" not in files

    async def test_list_excludes_secrets(self, tmp_path):
        (tmp_path / "secrets.yaml").write_text("token: abc")
        (tmp_path / "configuration.yaml").write_text("")
        hass = _make_hass(tmp_path)

        result = await list_config_files(hass, {})
        files = json.loads(result["content"][0]["text"])

        assert "secrets.yaml" not in files
        assert "configuration.yaml" in files

    async def test_list_excludes_secrets_yml_variant(self, tmp_path):
        (tmp_path / "secrets.yml").write_text("token: abc")
        hass = _make_hass(tmp_path)

        result = await list_config_files(hass, {})
        files = json.loads(result["content"][0]["text"])

        assert "secrets.yml" not in files

    async def test_list_excludes_subdirectories(self, tmp_path):
        subdir = tmp_path / "packages"
        subdir.mkdir()
        (subdir / "lights.yaml").write_text("")
        hass = _make_hass(tmp_path)

        result = await list_config_files(hass, {})
        files = json.loads(result["content"][0]["text"])

        assert "lights.yaml" not in files

    async def test_list_returns_sorted(self, tmp_path):
        (tmp_path / "zzz.yaml").write_text("")
        (tmp_path / "aaa.yaml").write_text("")
        hass = _make_hass(tmp_path)

        result = await list_config_files(hass, {})
        files = json.loads(result["content"][0]["text"])

        assert files == sorted(files)

    async def test_list_empty_dir(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await list_config_files(hass, {})
        files = json.loads(result["content"][0]["text"])
        assert files == []

    async def test_list_handles_os_error(self, tmp_path):
        hass = _make_hass(tmp_path)
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = await list_config_files(hass, {})
        assert "Error listing config files" in result["content"][0]["text"]


class TestGetConfigFile:
    async def test_read_existing_file(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("- alias: test\n")
        hass = _make_hass(tmp_path)

        result = await get_config_file(hass, {"filename": "automations.yaml"})

        assert result["content"][0]["text"] == "- alias: test\n"

    async def test_read_nonexistent_file(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await get_config_file(hass, {"filename": "missing.yaml"})
        assert "does not exist" in result["content"][0]["text"]

    async def test_read_blocks_secrets(self, tmp_path):
        (tmp_path / "secrets.yaml").write_text("token: abc")
        hass = _make_hass(tmp_path)

        result = await get_config_file(hass, {"filename": "secrets.yaml"})

        assert "blocked" in result["content"][0]["text"]

    async def test_read_blocks_non_yaml(self, tmp_path):
        (tmp_path / "script.py").write_text("import os")
        hass = _make_hass(tmp_path)

        result = await get_config_file(hass, {"filename": "script.py"})

        assert (
            "Error" in result["content"][0]["text"] or "Only YAML" in result["content"][0]["text"]
        )

    async def test_read_blocks_subdirectory_path(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await get_config_file(hass, {"filename": "subdir/file.yaml"})
        assert "Subdirectories are not allowed" in result["content"][0]["text"]

    async def test_read_blocks_path_traversal(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await get_config_file(hass, {"filename": "../etc/passwd"})
        assert "Subdirectories are not allowed" in result["content"][0]["text"]

    async def test_read_blocks_symlink_escape(self, tmp_path):
        """Symlink pointing outside config_dir must be blocked by is_relative_to check."""
        outside = tmp_path.parent / "outside.yaml"
        outside.write_text("secret: data")
        link = tmp_path / "escape.yaml"
        link.symlink_to(outside)
        hass = _make_hass(tmp_path)

        result = await get_config_file(hass, {"filename": "escape.yaml"})

        assert "Path traversal detected" in result["content"][0]["text"]

    async def test_read_blocks_oversized_file(self, tmp_path):
        large_file = tmp_path / "big.yaml"
        large_file.write_bytes(b"x: y\n" * 300_000)  # > 1 MB
        hass = _make_hass(tmp_path)

        result = await get_config_file(hass, {"filename": "big.yaml"})

        assert "too large" in result["content"][0]["text"]

    async def test_read_yml_extension(self, tmp_path):
        (tmp_path / "scripts.yml").write_text("my_script:\n  sequence: []\n")
        hass = _make_hass(tmp_path)

        result = await get_config_file(hass, {"filename": "scripts.yml"})

        assert "my_script" in result["content"][0]["text"]


class TestSaveConfigFile:
    async def test_write_new_file(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await save_config_file(hass, {"filename": "new.yaml", "content": "key: value\n"})

        assert "Successfully saved" in result["content"][0]["text"]
        assert (tmp_path / "new.yaml").read_text() == "key: value\n"

    async def test_overwrite_existing_file(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("old content")
        hass = _make_hass(tmp_path)

        await save_config_file(hass, {"filename": "automations.yaml", "content": "new content"})

        assert (tmp_path / "automations.yaml").read_text() == "new content"

    async def test_write_blocks_secrets(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await save_config_file(
            hass, {"filename": "secrets.yaml", "content": "token: hacked"}
        )
        assert "blocked" in result["content"][0]["text"]
        assert not (tmp_path / "secrets.yaml").exists()

    async def test_write_blocks_non_yaml(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await save_config_file(hass, {"filename": "malicious.sh", "content": "rm -rf /"})
        assert (
            "Error" in result["content"][0]["text"] or "Only YAML" in result["content"][0]["text"]
        )
        assert not (tmp_path / "malicious.sh").exists()

    async def test_write_blocks_subdirectory_path(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await save_config_file(hass, {"filename": "subdir/file.yaml", "content": "x: 1"})
        assert "Subdirectories are not allowed" in result["content"][0]["text"]


class TestDeleteConfigFile:
    async def test_delete_existing_file(self, tmp_path):
        (tmp_path / "custom.yaml").write_text("x: 1")
        hass = _make_hass(tmp_path)

        result = await delete_config_file(hass, {"filename": "custom.yaml"})

        assert "Successfully deleted" in result["content"][0]["text"]
        assert not (tmp_path / "custom.yaml").exists()

    async def test_delete_nonexistent_file(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await delete_config_file(hass, {"filename": "ghost.yaml"})
        assert "does not exist" in result["content"][0]["text"]

    async def test_delete_blocks_secrets(self, tmp_path):
        (tmp_path / "secrets.yaml").write_text("token: abc")
        hass = _make_hass(tmp_path)

        result = await delete_config_file(hass, {"filename": "secrets.yaml"})

        assert "blocked" in result["content"][0]["text"]
        assert (tmp_path / "secrets.yaml").exists()

    async def test_delete_blocks_non_yaml(self, tmp_path):
        (tmp_path / "script.py").write_text("")
        hass = _make_hass(tmp_path)

        result = await delete_config_file(hass, {"filename": "script.py"})

        assert (
            "Error" in result["content"][0]["text"] or "Only YAML" in result["content"][0]["text"]
        )
        assert (tmp_path / "script.py").exists()

    async def test_delete_blocks_subdirectory_path(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await delete_config_file(hass, {"filename": "subdir/file.yaml"})
        assert "Subdirectories are not allowed" in result["content"][0]["text"]


class TestBackupConfigFiles:
    async def test_backup_creates_timestamped_folder(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("[]")
        (tmp_path / "scripts.yaml").write_text("{}")
        hass = _make_hass(tmp_path)

        result = await backup_config_files(hass, {})
        text = result["content"][0]["text"]

        assert "mcp_backups/" in text
        backup_dirs = list((tmp_path / "mcp_backups").iterdir())
        assert len(backup_dirs) == 1
        assert (backup_dirs[0] / "automations.yaml").exists()
        assert (backup_dirs[0] / "scripts.yaml").exists()

    async def test_backup_excludes_secrets(self, tmp_path):
        (tmp_path / "configuration.yaml").write_text("homeassistant:")
        (tmp_path / "secrets.yaml").write_text("token: abc")
        hass = _make_hass(tmp_path)

        await backup_config_files(hass, {})
        backup_dir = next((tmp_path / "mcp_backups").iterdir())

        assert (backup_dir / "configuration.yaml").exists()
        assert not (backup_dir / "secrets.yaml").exists()

    async def test_backup_excludes_non_yaml(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("[]")
        (tmp_path / "readme.txt").write_text("ignored")
        hass = _make_hass(tmp_path)

        await backup_config_files(hass, {})
        backup_dir = next((tmp_path / "mcp_backups").iterdir())

        assert not (backup_dir / "readme.txt").exists()

    async def test_backup_excludes_mcp_backups_folder(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("[]")
        (tmp_path / "mcp_backups").mkdir()
        hass = _make_hass(tmp_path)

        await backup_config_files(hass, {})
        backup_dir = next((tmp_path / "mcp_backups").iterdir())

        assert not (backup_dir / "mcp_backups").exists()

    async def test_backup_reports_file_count(self, tmp_path):
        (tmp_path / "a.yaml").write_text("")
        (tmp_path / "b.yaml").write_text("")
        (tmp_path / "c.yml").write_text("")
        hass = _make_hass(tmp_path)

        result = await backup_config_files(hass, {})
        text = result["content"][0]["text"]

        assert "3 files" in text
        assert "a.yaml" in text
        assert "b.yaml" in text
        assert "c.yml" in text

    async def test_backup_empty_dir_no_folder_created(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await backup_config_files(hass, {})
        assert "No YAML files" in result["content"][0]["text"]
        assert not (tmp_path / "mcp_backups").exists()

    async def test_multiple_backups_create_separate_folders(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("[]")
        hass = _make_hass(tmp_path)

        await backup_config_files(hass, {})
        await backup_config_files(hass, {})

        backup_dirs = list((tmp_path / "mcp_backups").iterdir())
        assert len(backup_dirs) == 2

    async def test_backup_handles_os_error(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("[]")
        hass = _make_hass(tmp_path)

        with patch("shutil.copy2", side_effect=OSError("disk full")):
            result = await backup_config_files(hass, {})

        assert "Error creating backup" in result["content"][0]["text"]

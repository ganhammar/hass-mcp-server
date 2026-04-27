"""Tests for config file access tools."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools.config_files import (
    backup_config_files,
    batch_edit_config_files,
    cleanup_config_backups,
    delete_config_file,
    get_config_file,
    list_config_backups,
    list_config_files,
    restore_config_backup,
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


def _mock_check_config(valid: bool = True, errors: list[str] | None = None):
    from unittest.mock import AsyncMock

    result = {"valid": valid, "errors": errors or []}
    return patch(
        "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
        new=AsyncMock(return_value=result),
    )


def _mock_create_backup(path: str = "mcp_backups/2026-01-01_10-00-00-000000"):
    return patch(
        "custom_components.mcp_server_http_transport.tools.config_files._create_backup",
        return_value=path,
    )


class TestSaveConfigFile:
    async def test_write_new_file(self, tmp_path):
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config():
            result = await save_config_file(
                hass, {"filename": "new.yaml", "content": "key: value\n"}
            )

        assert "Successfully saved" in result["content"][0]["text"]
        assert (tmp_path / "new.yaml").read_text() == "key: value\n"

    async def test_backup_created_automatically(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("original")
        hass = _make_hass(tmp_path)
        with _mock_check_config():
            result = await save_config_file(
                hass, {"filename": "automations.yaml", "content": "updated"}
            )
        text = result["content"][0]["text"]
        assert "Backup:" in text
        assert "mcp_backups/" in text
        assert (tmp_path / "automations.yaml").read_text() == "updated"

    async def test_overwrite_existing_file(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("old content")
        hass = _make_hass(tmp_path)

        with _mock_create_backup(), _mock_check_config():
            await save_config_file(hass, {"filename": "automations.yaml", "content": "new content"})

        assert (tmp_path / "automations.yaml").read_text() == "new content"

    async def test_config_check_ok_reported(self, tmp_path):
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config(valid=True):
            result = await save_config_file(hass, {"filename": "test.yaml", "content": "x: 1"})
        assert "Config check: OK" in result["content"][0]["text"]

    async def test_config_check_errors_reported(self, tmp_path):
        hass = _make_hass(tmp_path)
        with (
            _mock_create_backup(),
            _mock_check_config(valid=False, errors=["Invalid entity: light.missing"]),
        ):
            result = await save_config_file(hass, {"filename": "test.yaml", "content": "x: 1"})
        text = result["content"][0]["text"]
        assert "Config check: ERRORS FOUND" in text
        assert "light.missing" in text

    async def test_config_check_skipped_when_run_check_false(self, tmp_path):
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config() as mock_check:
            result = await save_config_file(
                hass, {"filename": "test.yaml", "content": "x: 1", "run_check": False}
            )
        mock_check.assert_not_called()
        assert "Config check" not in result["content"][0]["text"]

    async def test_config_check_failure_doesnt_hide_save_success(self, tmp_path):
        hass = _make_hass(tmp_path)
        with (
            _mock_create_backup(),
            patch(
                "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
                side_effect=Exception("check unavailable"),
            ),
        ):
            result = await save_config_file(hass, {"filename": "test.yaml", "content": "x: 1"})
        text = result["content"][0]["text"]
        assert "Successfully saved" in text
        assert "Config check failed to run" in text

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

        with _mock_create_backup():
            result = await delete_config_file(hass, {"filename": "custom.yaml"})

        assert "Successfully deleted" in result["content"][0]["text"]
        assert not (tmp_path / "custom.yaml").exists()

    async def test_backup_created_automatically_before_delete(self, tmp_path):
        (tmp_path / "custom.yaml").write_text("x: 1")
        hass = _make_hass(tmp_path)

        result = await delete_config_file(hass, {"filename": "custom.yaml"})

        text = result["content"][0]["text"]
        assert "Successfully deleted" in text
        assert "Backup:" in text
        assert "mcp_backups/" in text

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


class TestListConfigBackups:
    async def test_list_no_backup_dir(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await list_config_backups(hass, {})
        assert "No backups found" in result["content"][0]["text"]

    async def test_list_empty_backup_dir(self, tmp_path):
        (tmp_path / "mcp_backups").mkdir()
        hass = _make_hass(tmp_path)
        result = await list_config_backups(hass, {})
        assert "No backups found" in result["content"][0]["text"]

    async def test_list_shows_backups_newest_first(self, tmp_path):
        backup_root = tmp_path / "mcp_backups"
        older = backup_root / "2026-01-01_10-00-00-000000"
        newer = backup_root / "2026-01-02_10-00-00-000000"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        (older / "automations.yaml").write_text("[]")
        (newer / "automations.yaml").write_text("[]")
        hass = _make_hass(tmp_path)

        result = await list_config_backups(hass, {})
        backups = json.loads(result["content"][0]["text"])

        assert backups[0]["timestamp"] == "2026-01-02_10-00-00-000000"
        assert backups[1]["timestamp"] == "2026-01-01_10-00-00-000000"

    async def test_list_shows_files_in_each_backup(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("[]")
        (backup_dir / "scripts.yaml").write_text("{}")
        hass = _make_hass(tmp_path)

        result = await list_config_backups(hass, {})
        backups = json.loads(result["content"][0]["text"])

        assert "automations.yaml" in backups[0]["files"]
        assert "scripts.yaml" in backups[0]["files"]

    async def test_list_ignores_non_timestamp_dirs(self, tmp_path):
        # Synology NAS creates @eaDir metadata folders; they must not appear as backups.
        backup_root = tmp_path / "mcp_backups"
        real = backup_root / "2026-01-01_10-00-00-000000"
        real.mkdir(parents=True)
        (real / "automations.yaml").write_text("[]")
        (backup_root / "@eaDir").mkdir()
        (backup_root / "not-a-timestamp").mkdir()
        hass = _make_hass(tmp_path)

        result = await list_config_backups(hass, {})
        backups = json.loads(result["content"][0]["text"])

        assert len(backups) == 1
        assert backups[0]["timestamp"] == "2026-01-01_10-00-00-000000"

    async def test_list_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await list_config_backups(hass, {})
        assert "disabled" in result["content"][0]["text"].lower()


class TestRestoreConfigBackup:
    async def test_restore_latest_backup(self, tmp_path):
        (tmp_path / "automations.yaml").write_text("original")
        hass = _make_hass(tmp_path)
        await backup_config_files(hass, {})
        (tmp_path / "automations.yaml").write_text("broken")

        with _mock_check_config():
            result = await restore_config_backup(hass, {})

        assert (tmp_path / "automations.yaml").read_text() == "original"
        assert "Restored" in result["content"][0]["text"]

    async def test_restore_specific_timestamp(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("from specific backup")
        (tmp_path / "automations.yaml").write_text("current")
        hass = _make_hass(tmp_path)

        with _mock_check_config():
            result = await restore_config_backup(hass, {"timestamp": "2026-01-01_10-00-00-000000"})

        assert (tmp_path / "automations.yaml").read_text() == "from specific backup"
        assert "2026-01-01_10-00-00-000000" in result["content"][0]["text"]

    async def test_restore_unknown_timestamp(self, tmp_path):
        (tmp_path / "mcp_backups").mkdir()
        hass = _make_hass(tmp_path)
        result = await restore_config_backup(hass, {"timestamp": "nonexistent"})
        assert "not found" in result["content"][0]["text"]

    async def test_restore_no_backups(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await restore_config_backup(hass, {})
        assert "No backups found" in result["content"][0]["text"]

    async def test_restore_does_not_delete_new_files(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("backed up")
        (tmp_path / "automations.yaml").write_text("current")
        (tmp_path / "new_file.yaml").write_text("added after backup")
        hass = _make_hass(tmp_path)

        with _mock_check_config():
            await restore_config_backup(hass, {})

        assert (tmp_path / "new_file.yaml").exists()

    async def test_restore_runs_config_check(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("[]")
        hass = _make_hass(tmp_path)

        with _mock_check_config(valid=True):
            result = await restore_config_backup(hass, {})

        assert "Config check: OK" in result["content"][0]["text"]

    async def test_restore_reports_config_errors(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("bad yaml:")
        hass = _make_hass(tmp_path)

        with _mock_check_config(valid=False, errors=["Syntax error in automations.yaml"]):
            result = await restore_config_backup(hass, {})

        assert "Config check: ERRORS FOUND" in result["content"][0]["text"]

    async def test_restore_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await restore_config_backup(hass, {})
        assert "disabled" in result["content"][0]["text"].lower()

    async def test_restore_backup_dir_exists_but_no_subdirs(self, tmp_path):
        (tmp_path / "mcp_backups").mkdir()
        hass = _make_hass(tmp_path)
        result = await restore_config_backup(hass, {})
        assert "No backups found" in result["content"][0]["text"]

    async def test_restore_backup_with_no_yaml_files(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "readme.txt").write_text("not yaml")
        hass = _make_hass(tmp_path)
        result = await restore_config_backup(hass, {})
        assert "contained no YAML files" in result["content"][0]["text"]

    async def test_restore_check_failure_doesnt_hide_restore_success(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("[]")
        hass = _make_hass(tmp_path)

        with patch(
            "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
            side_effect=Exception("check unavailable"),
        ):
            result = await restore_config_backup(hass, {})

        text = result["content"][0]["text"]
        assert "Restored" in text
        assert "Config check failed to run" in text

    async def test_restore_handles_os_error(self, tmp_path):
        backup_dir = tmp_path / "mcp_backups" / "2026-01-01_10-00-00-000000"
        backup_dir.mkdir(parents=True)
        (backup_dir / "automations.yaml").write_text("[]")
        hass = _make_hass(tmp_path)

        with patch("shutil.copy2", side_effect=OSError("disk full")):
            result = await restore_config_backup(hass, {})

        assert "Error restoring backup" in result["content"][0]["text"]


class TestListConfigBackupsErrors:
    async def test_list_handles_os_error(self, tmp_path):
        (tmp_path / "mcp_backups").mkdir()
        hass = _make_hass(tmp_path)
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = await list_config_backups(hass, {})
        assert "Error listing backups" in result["content"][0]["text"]


class TestRunConfigCheck:
    async def test_run_config_check_valid(self):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.mcp_server_http_transport.tools.config_files import (
            _run_config_check,
        )

        mock_res = MagicMock()
        mock_res.errors = []
        hass = Mock()

        with patch(
            "homeassistant.helpers.check_config.async_check_ha_config_file",
            new=AsyncMock(return_value=mock_res),
        ):
            result = await _run_config_check(hass)

        assert result == {"valid": True, "errors": []}

    async def test_run_config_check_with_errors(self):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.mcp_server_http_transport.tools.config_files import (
            _run_config_check,
        )

        mock_res = MagicMock()
        mock_res.errors = ["Invalid platform: sensor"]
        hass = Mock()

        with patch(
            "homeassistant.helpers.check_config.async_check_ha_config_file",
            new=AsyncMock(return_value=mock_res),
        ):
            result = await _run_config_check(hass)

        assert result["valid"] is False


class TestBatchEditConfigFiles:
    async def test_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await batch_edit_config_files(
            hass, {"saves": [{"filename": "x.yaml", "content": "x: 1"}]}
        )
        assert "disabled" in result["content"][0]["text"].lower()

    async def test_saves_multiple_files(self, tmp_path):
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config():
            result = await batch_edit_config_files(
                hass,
                {
                    "saves": [
                        {"filename": "a.yaml", "content": "a: 1"},
                        {"filename": "b.yaml", "content": "b: 2"},
                    ]
                },
            )
        text = result["content"][0]["text"]
        assert "a.yaml" in text
        assert "b.yaml" in text
        assert (tmp_path / "a.yaml").read_text() == "a: 1"
        assert (tmp_path / "b.yaml").read_text() == "b: 2"

    async def test_deletes_multiple_files(self, tmp_path):
        (tmp_path / "old1.yaml").write_text("x: 1")
        (tmp_path / "old2.yaml").write_text("x: 2")
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config():
            result = await batch_edit_config_files(hass, {"deletes": ["old1.yaml", "old2.yaml"]})
        text = result["content"][0]["text"]
        assert "old1.yaml" in text
        assert "old2.yaml" in text
        assert not (tmp_path / "old1.yaml").exists()
        assert not (tmp_path / "old2.yaml").exists()

    async def test_saves_and_deletes_in_one_call(self, tmp_path):
        (tmp_path / "remove_me.yaml").write_text("old: true")
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config():
            result = await batch_edit_config_files(
                hass,
                {
                    "saves": [{"filename": "new.yaml", "content": "new: true"}],
                    "deletes": ["remove_me.yaml"],
                },
            )
        text = result["content"][0]["text"]
        assert "new.yaml" in text
        assert "remove_me.yaml" in text
        assert (tmp_path / "new.yaml").exists()
        assert not (tmp_path / "remove_me.yaml").exists()

    async def test_one_backup_for_all_operations(self, tmp_path):
        (tmp_path / "del.yaml").write_text("x: 1")
        hass = _make_hass(tmp_path)
        backup_calls = []

        def counting_backup(h):
            backup_calls.append(1)
            return "mcp_backups/fake"

        with (
            patch(
                "custom_components.mcp_server_http_transport.tools.config_files._create_backup",
                side_effect=counting_backup,
            ),
            _mock_check_config(),
        ):
            await batch_edit_config_files(
                hass,
                {
                    "saves": [
                        {"filename": "a.yaml", "content": "a: 1"},
                        {"filename": "b.yaml", "content": "b: 2"},
                    ],
                    "deletes": ["del.yaml"],
                },
            )
        assert len(backup_calls) == 1

    async def test_validation_failure_aborts_before_any_change(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await batch_edit_config_files(
            hass,
            {
                "saves": [
                    {"filename": "secrets.yaml", "content": "bad: true"},
                    {"filename": "ok.yaml", "content": "ok: 1"},
                ]
            },
        )
        text = result["content"][0]["text"]
        assert "error" in text.lower()
        assert not (tmp_path / "ok.yaml").exists()

    async def test_delete_missing_file_aborts(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await batch_edit_config_files(hass, {"deletes": ["does_not_exist.yaml"]})
        text = result["content"][0]["text"]
        assert "error" in text.lower()

    async def test_run_check_false_skips_validation(self, tmp_path):
        hass = _make_hass(tmp_path)
        with _mock_create_backup():
            result = await batch_edit_config_files(
                hass,
                {"saves": [{"filename": "x.yaml", "content": "x: 1"}], "run_check": False},
            )
        text = result["content"][0]["text"]
        assert "config check" not in text.lower()

    async def test_config_check_errors_reported(self, tmp_path):
        hass = _make_hass(tmp_path)
        with _mock_create_backup(), _mock_check_config(valid=False, errors=["bad thing"]):
            result = await batch_edit_config_files(
                hass, {"saves": [{"filename": "x.yaml", "content": "x: 1"}]}
            )
        text = result["content"][0]["text"]
        assert "bad thing" in text

    async def test_empty_saves_and_deletes_returns_error(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await batch_edit_config_files(hass, {})
        text = result["content"][0]["text"]
        assert "error" in text.lower()

    async def test_delete_invalid_filename_returns_error(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await batch_edit_config_files(hass, {"deletes": ["secrets.yaml"]})
        assert "Error in deletes" in result["content"][0]["text"]

    async def test_save_write_failure_reports_error(self, tmp_path):
        hass = _make_hass(tmp_path)
        with (
            _mock_create_backup(),
            _mock_check_config(),
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        ):
            result = await batch_edit_config_files(
                hass, {"saves": [{"filename": "x.yaml", "content": "x: 1"}]}
            )
        text = result["content"][0]["text"]
        assert "Errors:" in text
        assert "disk full" in text

    async def test_delete_unlink_failure_reports_error(self, tmp_path):
        (tmp_path / "x.yaml").write_text("x: 1")
        hass = _make_hass(tmp_path)
        with (
            _mock_create_backup(),
            _mock_check_config(),
            patch("pathlib.Path.unlink", side_effect=OSError("permission denied")),
        ):
            result = await batch_edit_config_files(hass, {"deletes": ["x.yaml"]})
        text = result["content"][0]["text"]
        assert "Errors:" in text
        assert "permission denied" in text

    async def test_config_check_exception_reported(self, tmp_path):
        from unittest.mock import AsyncMock

        hass = _make_hass(tmp_path)
        with (
            _mock_create_backup(),
            patch(
                "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
                new=AsyncMock(side_effect=Exception("check exploded")),
            ),
        ):
            result = await batch_edit_config_files(
                hass, {"saves": [{"filename": "x.yaml", "content": "x: 1"}]}
            )
        assert "check exploded" in result["content"][0]["text"]


class TestCleanupConfigBackups:
    def _make_backup(self, backup_root: Path, timestamp: str, filename: str = "automations.yaml"):
        d = backup_root / timestamp
        d.mkdir(parents=True)
        (d / filename).write_text("[]")
        return d

    async def test_deletes_old_backups(self, tmp_path):
        backup_root = tmp_path / "mcp_backups"
        self._make_backup(backup_root, "2026-01-01_10-00-00-000000")
        self._make_backup(backup_root, "2026-01-02_10-00-00-000000")
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {"older_than_days": 1})
        text = result["content"][0]["text"]
        assert "Deleted 2" in text
        assert "0 backup(s) remaining" in text
        assert not (backup_root / "2026-01-01_10-00-00-000000").exists()

    async def test_keeps_recent_backups(self, tmp_path):

        backup_root = tmp_path / "mcp_backups"
        # old backup
        self._make_backup(backup_root, "2026-01-01_10-00-00-000000")
        # recent backup using today's date
        from datetime import date

        today = date.today().strftime("%Y-%m-%d")
        self._make_backup(backup_root, f"{today}_10-00-00-000000")
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {"older_than_days": 30})
        text = result["content"][0]["text"]
        assert "Deleted 1" in text
        assert "1 backup(s) remaining" in text
        assert (backup_root / f"{today}_10-00-00-000000").exists()

    async def test_no_backups_found(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {})
        assert "No backups found" in result["content"][0]["text"]

    async def test_no_old_backups_to_delete(self, tmp_path):
        from datetime import date

        backup_root = tmp_path / "mcp_backups"
        today = date.today().strftime("%Y-%m-%d")
        self._make_backup(backup_root, f"{today}_10-00-00-000000")
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {"older_than_days": 30})
        text = result["content"][0]["text"]
        assert "Deleted 0" in text
        assert "1 backup(s) remaining" in text

    async def test_ignores_non_timestamp_dirs(self, tmp_path):
        backup_root = tmp_path / "mcp_backups"
        self._make_backup(backup_root, "2026-01-01_10-00-00-000000")
        (backup_root / "@eaDir").mkdir()
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {"older_than_days": 1})
        text = result["content"][0]["text"]
        assert "Deleted 1" in text
        assert (backup_root / "@eaDir").exists()

    async def test_default_older_than_days_is_30(self, tmp_path):
        backup_root = tmp_path / "mcp_backups"
        self._make_backup(backup_root, "2026-01-01_10-00-00-000000")
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {})
        text = result["content"][0]["text"]
        assert "Deleted 1" in text

    async def test_rejects_zero_days(self, tmp_path):
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {"older_than_days": 0})
        assert "error" in result["content"][0]["text"].lower()

    async def test_skips_dir_matching_regex_but_invalid_date(self, tmp_path):
        # Regex matches but strptime fails (e.g. month 13) — must be skipped silently.
        backup_root = tmp_path / "mcp_backups"
        self._make_backup(backup_root, "2026-01-01_10-00-00-000000")
        (backup_root / "2026-13-99_99-99-99-000000").mkdir()
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {"older_than_days": 1})
        text = result["content"][0]["text"]
        assert "Deleted 1" in text

    async def test_no_valid_dirs_in_existing_backup_root(self, tmp_path):
        backup_root = tmp_path / "mcp_backups"
        backup_root.mkdir()
        (backup_root / "@eaDir").mkdir()
        hass = _make_hass(tmp_path)
        result = await cleanup_config_backups(hass, {})
        assert "No backups found" in result["content"][0]["text"]

    async def test_handles_os_error(self, tmp_path):
        (tmp_path / "mcp_backups").mkdir()
        hass = _make_hass(tmp_path)
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = await cleanup_config_backups(hass, {})
        assert "Error cleaning up backups" in result["content"][0]["text"]

    async def test_disabled(self, tmp_path):
        hass = _make_hass(tmp_path, config_file_access=False)
        result = await cleanup_config_backups(hass, {})
        assert "disabled" in result["content"][0]["text"].lower()

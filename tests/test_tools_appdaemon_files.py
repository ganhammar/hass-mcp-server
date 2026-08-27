"""Security and lifecycle tests for bounded AppDaemon file access."""

import json
import tracemalloc
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.mcp_server_http_transport.const import (
    CONF_APPDAEMON_APPS_ROOT,
    DEFAULT_APPDAEMON_APPS_ROOT,
    DOMAIN,
    validate_appdaemon_apps_root,
)
from custom_components.mcp_server_http_transport.tools import appdaemon_files as tools


def _hass() -> Mock:
    hass = Mock()
    hass.data = {DOMAIN: {"appdaemon_file_access": True}}

    async def executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=executor)
    return hass


def _configured_hass(root: str) -> Mock:
    hass = _hass()
    hass.data[DOMAIN][CONF_APPDAEMON_APPS_ROOT] = root
    return hass


def _disabled_hass() -> Mock:
    hass = _hass()
    hass.data[DOMAIN]["appdaemon_file_access"] = False
    return hass


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def apps_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "addon_configs" / "a0d7b954_appdaemon" / "apps"
    root.mkdir(parents=True)
    monkeypatch.setattr(tools, "_APPS_ROOT", root)
    return root


@pytest.mark.asyncio
async def test_read_nested_file_and_sha(apps_root: Path):
    file = apps_root / "predictors" / "solar.py"
    file.parent.mkdir()
    file.write_text("class Solar: pass\n")
    result = _payload(await tools.get_appdaemon_file(_hass(), {"path": "predictors/solar.py"}))
    assert result["path"] == "predictors/solar.py"
    assert result["content"] == "class Solar: pass\n"
    assert result["sha256"] == sha256(b"class Solar: pass\n").hexdigest()


@pytest.mark.asyncio
async def test_save_is_atomic_preserves_mode_and_creates_backup(apps_root: Path):
    file = apps_root / "solar.py"
    file.write_text("old\n")
    file.chmod(0o600)
    result = _payload(
        await tools.save_appdaemon_file(_hass(), {"path": "solar.py", "content": "new\n"})
    )
    assert result["success"]
    assert result["sha256_before"] == sha256(b"old\n").hexdigest()
    assert result["sha256_after"] == sha256(b"new\n").hexdigest()
    assert file.read_text() == "new\n" and (file.stat().st_mode & 0o777) == 0o600
    assert (apps_root / result["backup"] / "solar.py").read_text() == "old\n"


@pytest.mark.asyncio
async def test_delete_creates_backup_and_reports_hash(apps_root: Path):
    file = apps_root / "solar.py"
    file.write_text("delete me\n")
    result = _payload(await tools.delete_appdaemon_file(_hass(), {"path": "solar.py"}))
    assert result["success"]
    assert result["sha256_before"] == sha256(b"delete me\n").hexdigest()
    assert result["sha256_after"] is None
    assert not file.exists()
    assert (apps_root / result["backup"] / "solar.py").read_text() == "delete me\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", [".py", ".yaml", ".yml", ".json"])
async def test_writable_appdaemon_extensions_are_allowed(apps_root: Path, extension: str):
    result = _payload(
        await tools.save_appdaemon_file(_hass(), {"path": f"app{extension}", "content": "x"})
    )
    assert result["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", [".txt", ".sh", ".md", ""])
async def test_non_appdaemon_extensions_are_rejected_for_writes(apps_root: Path, extension: str):
    result = await tools.save_appdaemon_file(_hass(), {"path": f"app{extension}", "content": "x"})
    assert "Only .py, .yaml, .yml, and .json" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_non_appdaemon_extensions_are_rejected_for_deletes(apps_root: Path):
    target = apps_root / "notes.txt"
    target.write_text("keep")
    result = await tools.delete_appdaemon_file(_hass(), {"path": "notes.txt"})
    assert "Only .py, .yaml, .yml, and .json" in result["content"][0]["text"]
    assert target.exists()


@pytest.mark.asyncio
async def test_cleanup_appdaemon_backups_removes_only_old_snapshots(apps_root: Path):
    backup_root = apps_root / tools._BACKUP_DIR_NAME
    backup_root.mkdir()
    old = backup_root / "2020-01-01_00-00-00-000000"
    new = backup_root / "2099-01-01_00-00-00-000000"
    (old / "nested").mkdir(parents=True)
    (old / "nested" / "app.py").write_text("old")
    new.mkdir()
    result = await tools.cleanup_appdaemon_backups(_hass(), {"older_than_days": 1})
    assert "Deleted 1 backup(s)" in result["content"][0]["text"]
    assert not old.exists() and new.exists()


@pytest.mark.asyncio
async def test_snapshot_copies_incrementally_without_tree_byte_list(apps_root: Path, monkeypatch):
    (apps_root / "one.py").write_text("one")
    (apps_root / "two.py").write_text("two")
    monkeypatch.setattr(
        tools._RootFS,
        "files",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot must not materialize the file tree")
        ),
    )
    result = _payload(await tools.backup_appdaemon_files(_hass(), {}))
    assert result["success"] and sorted(result["files"]) == ["one.py", "two.py"]


@pytest.mark.asyncio
async def test_listing_skips_files_over_size_limit(apps_root: Path):
    (apps_root / "large.py").write_bytes(b"x" * (tools._MAX_FILE_BYTES + 1))
    result = _payload(await tools.list_appdaemon_files(_hass(), {}))
    assert result == []


@pytest.mark.asyncio
async def test_restore_skips_unsupported_files_and_reports_them(apps_root: Path):
    py_file = apps_root / "app.py"
    text_file = apps_root / "README.md"
    py_file.write_text("original")
    text_file.write_text("original notes")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    py_file.write_text("changed")
    text_file.write_text("changed notes")
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert result["success"]
    assert result["restored"] == ["app.py"]
    assert result["skipped"] == ["README.md"]
    assert py_file.read_text() == "original"
    assert text_file.read_text() == "changed notes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reserved_path",
    [
        (".mcp_appdaemon_backups", "injected.py"),
        ("foo", ".mcp_appdaemon_backups", "injected.py"),
    ],
)
async def test_restore_rejects_reserved_backup_paths(
    apps_root: Path, reserved_path: tuple[str, ...]
):
    stamp = "2020-01-01_00-00-00-000000"
    target = apps_root / "safe.py"
    target.write_text("safe")
    injected = apps_root / tools._BACKUP_DIR_NAME / stamp / Path(*reserved_path)
    injected.parent.mkdir(parents=True)
    injected.write_text("bad")
    result = await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp})
    assert "reserved backup-directory path" in result["content"][0]["text"]
    assert target.read_text() == "safe"


@pytest.mark.asyncio
async def test_restore_rejects_existing_destination_symlink(apps_root: Path, tmp_path: Path):
    source = apps_root / "app.py"
    source.write_text("snapshot")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    source.unlink()
    outside = tmp_path / "outside.py"
    outside.write_text("outside")
    source.symlink_to(outside)
    result = await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp})
    assert "non-regular file" in result["content"][0]["text"]
    assert source.is_symlink() and outside.read_text() == "outside"


@pytest.mark.asyncio
async def test_large_unrelated_file_does_not_block_small_save_and_restore(apps_root: Path):
    large = apps_root / "history.json"
    target = apps_root / "small.py"
    large_bytes = (b"0123456789abcdef" * 131072) + b"!"
    large.write_bytes(large_bytes)
    target.write_text("old\n")

    tracemalloc.start()
    try:
        result = _payload(
            await tools.save_appdaemon_file(
                _hass(), {"path": "small.py", "content": "new\n"}
            )
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["success"] and target.read_text() == "new\n"
    assert peak < 4 * 131072
    backup_file = apps_root / result["backup"] / "history.json"
    assert backup_file.stat().st_size == len(large_bytes)
    assert backup_file.read_bytes() == large_bytes

    target.write_text("changed\n")
    restored = _payload(
        await tools.restore_appdaemon_backup(
            _hass(), {"timestamp": result["backup"].split("/")[-1]}
        )
    )
    assert restored["success"]
    assert target.read_text() == "old\n" and large.read_bytes() == large_bytes


@pytest.mark.asyncio
async def test_large_unrelated_file_does_not_block_small_delete(apps_root: Path):
    large = apps_root / "history.json"
    target = apps_root / "small.py"
    large_bytes = b"x" * (tools._MAX_FILE_BYTES + 1)
    large.write_bytes(large_bytes)
    target.write_text("delete me\n")

    result = _payload(await tools.delete_appdaemon_file(_hass(), {"path": "small.py"}))

    assert result["success"] and not target.exists()
    assert (apps_root / result["backup"] / "history.json").read_bytes() == large_bytes


@pytest.mark.asyncio
async def test_cleanup_ignores_malformed_and_symlink_entries(apps_root: Path, tmp_path: Path):
    backup_root = apps_root / tools._BACKUP_DIR_NAME
    backup_root.mkdir()
    malformed = backup_root / "2020-01-01_00-00-00-000000"
    malformed.write_text("not a directory")
    outside = tmp_path / "outside"
    outside.mkdir()
    (backup_root / "2020-01-02_00-00-00-000000").symlink_to(outside, target_is_directory=True)
    result = await tools.cleanup_appdaemon_backups(_hass(), {"older_than_days": 1})
    assert result["content"][0]["text"] == "No backups found"
    assert malformed.exists() and (backup_root / "2020-01-02_00-00-00-000000").is_symlink()


@pytest.mark.asyncio
async def test_cleanup_missing_or_empty_backup_root_is_safe(apps_root: Path):
    result = await tools.cleanup_appdaemon_backups(_hass(), {})
    assert result["content"][0]["text"] == "No backups found"


@pytest.mark.asyncio
async def test_cleanup_removes_stale_snapshot_staging_directory(apps_root: Path):
    staging = (
        apps_root / tools._BACKUP_DIR_NAME / ".2020-01-01_00-00-00-000000.mcp_staging_deadbeef"
    )
    staging.mkdir(parents=True)
    (staging / "app.py").write_text("partial")
    result = await tools.cleanup_appdaemon_backups(_hass(), {"older_than_days": 1})
    assert "Deleted 1 backup(s)" in result["content"][0]["text"]
    assert not staging.exists()


@pytest.mark.asyncio
async def test_cleanup_ignores_snapshot_disappearing_during_removal(apps_root: Path, monkeypatch):
    backup_root = apps_root / tools._BACKUP_DIR_NAME
    old = backup_root / "2020-01-01_00-00-00-000000"
    old.mkdir(parents=True)
    original_remove = tools._remove_tree

    def disappearing(fs, parts):
        old.rmdir()
        return original_remove(fs, parts)

    monkeypatch.setattr(tools, "_remove_tree", disappearing)
    result = await tools.cleanup_appdaemon_backups(_hass(), {"older_than_days": 1})
    assert result["content"][0]["text"] == "No backups found"


@pytest.mark.asyncio
async def test_restore_creates_pre_restore_backup(apps_root: Path):
    file = apps_root / "solar.py"
    file.write_text("original\n")
    backup = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    file.write_text("changed\n")
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": backup}))
    assert result["success"] and file.read_text() == "original\n"
    assert (apps_root / result["pre_restore_backup"] / "solar.py").read_text() == "changed\n"


@pytest.mark.asyncio
async def test_restore_removes_new_allowlisted_files(apps_root: Path):
    original = apps_root / "original.py"
    original.write_text("original")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    (apps_root / "new.py").write_text("new executable")
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert result["success"]
    assert result["removed"] == ["new.py"]
    assert not (apps_root / "new.py").exists()


@pytest.mark.asyncio
async def test_restore_recreates_deleted_snapshot_subdirectory(apps_root: Path):
    file = apps_root / "some" / "subdirectory" / "file.py"
    file.parent.mkdir(parents=True)
    file.write_text("original\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    file.unlink()
    file.parent.rmdir()
    file.parent.parent.rmdir()
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert result["success"]
    assert file.read_text() == "original\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "../config/configuration.yaml",
        "/config/configuration.yaml",
        "/addon_configs/other/apps/x.py",
    ],
)
async def test_rejects_traversal_and_absolute_escapes(apps_root: Path, path: str):
    text = (await tools.get_appdaemon_file(_hass(), {"path": path}))["content"][0]["text"]
    assert "Error reading AppDaemon file" in text
    assert not (apps_root.parent.parent / "config").exists()


@pytest.mark.asyncio
async def test_rejects_symlink_escape_and_cannot_touch_config(apps_root: Path, tmp_path: Path):
    outside = tmp_path / "config"
    outside.mkdir()
    victim = outside / "configuration.yaml"
    victim.write_text("safe\n")
    (apps_root / "escape").symlink_to(outside, target_is_directory=True)
    text = (
        await tools.save_appdaemon_file(
            _hass(), {"path": "escape/configuration.yaml", "content": "bad\n"}
        )
    )["content"][0]["text"]
    assert "Error saving AppDaemon file" in text and victim.read_text() == "safe\n"


@pytest.mark.asyncio
async def test_list_backups_and_regular_files_do_not_expose_snapshots(apps_root: Path):
    (apps_root / "solar.py").write_text("x\n")
    await tools.backup_appdaemon_files(_hass(), {})
    files = _payload(await tools.list_appdaemon_files(_hass(), {}))
    backups = _payload(await tools.list_appdaemon_backups(_hass(), {}))
    assert [item["path"] for item in files] == ["solar.py"]
    assert len(backups) == 1 and backups[0]["files"] == ["solar.py"]


@pytest.mark.asyncio
async def test_disabled_gate_performs_no_filesystem_access(apps_root: Path):
    result = await tools.get_appdaemon_file(_disabled_hass(), {"path": "solar.py"})
    assert "disabled" in result["content"][0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["", ".", "a/./b", "a//b", "a\\b", "a/../b", "a\x00b"])
async def test_rejects_unusual_relative_forms(apps_root: Path, path: str):
    text = (await tools.get_appdaemon_file(_hass(), {"path": path}))["content"][0]["text"]
    assert "Error reading AppDaemon file" in text


@pytest.mark.parametrize(
    "root",
    [
        DEFAULT_APPDAEMON_APPS_ROOT,
        "/share/appdaemon/apps",
        "/share/project/appdaemon/apps",
        "/media/appdaemon/apps",
    ],
)
def test_allows_legacy_and_shared_roots(root: str):
    assert validate_appdaemon_apps_root(root) == root


@pytest.mark.parametrize(
    "root",
    [
        "/config/appdaemon/apps",
        "/share/../config/apps",
        "/share/appdaemon/./apps",
        "/share//appdaemon/apps",
        "share/appdaemon/apps",
        "/tmp/apps",
    ],
)
def test_rejects_unapproved_or_escaping_roots(root: str):
    with pytest.raises(ValueError):
        validate_appdaemon_apps_root(root)


def test_configured_shared_root_is_used():
    assert tools._root(_configured_hass("/share/appdaemon/apps")) == Path("/share/appdaemon/apps")


@pytest.mark.asyncio
async def test_rejects_symlinked_root_and_backup_directory(
    apps_root: Path, tmp_path: Path, monkeypatch
):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(tools, "_APPS_ROOT", linked_root)
    assert "Error listing" in (await tools.list_appdaemon_files(_hass(), {}))["content"][0]["text"]
    monkeypatch.setattr(tools, "_APPS_ROOT", apps_root)
    (apps_root / tools._BACKUP_DIR_NAME).symlink_to(outside, target_is_directory=True)
    assert (
        "Error backing" in (await tools.backup_appdaemon_files(_hass(), {}))["content"][0]["text"]
    )


@pytest.mark.asyncio
async def test_deterministic_symlink_swap_is_not_followed(
    apps_root: Path, tmp_path: Path, monkeypatch
):
    target = apps_root / "victim.py"
    target.write_text("inside\n")
    outside = tmp_path / "config.py"
    outside.write_text("outside\n")
    real_open = tools.os.open
    swapped = False

    def swap_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "victim.py" and flags & tools.os.O_NOFOLLOW and not swapped:
            swapped = True
            target.unlink()
            target.symlink_to(outside)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(tools.os, "open", swap_open)
    result = await tools.get_appdaemon_file(_hass(), {"path": "victim.py"})
    assert "Error reading" in result["content"][0]["text"]
    assert outside.read_text() == "outside\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "delete", "backup"])
async def test_symlink_swap_during_mutating_operations_does_not_touch_victim(
    apps_root: Path, tmp_path: Path, monkeypatch, operation: str
):
    target = apps_root / "victim.py"
    target.write_text("inside\n")
    outside = tmp_path / "configuration.yaml"
    outside.write_text("safe\n")
    real_open, swapped = tools.os.open, False

    def swap_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "victim.py" and flags & tools.os.O_NOFOLLOW and not swapped:
            swapped = True
            target.unlink()
            target.symlink_to(outside)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(tools.os, "open", swap_open)
    if operation == "save":
        result = await tools.save_appdaemon_file(_hass(), {"path": "victim.py", "content": "bad\n"})
    elif operation == "delete":
        result = await tools.delete_appdaemon_file(_hass(), {"path": "victim.py"})
    else:
        result = await tools.backup_appdaemon_files(_hass(), {})
    assert "Error" in result["content"][0]["text"]
    assert outside.read_text() == "safe\n"


@pytest.mark.asyncio
async def test_restore_symlink_swap_of_source_fails_before_mutation(
    apps_root: Path, tmp_path: Path, monkeypatch
):
    target = apps_root / "victim.py"
    target.write_text("original\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    target.write_text("current\n")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n")
    source = apps_root / tools._BACKUP_DIR_NAME / stamp / "victim.py"
    real_open, swapped = tools.os.open, False

    def swap_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "victim.py" and flags & tools.os.O_NOFOLLOW and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(outside)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(tools.os, "open", swap_open)
    result = await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp})
    payload = _payload(result)
    assert not payload["success"]
    assert payload["possibly_committed_paths"] == []
    assert target.read_text() == "current\n" and outside.read_text() == "outside\n"


@pytest.mark.asyncio
async def test_backup_failure_prevents_save_mutation(apps_root: Path, monkeypatch):
    target = apps_root / "solar.py"
    target.write_text("old\n")
    monkeypatch.setattr(
        tools._RootFS, "snapshot", lambda _self: (_ for _ in ()).throw(OSError("full"))
    )
    result = await tools.save_appdaemon_file(_hass(), {"path": "solar.py", "content": "new\n"})
    assert "Error saving" in result["content"][0]["text"]
    assert target.read_text() == "old\n"


@pytest.mark.asyncio
async def test_restore_failure_rolls_back_modified_targets(apps_root: Path, monkeypatch):
    one, two = apps_root / "one.py", apps_root / "two.py"
    one.write_text("original one\n")
    two.write_text("original two\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    one.write_text("changed one\n")
    two.write_text("changed two\n")
    original_copy = tools._RootFS.copy
    failed = False

    def fail_second(self, source, target):
        nonlocal failed
        if target == ("two.py",) and not failed:
            failed = True
            raise OSError("injected mutation failure")
        return original_copy(self, source, target)

    monkeypatch.setattr(tools._RootFS, "copy", fail_second)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"] and result["rollback_attempted"]
    assert result["rollback_result"] == "succeeded"
    assert result["attempted_paths"] == ["one.py", "two.py"]
    assert result["possibly_committed_paths"] == ["one.py"]
    assert result["rolled_back_paths"] == ["one.py"]
    assert result["rollback_failed_paths"] == []
    assert one.read_text() == "changed one\n" and two.read_text() == "changed two\n"


@pytest.mark.asyncio
async def test_restore_failure_removes_file_that_was_previously_absent(
    apps_root: Path, monkeypatch
):
    one, two = apps_root / "one.py", apps_root / "two.py"
    one.write_text("snapshot one\n")
    two.write_text("snapshot two\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    one.unlink()
    two.write_text("current two\n")
    original_copy = tools._RootFS.copy
    failed = False

    def fail_second(self, source, target):
        nonlocal failed
        if target == ("two.py",) and not failed:
            failed = True
            raise OSError("injected mutation failure")
        return original_copy(self, source, target)

    monkeypatch.setattr(tools._RootFS, "copy", fail_second)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"] and result["rollback_result"] == "succeeded"
    assert not one.exists() and two.read_text() == "current two\n"


@pytest.mark.asyncio
async def test_restore_failure_removes_directories_created_for_partial_tree(
    apps_root: Path, monkeypatch
):
    (apps_root / "nested" / "one.py").parent.mkdir(parents=True)
    (apps_root / "nested" / "one.py").write_text("snapshot\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    (apps_root / "nested" / "one.py").unlink()
    (apps_root / "nested").rmdir()

    def fail_restore(self, source, target):
        raise OSError("injected mutation failure")

    monkeypatch.setattr(tools._RootFS, "copy", fail_restore)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"]
    assert not (apps_root / "nested").exists()


@pytest.mark.asyncio
async def test_restore_post_replace_failure_rolls_back_possibly_committed_target(
    apps_root: Path, monkeypatch
):
    one, two = apps_root / "one.py", apps_root / "two.py"
    one.write_text("snapshot one\n")
    two.write_text("snapshot two\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    one.write_text("current one\n")
    two.write_text("current two\n")
    original_replace, replacement_count, failed = tools.os.replace, 0, False

    def replace_then_fail(source, destination, *args, **kwargs):
        nonlocal failed, replacement_count
        original_replace(source, destination, *args, **kwargs)
        if destination == "two.py":
            replacement_count += 1
        # The first matching replacement belongs to the pre-restore snapshot;
        # the second is the actual target mutation.
        if destination == "two.py" and replacement_count == 2 and not failed:
            failed = True
            raise OSError("injected post-replace failure")

    monkeypatch.setattr(tools.os, "replace", replace_then_fail)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"]
    assert result["attempted_paths"] == ["one.py", "two.py"]
    assert result["possibly_committed_paths"] == ["one.py", "two.py"]
    assert result["rolled_back_paths"] == ["two.py", "one.py"]
    assert result["rollback_failed_paths"] == []
    assert one.read_text() == "current one\n" and two.read_text() == "current two\n"


@pytest.mark.asyncio
async def test_list_component_swap_to_symlink_does_not_read_external_file(
    apps_root: Path, tmp_path: Path, monkeypatch
):
    nested = apps_root / "nested"
    nested.mkdir()
    (nested / "inside.py").write_text("inside\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.py"
    victim.write_text("external\n")
    original_open, swapped = tools.os.open, False

    def swap_directory(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "nested" and flags & tools.os.O_DIRECTORY and not swapped:
            swapped = True
            (nested / "inside.py").unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(tools.os, "open", swap_directory)
    result = _payload(await tools.list_appdaemon_files(_hass(), {}))
    assert result == [] and victim.read_text() == "external\n"


@pytest.mark.asyncio
async def test_restore_destination_component_swap_fails_without_external_write(
    apps_root: Path, tmp_path: Path, monkeypatch
):
    nested = apps_root / "nested"
    nested.mkdir()
    target = nested / "app.py"
    target.write_text("snapshot\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    target.write_text("current\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "app.py"
    victim.write_text("external\n")
    original_copy, swapped = tools._RootFS.copy, False

    def swap_before_destination_write(self, source, target_path):
        nonlocal swapped
        if target_path == ("nested", "app.py") and not swapped:
            swapped = True
            target.unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_copy(self, source, target_path)

    monkeypatch.setattr(tools._RootFS, "copy", swap_before_destination_write)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"]
    assert result["possibly_committed_paths"] == []
    assert result["rollback_result"] == "succeeded"
    assert victim.read_text() == "external\n"


@pytest.mark.asyncio
async def test_restore_reports_rollback_failure(apps_root: Path, monkeypatch):
    one, two = apps_root / "one.py", apps_root / "two.py"
    one.write_text("old one\n")
    two.write_text("old two\n")
    stamp = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    one.write_text("new one\n")
    two.write_text("new two\n")
    original_copy, calls = tools._RootFS.copy, []

    def fail_mutation_and_rollback(self, source, target):
        if target == ("two.py",):
            raise OSError("mutation failure")
        if target == ("one.py",) and calls:
            raise OSError("rollback failure")
        if target == ("one.py",):
            calls.append(target)
        return original_copy(self, source, target)

    monkeypatch.setattr(tools._RootFS, "copy", fail_mutation_and_rollback)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"] and result["rollback_result"] == "failed"
    assert result["rollback_errors"]

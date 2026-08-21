"""Security and lifecycle tests for bounded AppDaemon file access."""

import json
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
async def test_restore_creates_pre_restore_backup(apps_root: Path):
    file = apps_root / "solar.py"
    file.write_text("original\n")
    backup = _payload(await tools.backup_appdaemon_files(_hass(), {}))["backup"].split("/")[-1]
    file.write_text("changed\n")
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": backup}))
    assert result["success"] and file.read_text() == "original\n"
    assert (apps_root / result["pre_restore_backup"] / "solar.py").read_text() == "changed\n"


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
    assert "Error restoring" in result["content"][0]["text"]
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
    original_write = tools._RootFS.write
    failed = False

    def fail_second(self, parts, content, mode):
        nonlocal failed
        if parts == ("two.py",) and not failed:
            failed = True
            raise OSError("injected mutation failure")
        return original_write(self, parts, content, mode)

    monkeypatch.setattr(tools._RootFS, "write", fail_second)
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
    original_write = tools._RootFS.write
    failed = False

    def fail_second(self, parts, content, mode):
        nonlocal failed
        if parts == ("two.py",) and not failed:
            failed = True
            raise OSError("injected mutation failure")
        return original_write(self, parts, content, mode)

    monkeypatch.setattr(tools._RootFS, "write", fail_second)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"] and result["rollback_result"] == "succeeded"
    assert not one.exists() and two.read_text() == "current two\n"


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
    original_write, swapped = tools._RootFS.write, False

    def swap_before_destination_write(self, parts, content, mode):
        nonlocal swapped
        if parts == ("nested", "app.py") and not swapped:
            swapped = True
            target.unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_write(self, parts, content, mode)

    monkeypatch.setattr(tools._RootFS, "write", swap_before_destination_write)
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
    original_write, calls = tools._RootFS.write, []

    def fail_mutation_and_rollback(self, parts, content, mode):
        if parts == ("two.py",):
            raise OSError("mutation failure")
        if parts == ("one.py",) and calls:
            raise OSError("rollback failure")
        if parts == ("one.py",):
            calls.append(parts)
        return original_write(self, parts, content, mode)

    monkeypatch.setattr(tools._RootFS, "write", fail_mutation_and_rollback)
    result = _payload(await tools.restore_appdaemon_backup(_hass(), {"timestamp": stamp}))
    assert not result["success"] and result["rollback_result"] == "failed"
    assert result["rollback_errors"]

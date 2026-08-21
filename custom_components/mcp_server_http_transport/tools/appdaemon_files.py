"""Narrow, race-resistant opt-in access to AppDaemon application files only."""

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import (
    CONF_APPDAEMON_APPS_ROOT,
    DEFAULT_APPDAEMON_APPS_ROOT,
    DOMAIN,
    validate_appdaemon_apps_root,
)
from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    register_tool,
)

_APPS_ROOT = Path(DEFAULT_APPDAEMON_APPS_ROOT)
_BACKUP_DIR_NAME = ".mcp_appdaemon_backups"
_BACKUP_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d+$")
_MAX_FILE_BYTES = 1_048_576
_DEFAULT_MODE = 0o640
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _disabled() -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    "AppDaemon file access is disabled. "
                    "Enable it in the MCP Server integration settings."
                ),
            }
        ]
    }


def _enabled(hass: HomeAssistant) -> bool:
    return hass.data.get(DOMAIN, {}).get("appdaemon_file_access", False)


def _root(hass: HomeAssistant) -> Path:
    """Return the configured, bounded production root."""
    configured = hass.data.get(DOMAIN, {}).get(CONF_APPDAEMON_APPS_ROOT)
    if configured is None:
        return _APPS_ROOT
    return Path(validate_appdaemon_apps_root(configured))


def _error(prefix: str, exc: Exception) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error {prefix}: {exc}"}]}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parts(value: str, *, allow_backup: bool = False) -> tuple[str, ...]:
    """Validate a lexical relative name; containment is enforced by dir_fds below."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("Path must be a non-empty slash-separated relative path")
    if value.startswith("/"):
        raise ValueError("Absolute paths are not allowed")
    parts = tuple(value.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Path contains an invalid component")
    if not allow_backup and _BACKUP_DIR_NAME in parts:
        raise ValueError("Backup snapshots are managed; use list/restore AppDaemon backups")
    return parts


class _WriteFailure(OSError):
    """A write failure that says whether replacement may already have happened."""

    def __init__(self, cause: Exception, *, possibly_committed: bool) -> None:
        super().__init__(str(cause))
        self.possibly_committed = possibly_committed


class _RootFS:
    """Filesystem operations rooted at an O_NOFOLLOW directory descriptor.

    Names are always used with ``dir_fd``.  No operation follows a user controlled
    directory entry, so a swap after lexical validation cannot escape the root.
    """

    def __init__(self, root: Path) -> None:
        self.fd = self._open_root(root)

    @staticmethod
    def _open_root(root: Path) -> int:
        """Open every root component without following symlinks."""
        fd = os.open("/", _DIR_FLAGS)
        try:
            for part in root.parts[1:]:
                next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except Exception:
            os.close(fd)
            raise

    def close(self) -> None:
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def dir(self, parts: tuple[str, ...], *, create: bool = False) -> int:
        fd = os.dup(self.fd)
        try:
            for part in parts:
                try:
                    next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, 0o750, dir_fd=fd)
                    next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except Exception:
            os.close(fd)
            raise

    def _parent(self, parts: tuple[str, ...]) -> tuple[int, str]:
        return self.dir(parts[:-1]), parts[-1]

    def read(self, parts: tuple[str, ...], *, limit: int | None = None) -> tuple[bytes, int]:
        parent, name = self._parent(parts)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("File does not exist or is not regular")
                data = b""
                while True:
                    block = os.read(fd, 131072)
                    if not block:
                        break
                    data += block
                    if limit is not None and len(data) > limit:
                        raise ValueError("File is too large (maximum 1 MB)")
                return data, stat.S_IMODE(info.st_mode)
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    def exists_regular(self, parts: tuple[str, ...]) -> bool:
        parent, name = self._parent(parts)
        try:
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Target is not a regular file")
            return True
        finally:
            os.close(parent)

    def write(self, parts: tuple[str, ...], content: bytes, mode: int) -> None:
        parent, name = self._parent(parts)
        temp = f".{name}.mcp_tmp_{uuid.uuid4().hex}"
        committed = False
        replace_started = False
        try:
            fd = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent
            )
            try:
                view = memoryview(content)
                while view:
                    view = view[os.write(fd, view) :]
                os.fchmod(fd, mode)
                os.fsync(fd)
            finally:
                os.close(fd)
            # rename replaces the directory entry, never the target of a symlink.
            replace_started = True
            os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
            committed = True
        except Exception as exc:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass
            raise _WriteFailure(exc, possibly_committed=committed or replace_started) from exc
        finally:
            try:
                os.close(parent)
            except Exception as exc:
                # Closing after replacement cannot undo the replacement.
                raise _WriteFailure(exc, possibly_committed=committed) from exc

    def unlink(self, parts: tuple[str, ...]) -> None:
        # Read through O_NOFOLLOW first, then unlink only the in-root entry.
        self.read(parts)
        parent, name = self._parent(parts)
        try:
            os.unlink(name, dir_fd=parent)
        finally:
            os.close(parent)

    def files(
        self,
        start: tuple[str, ...] = (),
        *,
        include_backups: bool = False,
        strict: bool = False,
    ) -> list[tuple[tuple[str, ...], bytes, int]]:
        result: list[tuple[tuple[str, ...], bytes, int]] = []

        def walk(fd: int, rel: tuple[str, ...]) -> None:
            for name in sorted(os.listdir(fd)):
                if not include_backups and not rel and name == _BACKUP_DIR_NAME:
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                child = rel + (name,)
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    except OSError:
                        continue
                    try:
                        walk(child_fd, child)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(info.st_mode):
                    try:
                        data, mode = self.read(start + child)
                    except (FileNotFoundError, OSError, ValueError):
                        if strict:
                            raise
                        continue
                    result.append((child, data, mode))

        fd = self.dir(start)
        try:
            walk(fd, ())
        finally:
            os.close(fd)
        return result

    def snapshot(self) -> tuple[str, list[str]]:
        base = self.dir((_BACKUP_DIR_NAME,), create=True)
        try:
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            os.mkdir(stamp, 0o750, dir_fd=base)
        finally:
            os.close(base)
        saved: list[str] = []
        for rel, data, mode in self.files(strict=True):
            target = (_BACKUP_DIR_NAME, stamp) + rel
            parent = target[:-1]
            directory = self.dir(parent, create=True)
            os.close(directory)
            self.write(target, data, mode)
            saved.append("/".join(rel))
        return stamp, saved


def _backup_path(stamp: str) -> str:
    return f"{_BACKUP_DIR_NAME}/{stamp}"


def _restore(fs: _RootFS, timestamp: str) -> dict[str, Any]:
    source_prefix = (_BACKUP_DIR_NAME, timestamp)
    # Opening this directory via no-follow is source validation at use time.
    source_fd = fs.dir(source_prefix)
    os.close(source_fd)
    sources = fs.files(source_prefix, include_backups=True, strict=True)
    if not sources:
        # An empty snapshot is legitimate, but the timestamp must be a controlled directory.
        pass
    planned = [(rel, data, mode) for rel, data, mode in sources]
    previous: dict[tuple[str, ...], tuple[bytes, int] | None] = {}
    for rel, _data, _mode in planned:
        try:
            previous[rel] = fs.read(rel)
        except FileNotFoundError:
            previous[rel] = None
        # Ensure every existing intermediate directory is opened no-follow before mutation.
        parent = fs.dir(rel[:-1])
        os.close(parent)
    pre, _ = fs.snapshot()
    attempted: list[tuple[str, ...]] = []
    possibly_committed: list[tuple[str, ...]] = []
    try:
        for rel, data, mode in planned:
            attempted.append(rel)
            try:
                fs.write(rel, data, mode)
            except _WriteFailure as exc:
                if exc.possibly_committed:
                    possibly_committed.append(rel)
                raise
            else:
                # A normal return means os.replace completed.
                possibly_committed.append(rel)
    except Exception as mutation_error:
        rollback_errors: list[str] = []
        rolled_back: list[str] = []
        for rel in reversed(possibly_committed):
            try:
                old = previous[rel]
                if old is None:
                    try:
                        fs.unlink(rel)
                    except FileNotFoundError:
                        pass
                else:
                    fs.write(rel, old[0], old[1])
                rolled_back.append("/".join(rel))
            except Exception as rollback_error:  # explicit, never concealed
                rollback_errors.append(f"{'/'.join(rel)}: {rollback_error}")
        return {
            "success": False,
            "backup": _backup_path(timestamp),
            "pre_restore_backup": _backup_path(pre),
            "mutation_result": f"failed: {mutation_error}",
            "rollback_attempted": True,
            "rollback_result": "failed" if rollback_errors else "succeeded",
            "attempted_paths": ["/".join(item) for item in attempted],
            "possibly_committed_paths": ["/".join(item) for item in possibly_committed],
            "rolled_back_paths": rolled_back,
            "rollback_failed_paths": [item.split(":", 1)[0] for item in rollback_errors],
            "rollback_errors": rollback_errors,
        }
    return {
        "success": True,
        "backup": _backup_path(timestamp),
        "pre_restore_backup": _backup_path(pre),
        "mutation_result": "succeeded",
        "rollback_attempted": False,
        "rollback_result": "not_required",
        "attempted_paths": ["/".join(item) for item in attempted],
        "possibly_committed_paths": ["/".join(item) for item in possibly_committed],
        "rolled_back_paths": [],
        "rollback_failed_paths": [],
        "restored": ["/".join(item) for item in attempted],
    }


@register_tool(
    "list_appdaemon_files",
    "List regular files under the configured, bounded AppDaemon apps root.",
    {"type": "object", "properties": {}},
    ANNOTATION_READ_ONLY,
)
async def list_appdaemon_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:

        def work():
            with _RootFS(_root(hass)) as fs:
                return [
                    {"path": "/".join(rel), "size": len(data), "sha256": _sha(data)}
                    for rel, data, _mode in fs.files()
                ]

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(await hass.async_add_executor_job(work), indent=2),
                }
            ]
        }
    except Exception as exc:
        return _error("listing AppDaemon files", exc)


@register_tool(
    "get_appdaemon_file",
    "Read a file relative to the configured, bounded AppDaemon apps root.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    ANNOTATION_READ_ONLY,
)
async def get_appdaemon_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:
        parts = _parts(arguments["path"])

        def work():
            with _RootFS(_root(hass)) as fs:
                data, _mode = fs.read(parts, limit=_MAX_FILE_BYTES)
                return {
                    "path": "/".join(parts),
                    "sha256": _sha(data),
                    "content": data.decode("utf-8"),
                }

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("reading AppDaemon file", exc)


@register_tool(
    "save_appdaemon_file",
    "Atomically write an AppDaemon app file after a rollback snapshot.",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
    ANNOTATION_IDEMPOTENT,
)
async def save_appdaemon_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:
        parts, content = _parts(arguments["path"]), arguments["content"].encode("utf-8")
        if len(content) > _MAX_FILE_BYTES:
            raise ValueError("Content is too large (maximum 1 MB)")

        def work():
            with _RootFS(_root(hass)) as fs:
                try:
                    before, mode = fs.read(parts)
                except FileNotFoundError:
                    before, mode = None, _DEFAULT_MODE
                stamp, _ = fs.snapshot()
                fs.write(parts, content, mode)
                return {
                    "success": True,
                    "path": "/".join(parts),
                    "backup": _backup_path(stamp),
                    "sha256_before": _sha(before) if before is not None else None,
                    "sha256_after": _sha(content),
                }

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("saving AppDaemon file", exc)


@register_tool(
    "delete_appdaemon_file",
    "Delete an AppDaemon app file after a rollback snapshot.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    ANNOTATION_DESTRUCTIVE,
)
async def delete_appdaemon_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:
        parts = _parts(arguments["path"])

        def work():
            with _RootFS(_root(hass)) as fs:
                before, _mode = fs.read(parts)
                stamp, _ = fs.snapshot()
                fs.unlink(parts)
                return {
                    "success": True,
                    "path": "/".join(parts),
                    "backup": _backup_path(stamp),
                    "sha256_before": _sha(before),
                    "sha256_after": None,
                }

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("deleting AppDaemon file", exc)


@register_tool(
    "backup_appdaemon_files",
    "Create a timestamped snapshot of AppDaemon app files.",
    {"type": "object", "properties": {}},
    ANNOTATION_NON_IDEMPOTENT,
)
async def backup_appdaemon_files(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:

        def work():
            with _RootFS(_root(hass)) as fs:
                stamp, files = fs.snapshot()
                return {"success": True, "backup": _backup_path(stamp), "files": files}

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("backing up AppDaemon files", exc)


@register_tool(
    "list_appdaemon_backups",
    "List controlled AppDaemon rollback snapshots.",
    {"type": "object", "properties": {}},
    ANNOTATION_READ_ONLY,
)
async def list_appdaemon_backups(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:

        def work():
            with _RootFS(_root(hass)) as fs:
                try:
                    base = fs.dir((_BACKUP_DIR_NAME,))
                except FileNotFoundError:
                    return []
                try:
                    names = sorted(os.listdir(base), reverse=True)
                finally:
                    os.close(base)
                answer = []
                for stamp in names:
                    if not _BACKUP_TS_RE.fullmatch(stamp):
                        continue
                    try:
                        fd = fs.dir((_BACKUP_DIR_NAME, stamp))
                        os.close(fd)
                        files = [
                            "/".join(rel)
                            for rel, _data, _mode in fs.files(
                                (_BACKUP_DIR_NAME, stamp), include_backups=True
                            )
                        ]
                        answer.append(
                            {"timestamp": stamp, "path": _backup_path(stamp), "files": files}
                        )
                    except (FileNotFoundError, NotADirectoryError, OSError):
                        continue
                return answer

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("listing AppDaemon backups", exc)


@register_tool(
    "restore_appdaemon_backup",
    "Restore a snapshot after first snapshotting current app files.",
    {"type": "object", "properties": {"timestamp": {"type": "string"}}, "required": ["timestamp"]},
    ANNOTATION_NON_IDEMPOTENT,
)
async def restore_appdaemon_backup(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:
        timestamp = arguments["timestamp"]
        if not isinstance(timestamp, str) or not _BACKUP_TS_RE.fullmatch(timestamp):
            raise ValueError("Invalid backup timestamp")

        def work():
            with _RootFS(_root(hass)) as fs:
                return _restore(fs, timestamp)

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("restoring AppDaemon backup", exc)

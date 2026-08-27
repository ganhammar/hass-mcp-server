"""Narrow, race-resistant opt-in access to AppDaemon application files only."""

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from datetime import datetime, timedelta
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
    ANNOTATION_NON_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    register_tool,
)

_APPS_ROOT = Path(DEFAULT_APPDAEMON_APPS_ROOT)
_BACKUP_DIR_NAME = ".mcp_appdaemon_backups"
_BACKUP_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d+$")
_STAGING_RE = re.compile(r"^\.(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d+)\.mcp_staging_[0-9a-f]+$")
_MAX_FILE_BYTES = 1_048_576
_DEFAULT_MODE = 0o640
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_WRITABLE_EXTENSIONS = frozenset({".py", ".yaml", ".yml", ".json"})
_MAX_SNAPSHOT_FILES = 10000
_APPDAEMON_LOCK = threading.RLock()


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


def _writable_parts(value: str) -> tuple[str, ...]:
    parts = _parts(value)
    if Path(parts[-1]).suffix.lower() not in _WRITABLE_EXTENSIONS:
        raise ValueError("Only .py, .yaml, .yml, and .json files may be written or deleted")
    return parts


class _WriteFailure(OSError):
    """A write failure that says whether replacement may already have happened."""

    def __init__(self, cause: Exception, *, possibly_committed: bool) -> None:
        super().__init__(str(cause))
        self.possibly_committed = possibly_committed


class _UnlinkFailure(OSError):
    """An unlink failure that says whether removal may already have happened."""

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
                data = bytearray()
                while True:
                    block = os.read(fd, 131072)
                    if not block:
                        break
                    data.extend(block)
                    if limit is not None and len(data) > limit:
                        raise ValueError("File is too large (maximum 1 MB)")
                return bytes(data), stat.S_IMODE(info.st_mode)
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    def hash_regular(self, parts: tuple[str, ...], *, limit: int | None = None) -> tuple[str, int]:
        """Hash one regular file incrementally and return its hash and mode."""
        parent, name = self._parent(parts)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("File does not exist or is not regular")
                digest = hashlib.sha256()
                total = 0
                while block := os.read(fd, 131072):
                    total += len(block)
                    if limit is not None and total > limit:
                        raise ValueError("File is too large (maximum 1 MB)")
                    digest.update(block)
                return digest.hexdigest(), stat.S_IMODE(info.st_mode)
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

    def regular_signature(self, parts: tuple[str, ...]) -> tuple[int, ...] | None:
        """Return identity/metadata for a regular entry without following symlinks."""
        try:
            parent, name = self._parent(parts)
        except FileNotFoundError:
            return None
        try:
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Target is not a regular file")
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        finally:
            os.close(parent)

    def write(
        self, parts: tuple[str, ...], content: bytes, mode: int, *, require_regular: bool = False
    ) -> None:
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
            if require_regular:
                try:
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Target is not a regular file")
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

    def copy(
        self,
        source: tuple[str, ...],
        target: tuple[str, ...],
        *,
        expected_source: tuple[int, ...] | None = None,
        expected_target: tuple[int, ...] | None = None,
    ) -> None:
        """Copy one regular file through rooted descriptors without retaining its contents.

        Backup copies deliberately have no MCP payload-size limit. The copy is still
        bounded by one fixed-size block and is committed only after source identity,
        size, and timestamps remain stable.
        """
        source_parent = target_parent = -1
        source_fd = target_fd = -1
        committed = False
        replace_started = False
        try:
            source_parent, source_name = self._parent(source)
            target_parent, target_name = self._parent(target)
            temp = f".{target_name}.mcp_tmp_{uuid.uuid4().hex}"
            source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
            info = os.fstat(source_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("File does not exist or is not regular")
            source_signature = (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            if expected_source is not None and source_signature != expected_source:
                raise ValueError(f"Source changed before it was copied: {'/'.join(source)}")
            target_fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                stat.S_IMODE(info.st_mode),
                dir_fd=target_parent,
            )
            copied = 0
            while block := os.read(source_fd, 131072):
                copied += len(block)
                view = memoryview(block)
                while view:
                    view = view[os.write(target_fd, view) :]
            final_info = os.fstat(source_fd)
            final_signature = (
                final_info.st_dev,
                final_info.st_ino,
                final_info.st_size,
                final_info.st_mtime_ns,
                final_info.st_ctime_ns,
            )
            if final_signature != source_signature:
                raise ValueError("Source file changed while it was being copied")
            if expected_target is not None:
                if self.regular_signature(target) != expected_target:
                    raise ValueError(
                        f"Target changed while it was being copied: {'/'.join(target)}"
                    )
            elif self.regular_signature(target) is not None:
                raise ValueError(f"Target appeared while it was being copied: {'/'.join(target)}")
            os.fchmod(target_fd, stat.S_IMODE(info.st_mode))
            os.fsync(target_fd)
            os.close(target_fd)
            target_fd = -1
            replace_started = True
            os.replace(temp, target_name, src_dir_fd=target_parent, dst_dir_fd=target_parent)
            committed = True
        except Exception as exc:
            if target_parent >= 0:
                try:
                    os.unlink(temp, dir_fd=target_parent)
                except FileNotFoundError:
                    pass
            raise _WriteFailure(exc, possibly_committed=committed or replace_started) from exc
        finally:
            for fd in (source_fd, target_fd, source_parent, target_parent):
                if fd >= 0:
                    os.close(fd)

    def unlink(self, parts: tuple[str, ...], *, expected: tuple[int, ...] | None = None) -> None:
        parent, name = self._parent(parts)
        unlinked = False
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("File does not exist or is not regular")
            actual = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if expected is not None and actual != expected:
                raise ValueError(f"Target changed before removal: {'/'.join(parts)}")
            os.unlink(name, dir_fd=parent)
            unlinked = True
        except Exception as exc:
            raise _UnlinkFailure(exc, possibly_committed=unlinked) from exc
        finally:
            try:
                os.close(parent)
            except Exception as exc:
                raise _UnlinkFailure(exc, possibly_committed=unlinked) from exc

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
                    child_fd = -1
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

    def file_paths(
        self,
        start: tuple[str, ...] = (),
        *,
        include_backups: bool = False,
        strict: bool = False,
        max_files: int | None = None,
    ) -> list[tuple[str, ...]]:
        """Return regular-file paths without reading their contents."""
        result: list[tuple[str, ...]] = []

        def walk(fd: int, rel: tuple[str, ...]) -> None:
            for name in sorted(os.listdir(fd)):
                if not include_backups and not rel and name == _BACKUP_DIR_NAME:
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    if strict:
                        raise
                    continue
                child = rel + (name,)
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    except OSError:
                        if strict:
                            raise
                        continue
                    try:
                        walk(child_fd, child)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(info.st_mode):
                    result.append(child)
                    if max_files is not None and len(result) > max_files:
                        raise ValueError(f"Tree contains more than {max_files} files")
                elif strict:
                    raise ValueError("Encountered a non-regular file while traversing")

        fd = self.dir(start)
        try:
            walk(fd, ())
        finally:
            os.close(fd)
        return result

    def metadata(self) -> list[tuple[tuple[str, ...], int, str]]:
        result: list[tuple[tuple[str, ...], int, str]] = []

        def walk(fd: int, rel: tuple[str, ...]) -> None:
            for name in sorted(os.listdir(fd)):
                if not rel and name == _BACKUP_DIR_NAME:
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
                    child_fd = -1
                    try:
                        if info.st_size > _MAX_FILE_BYTES:
                            continue
                        child_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                        initial = os.fstat(child_fd)
                        digest = hashlib.sha256()
                        total = 0
                        while block := os.read(child_fd, 131072):
                            total += len(block)
                            if total > _MAX_FILE_BYTES:
                                raise ValueError("File is too large (maximum 1 MB)")
                            digest.update(block)
                        final = os.fstat(child_fd)
                        if (
                            initial.st_dev,
                            initial.st_ino,
                            initial.st_size,
                            initial.st_mtime_ns,
                            initial.st_ctime_ns,
                        ) != (
                            final.st_dev,
                            final.st_ino,
                            final.st_size,
                            final.st_mtime_ns,
                            final.st_ctime_ns,
                        ):
                            continue
                        size = final.st_size
                    except (FileNotFoundError, OSError, ValueError):
                        continue
                    finally:
                        if child_fd >= 0:
                            os.close(child_fd)
                    result.append((child, size, digest.hexdigest()))

        fd = self.dir(())
        try:
            walk(fd, ())
        finally:
            os.close(fd)
        return result

    def snapshot(self) -> tuple[str, list[str]]:
        base = self.dir((_BACKUP_DIR_NAME,), create=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        staging = f".{stamp}.mcp_staging_{uuid.uuid4().hex}"
        try:
            os.mkdir(staging, 0o750, dir_fd=base)
            saved: list[str] = []
            initial_paths = self.file_paths(strict=True, max_files=_MAX_SNAPSHOT_FILES)
            initial_signatures = {rel: self.regular_signature(rel) for rel in initial_paths}
            for rel in initial_paths:
                target = (_BACKUP_DIR_NAME, staging) + rel
                parent = target[:-1]
                directory = self.dir(parent, create=True)
                os.close(directory)
                self.copy(rel, target, expected_source=initial_signatures[rel])
                saved.append("/".join(rel))
            if self.file_paths(strict=True, max_files=_MAX_SNAPSHOT_FILES) != initial_paths:
                raise ValueError("AppDaemon file tree changed while it was being snapshotted")
            os.replace(staging, stamp, src_dir_fd=base, dst_dir_fd=base)
            return stamp, saved
        except Exception:
            try:
                _remove_tree(self, (_BACKUP_DIR_NAME, staging))
            except OSError:
                pass
            raise
        finally:
            os.close(base)


def _backup_path(stamp: str) -> str:
    return f"{_BACKUP_DIR_NAME}/{stamp}"


def _remove_tree(fs: _RootFS, parts: tuple[str, ...]) -> None:
    """Remove a snapshot using only descriptors rooted below the AppDaemon root."""
    directory = fs.dir(parts)
    try:
        for name in os.listdir(directory):
            child = parts + (name,)
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(info.st_mode):
                try:
                    _remove_tree(fs, child)
                except FileNotFoundError:
                    continue
            else:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    continue
    finally:
        os.close(directory)
    parent = fs.dir(parts[:-1])
    try:
        os.rmdir(parts[-1], dir_fd=parent)
    finally:
        os.close(parent)


def _cleanup_snapshots(fs: _RootFS, older_than_days: int) -> dict[str, list[str]] | None:
    try:
        base = fs.dir((_BACKUP_DIR_NAME,))
    except FileNotFoundError:
        return None
    cutoff = datetime.now() - timedelta(days=older_than_days)
    deleted: list[str] = []
    kept: list[str] = []
    try:
        names = os.listdir(base)
    finally:
        os.close(base)
    for stamp in names:
        staging_match = _STAGING_RE.fullmatch(stamp)
        if staging_match:
            try:
                timestamp = datetime.strptime(staging_match.group(1), "%Y-%m-%d_%H-%M-%S-%f")
                staging_fd = fs.dir((_BACKUP_DIR_NAME, stamp))
            except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
                continue
            os.close(staging_fd)
            if timestamp < cutoff:
                try:
                    _remove_tree(fs, (_BACKUP_DIR_NAME, stamp))
                except (FileNotFoundError, NotADirectoryError):
                    continue
                deleted.append(stamp)
            continue
        if not _BACKUP_TS_RE.fullmatch(stamp):
            continue
        try:
            timestamp = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S-%f")
        except ValueError:
            continue
        if timestamp < cutoff:
            try:
                snapshot_fd = fs.dir((_BACKUP_DIR_NAME, stamp))
            except (FileNotFoundError, NotADirectoryError, OSError):
                continue
            else:
                os.close(snapshot_fd)
                try:
                    _remove_tree(fs, (_BACKUP_DIR_NAME, stamp))
                except (FileNotFoundError, NotADirectoryError):
                    continue
                deleted.append(stamp)
        else:
            kept.append(stamp)
    if not deleted and not kept:
        return None
    return {"deleted": deleted, "kept": kept}


def _restore(fs: _RootFS, timestamp: str) -> dict[str, Any]:
    source_prefix = (_BACKUP_DIR_NAME, timestamp)
    # Opening this directory via no-follow is source validation at use time.
    source_fd = fs.dir(source_prefix)
    os.close(source_fd)
    source_paths = fs.file_paths(
        source_prefix,
        include_backups=True,
        strict=True,
        max_files=_MAX_SNAPSHOT_FILES,
    )
    if not source_paths:
        # An empty snapshot is legitimate, but the timestamp must be a controlled directory.
        pass
    if any(_BACKUP_DIR_NAME in rel for rel in source_paths):
        raise ValueError("Backup contains a reserved backup-directory path")
    planned = [rel for rel in source_paths if Path(rel[-1]).suffix.lower() in _WRITABLE_EXTENSIONS]
    skipped = [rel for rel in source_paths if rel not in planned]
    current_paths = fs.file_paths(strict=True, max_files=_MAX_SNAPSHOT_FILES)
    remove_paths = [
        rel
        for rel in current_paths
        if rel not in source_paths and Path(rel[-1]).suffix.lower() in _WRITABLE_EXTENSIONS
    ]
    remove_signatures = {rel: fs.regular_signature(rel) for rel in remove_paths}
    target_signatures = {rel: fs.regular_signature(rel) for rel in planned}
    created_dirs: set[tuple[str, ...]] = set()
    for rel in planned:
        for index in range(1, len(rel)):
            directory = rel[:index]
            try:
                directory_fd = fs.dir(directory)
            except FileNotFoundError:
                created_dirs.add(directory)
            else:
                os.close(directory_fd)
    for rel in planned:
        try:
            fs.exists_regular(rel)
        except FileNotFoundError:
            pass
    pre, _ = fs.snapshot()
    attempted: list[tuple[str, ...]] = []
    possibly_committed: list[tuple[str, ...]] = []
    removed_paths: list[tuple[str, ...]] = []
    try:
        for rel in planned:
            attempted.append(rel)
            try:
                if fs.regular_signature(rel) != target_signatures[rel]:
                    raise ValueError(f"Restore target changed: {'/'.join(rel)}")
                parent = fs.dir(rel[:-1], create=True)
                os.close(parent)
                fs.copy(
                    source_prefix + rel,
                    rel,
                    expected_target=target_signatures[rel],
                )
            except _WriteFailure as exc:
                if exc.possibly_committed:
                    possibly_committed.append(rel)
                    target_signatures[rel] = fs.regular_signature(rel)
                raise
            else:
                # A normal return means os.replace completed.
                possibly_committed.append(rel)
                target_signatures[rel] = fs.regular_signature(rel)
        for rel in remove_paths:
            attempted.append(rel)
            if fs.regular_signature(rel) != remove_signatures[rel]:
                raise ValueError(f"Restore removal target changed: {'/'.join(rel)}")
            try:
                fs.unlink(rel)
            except _UnlinkFailure as exc:
                if exc.possibly_committed:
                    possibly_committed.append(rel)
                raise
            possibly_committed.append(rel)
            removed_paths.append(rel)
    except Exception as mutation_error:
        rollback_errors: list[str] = []
        rolled_back: list[str] = []
        for rel in reversed(possibly_committed):
            try:
                expected = None if rel in remove_signatures else target_signatures[rel]
                if fs.regular_signature(rel) != expected:
                    raise ValueError(f"Rollback target changed: {'/'.join(rel)}")
                old = fs.regular_signature((_BACKUP_DIR_NAME, pre) + rel)
                if old is None:
                    try:
                        fs.unlink(rel, expected=expected)
                    except FileNotFoundError:
                        pass
                else:
                    fs.copy(
                        (_BACKUP_DIR_NAME, pre) + rel,
                        rel,
                        expected_target=expected,
                    )
                rolled_back.append("/".join(rel))
            except Exception as rollback_error:  # explicit, never concealed
                rollback_errors.append(f"{'/'.join(rel)}: {rollback_error}")
        for directory in sorted(created_dirs, key=len, reverse=True):
            try:
                parent = fs.dir(directory[:-1])
                try:
                    os.rmdir(directory[-1], dir_fd=parent)
                finally:
                    os.close(parent)
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as rollback_error:
                rollback_errors.append(f"{'/'.join(directory)}: {rollback_error}")
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
            "skipped": ["/".join(item) for item in skipped],
            "removed": ["/".join(item) for item in removed_paths],
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
        "restored": ["/".join(item) for item in planned],
        "skipped": ["/".join(item) for item in skipped],
        "removed": ["/".join(item) for item in removed_paths],
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
                    {"path": "/".join(rel), "size": size, "sha256": digest}
                    for rel, size, digest in fs.metadata()
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
    ANNOTATION_DESTRUCTIVE,
)
async def save_appdaemon_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    try:
        parts, content = _writable_parts(arguments["path"]), arguments["content"].encode("utf-8")
        if len(content) > _MAX_FILE_BYTES:
            raise ValueError("Content is too large (maximum 1 MB)")

        def work():
            with _APPDAEMON_LOCK, _RootFS(_root(hass)) as fs:
                try:
                    sha_before, mode = fs.hash_regular(parts)
                except FileNotFoundError:
                    sha_before, mode = None, _DEFAULT_MODE
                stamp, _ = fs.snapshot()
                try:
                    fs.write(parts, content, mode)
                except _WriteFailure as exc:
                    return {
                        "success": False,
                        "path": "/".join(parts),
                        "backup": _backup_path(stamp),
                        "mutation_result": f"failed: {exc}",
                        "possibly_committed": exc.possibly_committed,
                    }
                return {
                    "success": True,
                    "path": "/".join(parts),
                    "backup": _backup_path(stamp),
                    "sha256_before": sha_before,
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
        parts = _writable_parts(arguments["path"])

        def work():
            with _APPDAEMON_LOCK, _RootFS(_root(hass)) as fs:
                sha_before, _mode = fs.hash_regular(parts)
                stamp, _ = fs.snapshot()
                fs.unlink(parts)
                return {
                    "success": True,
                    "path": "/".join(parts),
                    "backup": _backup_path(stamp),
                    "sha256_before": sha_before,
                    "sha256_after": None,
                }

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        if isinstance(exc, _UnlinkFailure) and exc.possibly_committed:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "mutation_result": "delete may have committed before an error",
                                "possibly_committed": True,
                                "error": str(exc),
                            }
                        ),
                    }
                ]
            }
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
            with _APPDAEMON_LOCK, _RootFS(_root(hass)) as fs:
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
                            for rel in fs.file_paths(
                                (_BACKUP_DIR_NAME, stamp), include_backups=True, strict=True
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
    "cleanup_appdaemon_backups",
    "Delete AppDaemon rollback snapshots older than a given number of days (default 30).",
    {
        "type": "object",
        "properties": {
            "older_than_days": {
                "type": "integer",
                "description": "Delete backups older than this many days (default: 30, minimum: 1)",
            }
        },
    },
    ANNOTATION_DESTRUCTIVE,
)
async def cleanup_appdaemon_backups(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    if not _enabled(hass):
        return _disabled()
    older_than_days = arguments.get("older_than_days", 30)
    if not isinstance(older_than_days, int) or older_than_days < 1:
        return _error(
            "cleaning up AppDaemon backups", ValueError("older_than_days must be an integer >= 1")
        )
    try:

        def work():
            with _APPDAEMON_LOCK, _RootFS(_root(hass)) as fs:
                return _cleanup_snapshots(fs, older_than_days)

        result = await hass.async_add_executor_job(work)
        if result is None:
            return {"content": [{"type": "text", "text": "No backups found"}]}
        deleted, kept = result["deleted"], result["kept"]
        lines = [f"Deleted {len(deleted)} backup(s) older than {older_than_days} day(s)."]
        lines.extend(f"  - {name}" for name in sorted(deleted))
        lines.append(f"{len(kept)} backup(s) remaining.")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}
    except Exception as exc:
        return _error("cleaning up AppDaemon backups", exc)


@register_tool(
    "restore_appdaemon_backup",
    (
        "Restore allowlisted files from a snapshot after first snapshotting current app files; "
        "report unsupported files as skipped."
    ),
    {"type": "object", "properties": {"timestamp": {"type": "string"}}, "required": ["timestamp"]},
    ANNOTATION_DESTRUCTIVE,
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
            with _APPDAEMON_LOCK, _RootFS(_root(hass)) as fs:
                return _restore(fs, timestamp)

        return {
            "content": [
                {"type": "text", "text": json.dumps(await hass.async_add_executor_job(work))}
            ]
        }
    except Exception as exc:
        return _error("restoring AppDaemon backup", exc)

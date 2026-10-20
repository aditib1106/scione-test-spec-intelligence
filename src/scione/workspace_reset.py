"""Recoverable removal of local demo state, never source files or credentials."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_TARGETS = {"lab.json": "file", "specifications": "directory", "reports": "directory"}
_PROTECTED_PARTS = {".git", ".venv", ".aws", ".codex", ".agents", "src", "docs", "tests"}


class DemoResetError(RuntimeError):
    """A reset was rejected or could not finish; original state remains recoverable."""


@dataclass(frozen=True)
class DemoResetResult:
    backup_path: Path | None
    moved_names: tuple[str, ...]
    file_count: int


def _safe_path(path: str | Path) -> Path:
    checked = Path(path).absolute()
    if ".." in checked.parts:
        raise DemoResetError("Reset paths must not contain parent traversal.")
    for parent in [*reversed(checked.parents), checked]:
        if parent.is_symlink():
            raise DemoResetError("Demo reset paths must not traverse symlinks.")
        if parent.exists() and not parent.is_dir():
            raise DemoResetError("Demo reset paths must be directories.")
    return checked


def _validate_roots(workspace_dir: str | Path, backup_root: str | Path | None) -> tuple[Path, Path]:
    root = _safe_path(workspace_dir)
    home = Path.home().resolve()
    current = Path.cwd().resolve()
    if (
        root == Path(root.anchor)
        or root == home
        or root == current
        or root in current.parents
        or any(part in _PROTECTED_PARTS for part in root.parts)
        or (root / ".git").exists()
        or (root / "pyproject.toml").exists()
    ):
        raise DemoResetError("Refusing to reset a home, repository, source, or system directory.")
    backup = _safe_path(backup_root if backup_root is not None else root.parent / ".demo-backups")
    if backup.name != ".demo-backups":
        raise DemoResetError("The recovery directory must be named .demo-backups.")
    if root == backup or root in backup.parents or backup in root.parents:
        raise DemoResetError("Demo state and recovery directories must not contain each other.")
    return root, backup


def _count_regular_files(path: Path) -> int:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode):
        raise DemoResetError("Refusing to move symlinks from demo state.")
    if stat.S_ISREG(details.st_mode):
        return 1
    if not stat.S_ISDIR(details.st_mode):
        raise DemoResetError("Demo state must contain only regular files and directories.")
    count = 0
    for child in path.iterdir():
        if child.name in {".write.lock", ".lab.lock", ".demo-reset.lock"}:
            raise DemoResetError(
                "A demo writer may be active. Finish its operation before resetting."
            )
        count += _count_regular_files(child)
    return count


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reset_demo_workspace(
    workspace_dir: str | Path,
    backup_root: str | Path | None = None,
) -> DemoResetResult:
    """Move only lab.json, specifications/, and reports/ into a private backup.

    The caller must finish active demo operations before resetting and clear its
    own in-memory UI state afterward. Existing store locks are honored. Every
    selected artifact is preserved byte-for-byte, with a recovery manifest; if
    moving or recording the reset fails, completed moves are rolled back.
    Unknown files, .env, catalog assets, PDFs outside the workspace, and the
    Python environment are never selected. No model or network calls occur.
    """

    root, backup = _validate_roots(workspace_dir, backup_root)
    if not root.exists():
        return DemoResetResult(None, (), 0)
    for lock in [root / ".lab.lock", root / ".demo-reset.lock"]:
        if lock.exists() or lock.is_symlink():
            raise DemoResetError(
                "A demo writer may be active. Finish its operation before resetting."
            )
    selected: list[tuple[Path, int]] = []
    for name, kind in _TARGETS.items():
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            raise DemoResetError(f"Refusing to move a symlink named {name}.")
        if path.is_file() if kind == "directory" else path.is_dir():
            raise DemoResetError(f"Unexpected demo artifact type: {name}.")
        selected.append((path, _count_regular_files(path)))
    if not selected:
        return DemoResetResult(None, (), 0)

    locks: list[tuple[Path, Path | None]] = []
    moved: list[Path] = []
    destination: Path | None = None
    manifest: dict[str, object] = {}
    try:
        lock_targets = [(root / ".demo-reset.lock", None), (root / ".lab.lock", None)]
        lock_targets.extend(
            (path / ".write.lock", path) for path, _ in selected if path.name != "lab.json"
        )
        for lock, owner in lock_targets:
            descriptor = os.open(
                lock,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
            locks.append((lock, owner))
        backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        _safe_path(backup)
        destination = backup / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
        destination.mkdir(mode=0o700)
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": str(root),
            "status": "in_progress",
            "artifacts": [{"name": path.name, "file_count": count} for path, count in selected],
            "recovery": (
                "Stop the application. Move the named artifacts from this folder back to "
                "the workspace only when their destination names do not exist. "
                "Keep any newer demo state separately; never overwrite it."
            ),
        }
        _write_manifest(destination / "manifest.json", manifest)
        for source, _ in selected:
            if source.is_symlink():
                raise DemoResetError(
                    "Demo artifacts changed during reset; retry after writers finish."
                )
            source.rename(destination / source.name)
            moved.append(source)
        manifest["status"] = "complete"
        _write_manifest(destination / "manifest.json", manifest)
        return DemoResetResult(
            destination,
            tuple(path.name for path, _ in selected),
            sum(count for _, count in selected),
        )
    except (OSError, DemoResetError) as exc:
        rollback_errors: list[str] = []
        if destination is not None:
            for original in reversed(moved):
                try:
                    if original.exists() or original.is_symlink():
                        raise OSError("The original location was recreated by another writer.")
                    (destination / original.name).rename(original)
                except OSError:
                    rollback_errors.append(original.name)
            manifest["status"] = "recovery_required" if rollback_errors else "rolled_back"
            try:
                _write_manifest(destination / "manifest.json", manifest)
            except OSError:
                pass
        recovery = f" Recovery copies are at {destination}." if rollback_errors else ""
        raise DemoResetError(
            f"Demo reset did not finish; no files were deleted.{recovery}"
        ) from exc
    finally:
        cleanup_failed = False
        for lock, owner in reversed(locks):
            active_lock = lock
            if owner is not None and destination is not None:
                archived_lock = destination / owner.name / lock.name
                if archived_lock.exists():
                    active_lock = archived_lock
            try:
                active_lock.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise DemoResetError(
                "Demo artifacts remain preserved, but a reset lock could not be released. "
                f"Check recovery files at {destination} before retrying."
            )

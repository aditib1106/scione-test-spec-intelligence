"""Atomic local JSON storage for one demo lab, without a database or credentials."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scione.catalogs.models import LabProfile, ReferenceCatalog, validate_lab_references
from scione.catalogs.seed import default_demo_lab, load_reference_catalog


class LabStoreError(RuntimeError):
    """Local lab data could not be read or written safely; do not reset it silently."""


class LabVersionConflict(LabStoreError):
    """The lab changed after this editor loaded it; reload before saving again."""


class FileLabStore:
    """Persist ``root/lab.json``; imports and construction never seed local data.

    ``load_or_create`` explicitly seeds a missing profile at version 1. ``save``
    requires an existing profile with the same ID and current version and writes
    version + 1. It does not mutate the caller's object: reload after saving.
    Corruption is reported and preserved, never replaced with default data.

    Atomic replacement plus an exclusive short-lived lock prevents overlapping
    prototype writes. If a process crashes leaving ``.lab.lock``, an operator
    must verify no writer remains and remove that lock before retrying.
    """

    def __init__(self, root: str | Path, catalog: ReferenceCatalog | None = None) -> None:
        self.root = Path(root).absolute()
        self.catalog = catalog if catalog is not None else load_reference_catalog()

    @property
    def path(self) -> Path:
        return self.root / "lab.json"

    def load_or_create(self) -> LabProfile:
        """Load a validated profile, creating the synthetic seed only if absent."""

        try:
            with self._locked():
                if self.path.exists() or self.path.is_symlink():
                    return self._read()
                lab = self._validate(default_demo_lab())
                self._write(lab)
                return lab
        except (OSError, ValueError) as exc:
            raise LabStoreError(f"Unable to load local lab: {exc}") from exc

    def save(self, lab: LabProfile) -> None:
        """Validate references and reject stale updates before atomic replacement."""

        try:
            with self._locked():
                if not self.path.exists() and not self.path.is_symlink():
                    raise LabStoreError("No saved lab exists; call load_or_create first.")
                existing = self._read()
                candidate = self._validate(lab)
                if candidate.id != existing.id:
                    raise LabStoreError("The saved lab ID cannot be changed.")
                if candidate.version != existing.version:
                    raise LabVersionConflict(
                        "The lab has changed. Reload before saving "
                        f"(editor version {candidate.version}, saved version {existing.version})."
                    )
                next_lab = LabProfile.model_validate(
                    {**candidate.model_dump(), "version": existing.version + 1}
                )
                self._write(next_lab)
        except (OSError, ValueError) as exc:
            raise LabStoreError(f"Unable to save local lab: {exc}") from exc

    def _validate(self, lab: LabProfile) -> LabProfile:
        # Revalidation also covers objects changed through model_copy(update=...).
        checked = LabProfile.model_validate(lab.model_dump())
        validate_lab_references(checked, self.catalog)
        return checked

    def _check_root(self) -> None:
        if ".." in self.root.parts:
            raise LabStoreError("Local lab root must not contain parent traversal.")
        for path in [*reversed(self.root.parents), self.root]:
            if path.is_symlink():
                raise LabStoreError("Local lab storage must not traverse symlink directories.")
            if path.exists() and not path.is_dir():
                raise LabStoreError("Local lab storage root must be a directory.")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._check_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._check_root()
        lock_path = self.root / ".lab.lock"
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError as exc:
            raise LabStoreError(
                "Another local lab update is active, or a previous writer left .lab.lock. "
                "Retry after the writer finishes; do not reset the lab data."
            ) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            lock_path.unlink()

    def _read(self) -> LabProfile:
        if self.path.is_symlink():
            raise LabStoreError("Refusing to load lab.json through a symlink.")
        descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise LabStoreError("Local lab data must be a regular JSON file.")
            return self._validate(LabProfile.model_validate_json(handle.read()))

    def _write(self, lab: LabProfile) -> None:
        self._check_root()
        if self.path.is_symlink():
            raise LabStoreError("Refusing to replace a symlink named lab.json.")
        descriptor, name = tempfile.mkstemp(prefix=".lab-", suffix=".tmp", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(lab.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._check_root()
            if self.path.is_symlink():
                raise LabStoreError("Refusing to replace a symlink named lab.json.")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

"""Local mutable matching choices and immutable, self-contained PDF/JSON reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path
from uuid import UUID

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from scione.catalogs.models import validate_lab_references
from scione.reports.models import ReportSnapshot, ReportWorkspace

MAX_REPORT_PDF_BYTES = 25 * 1024 * 1024
MAX_REPORT_PAGES = 500
MAX_REPORT_JSON_BYTES = 64 * 1024 * 1024


class ReportStoreError(RuntimeError):
    """A report artifact cannot be read/saved; existing data is never reset silently."""


class ReportVersionConflict(ReportStoreError):
    """Matching choices changed since this editor loaded them."""


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    report_id: str
    json_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileReportStore:
    """Construction and reads do not create directories or seed records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute() / "reports"

    def load_workspace(self, document_id: str, run_id: str) -> ReportWorkspace:
        path = self._workspace_path(document_id, run_id)
        if not path.exists() and not path.is_symlink():
            return ReportWorkspace(document_id=document_id, run_id=run_id)
        try:
            workspace = ReportWorkspace.model_validate_json(self._read_bytes(path))
            self._validate_workspace(workspace)
            if workspace.document_id != document_id or workspace.run_id != run_id:
                raise ReportStoreError("Saved matching choices have inconsistent source IDs.")
            return workspace
        except (OSError, ValueError) as exc:
            raise ReportStoreError(f"Unable to load matching choices: {exc}") from exc

    def save_workspace(
        self, workspace: ReportWorkspace, *, expected_version: int
    ) -> ReportWorkspace:
        """Compare-and-swap choices, returning a copy with its new saved version."""

        try:
            checked = ReportWorkspace.model_validate(workspace.model_dump())
            self._validate_workspace(checked)
            if type(expected_version) is not int or expected_version < 0:
                raise ReportStoreError("Expected workspace version must be a nonnegative integer.")
            if checked.version != expected_version:
                raise ReportVersionConflict("Matching choices changed. Reload before saving.")
            with self._locked():
                current = self.load_workspace(checked.document_id, checked.run_id)
                if current.version != expected_version:
                    raise ReportVersionConflict("Matching choices changed. Reload before saving.")
                checked.version = current.version + 1
                path = self._workspace_path(checked.document_id, checked.run_id)
                encoded = self._json_bytes(checked)
                if len(encoded) > MAX_REPORT_JSON_BYTES:
                    raise ReportStoreError("Matching choices exceed the 64 MB limit.")
                path.parent.mkdir(parents=True, exist_ok=True)
                self._write_atomic(path, encoded)
            return checked
        except (OSError, ValueError) as exc:
            raise ReportStoreError(f"Unable to save matching choices: {exc}") from exc

    def save_report(self, snapshot: ReportSnapshot, pdf: bytes) -> None:
        """Commit the PDF, source snapshot, and hashes together, never overwrite an ID."""

        try:
            checked = ReportSnapshot.model_validate(snapshot.model_dump())
            self._validate_snapshot(checked)
            self._validate_pdf(pdf)
            encoded = self._json_bytes(checked)
            if len(encoded) > MAX_REPORT_JSON_BYTES:
                raise ReportStoreError("Report JSON exceeds the 64 MB limit.")
            manifest = _Manifest(
                report_id=checked.report_id,
                json_sha256=hashlib.sha256(encoded).hexdigest(),
                pdf_sha256=hashlib.sha256(pdf).hexdigest(),
            )
            with self._locked():
                directory = self._snapshot_directory(checked.report_id)
                if directory.exists():
                    raise ReportStoreError("This report already exists and is immutable.")
                directory.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=".snapshot-", dir=directory.parent
                ) as temporary:
                    staging = Path(temporary)
                    self._write_atomic(staging / "report.json", encoded)
                    self._write_atomic(staging / "report.pdf", pdf)
                    self._write_atomic(staging / "manifest.json", self._json_bytes(manifest))
                    self._check_directory(directory)
                    os.rename(staging, directory)
        except (OSError, ValueError) as exc:
            raise ReportStoreError(f"Unable to save report: {exc}") from exc

    def load_report(self, report_id: str) -> ReportSnapshot:
        return self._load_bundle(report_id)[0]

    def read_pdf(self, report_id: str) -> bytes:
        """Return the exact generated PDF, only after checking the entire saved bundle."""

        return self._load_bundle(report_id)[1]

    def list_reports(self, document_id: str | None = None) -> list[ReportSnapshot]:
        if document_id is not None:
            self._validate_document_id(document_id)
        directory = self.root / "snapshots"
        self._check_directory(directory)
        if not directory.exists():
            return []
        try:
            reports: list[ReportSnapshot] = []
            for entry in directory.iterdir():
                self._check_directory(entry)
                if entry.name.startswith(".snapshot-"):
                    continue
                report = self.load_report(entry.name)
                if document_id is None or report.document_id == document_id:
                    reports.append(report)
            return sorted(
                reports,
                key=lambda item: (
                    item.created_at.replace(tzinfo=UTC)
                    if item.created_at.tzinfo is None
                    else item.created_at.astimezone(UTC),
                    item.report_id,
                ),
                reverse=True,
            )
        except (OSError, ValueError) as exc:
            raise ReportStoreError(f"Unable to list reports: {exc}") from exc

    def _load_bundle(self, report_id: str) -> tuple[ReportSnapshot, bytes]:
        directory = self._snapshot_directory(report_id)
        try:
            if {path.name for path in directory.iterdir()} != {
                "report.json",
                "report.pdf",
                "manifest.json",
            }:
                raise ReportStoreError("Report bundle has missing or unexpected artifacts.")
            manifest = _Manifest.model_validate_json(
                self._read_bytes(directory / "manifest.json", maximum=4096)
            )
            encoded = self._read_bytes(directory / "report.json")
            pdf = self._read_bytes(directory / "report.pdf", maximum=MAX_REPORT_PDF_BYTES)
            if (
                manifest.schema_version != "1"
                or manifest.report_id != report_id
                or hashlib.sha256(encoded).hexdigest() != manifest.json_sha256
                or hashlib.sha256(pdf).hexdigest() != manifest.pdf_sha256
            ):
                raise ReportStoreError("Report bundle identity or content hashes are inconsistent.")
            report = ReportSnapshot.model_validate_json(encoded)
            self._validate_snapshot(report)
            self._validate_pdf(pdf)
            if report.report_id != report_id:
                raise ReportStoreError("Report identity does not match its directory.")
            return report, pdf
        except (OSError, ValueError) as exc:
            raise ReportStoreError(f"Unable to load report {report_id}: {exc}") from exc

    @classmethod
    def _validate_workspace(cls, workspace: ReportWorkspace) -> None:
        cls._validate_document_id(workspace.document_id)
        cls._validate_uuid(workspace.run_id, "extraction run")
        if any(
            not re.fullmatch(r"req_[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key)
            for key in (*workspace.mappings, *workspace.quantities)
        ):
            raise ReportStoreError("Matching choices contain an invalid requirement ID.")

    @classmethod
    def _validate_snapshot(cls, report: ReportSnapshot) -> None:
        cls._validate_uuid(report.report_id, "report")
        cls._validate_workspace(report.workspace)
        cls._validate_document_id(report.document_id)
        if (
            not re.fullmatch(r"[a-f0-9]{64}", report.document_sha256)
            or report.document_sha256[:16] != report.document_id
        ):
            raise ReportStoreError("Report source document hash and ID do not agree.")
        valid_ids = {item.id for item in report.source_run.extraction.requirements}
        if (
            not set(report.source_review.decisions) <= valid_ids
            or any(
                key != item.requirement_id for key, item in report.source_review.decisions.items()
            )
            or not set(report.workspace.mappings) <= valid_ids
            or not set(report.workspace.quantities) <= valid_ids
        ):
            raise ReportStoreError("Report choices or reviews refer to an unknown requirement.")
        for line, source in zip(
            report.lines, report.source_run.extraction.requirements, strict=True
        ):
            if line.requirement != source:
                raise ReportStoreError("Report line must preserve the full source requirement.")
            if line.capability is not None and line.capability not in report.lab.capabilities:
                raise ReportStoreError("Report capability is absent from its saved lab profile.")
        validate_lab_references(report.lab, report.catalog)

    @staticmethod
    def _validate_document_id(document_id: str) -> None:
        if not isinstance(document_id, str) or not re.fullmatch(r"[a-f0-9]{16}", document_id):
            raise ReportStoreError("Invalid specification ID.")

    @staticmethod
    def _validate_uuid(value: str, kind: str) -> None:
        try:
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("Noncanonical UUID")
        except (ValueError, AttributeError, TypeError) as exc:
            raise ReportStoreError(f"Invalid {kind} ID; a canonical UUID is required.") from exc

    def _workspace_path(self, document_id: str, run_id: str) -> Path:
        self._validate_document_id(document_id)
        self._validate_uuid(run_id, "extraction run")
        path = self.root / "workspaces" / document_id / f"{run_id}.json"
        self._check_directory(path.parent)
        return path

    def _snapshot_directory(self, report_id: str) -> Path:
        self._validate_uuid(report_id, "report")
        directory = self.root / "snapshots" / report_id
        self._check_directory(directory)
        return directory

    @staticmethod
    def _check_directory(path: Path) -> None:
        if ".." in path.parts:
            raise ReportStoreError("Report storage must not contain parent traversal.")
        for part in [*reversed(path.parents), path]:
            if part.is_symlink():
                raise ReportStoreError("Report storage must not traverse symlinks.")
            if part.exists() and not part.is_dir():
                raise ReportStoreError("Report storage path must be a directory.")

    @classmethod
    def _read_bytes(cls, path: Path, *, maximum: int = MAX_REPORT_JSON_BYTES) -> bytes:
        cls._check_directory(path.parent)
        if path.is_symlink():
            raise ReportStoreError("Refusing to read a report artifact through a symlink.")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ReportStoreError("Report artifact must be a regular file.")
            if info.st_size > maximum:
                raise ReportStoreError("Saved report artifact exceeds its size limit.")
            data = handle.read(maximum + 1)
            if len(data) > maximum:
                raise ReportStoreError("Saved report artifact exceeds its size limit.")
            return data

    @classmethod
    def _write_atomic(cls, path: Path, data: bytes) -> None:
        cls._check_directory(path.parent)
        if path.exists() or path.is_symlink():
            # Existing regular workspace files may be atomically replaced, not links/devices.
            cls._read_bytes(path)
        descriptor, name = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if path.is_symlink():
                raise ReportStoreError("Refusing to replace a symlink report artifact.")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _json_bytes(model: BaseModel) -> bytes:
        return (
            json.dumps(
                model.model_dump(mode="json", exclude_computed_fields=True),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _validate_pdf(data: bytes) -> None:
        if not isinstance(data, bytes) or not data or len(data) > MAX_REPORT_PDF_BYTES:
            raise ReportStoreError("Report PDF must be nonempty and at most 25 MB.")
        if b"%PDF-" not in data[:1024]:
            raise ReportStoreError("Report artifact is not a PDF.")
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass or (document.metadata or {}).get("encryption"):
                    raise ReportStoreError("Encrypted report PDFs are not supported.")
                if not 1 <= len(document) <= MAX_REPORT_PAGES:
                    raise ReportStoreError("Report PDF must contain between 1 and 500 pages.")
        except (RuntimeError, ValueError) as exc:
            raise ReportStoreError(f"Unable to open report PDF: {exc}") from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._check_directory(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._check_directory(self.root)
        lock = self.root / ".write.lock"
        if lock.is_symlink():
            raise ReportStoreError("Report write lock must not be a symlink.")
        try:
            descriptor = os.open(
                lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
        except FileExistsError as exc:
            raise ReportStoreError(
                "Another report update is active; retry after it finishes."
            ) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink()

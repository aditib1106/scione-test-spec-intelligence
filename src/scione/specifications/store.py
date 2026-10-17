"""Persistent PDF, extraction, and human-review artifacts for the local prototype.

The original PDF and model extraction are immutable. Review decisions are separate
versioned records, so confirming a requirement never rewrites model output.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.schemas import IngestedDocument, TextExtractionStatus
from scione.specifications.models import CustomerExtractionRun

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 200


class SpecStoreError(RuntimeError):
    """Specification artifacts could not be read or saved; never reset silently."""


class SpecVersionConflict(SpecStoreError):
    """Another review update was saved; reload before replacing it."""


class RequirementReview(BaseModel):
    """Explicit human decision, not an alteration of the model extraction."""

    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    status: Literal["confirmed", "needs_review", "excluded"]
    note: str = Field(default="", max_length=4000)
    updated_at: datetime


class RequirementReviewSet(BaseModel):
    """Decisions attached to exactly one immutable extraction run."""

    model_config = ConfigDict(extra="forbid")
    document_id: str
    run_id: str
    version: int = Field(default=0, ge=0)
    decisions: dict[str, RequirementReview] = Field(default_factory=dict)


class ExtractionFailure(BaseModel):
    """Last failed attempt, separate from the last successful extraction."""

    model_config = ConfigDict(extra="forbid")
    document_id: str
    occurred_at: datetime
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=500)
    strategy: str = Field(min_length=1, max_length=100)
    output_limit: int = Field(gt=0)
    message: str = Field(min_length=1, max_length=4000)
    previous_run_id: str | None = None


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    original_name: str
    created_at: datetime
    schema_version: str = "0.1"


class _LatestRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


@dataclass(frozen=True)
class StoredSpecification:
    document: IngestedDocument
    original_name: str
    created_at: datetime
    pdf_path: Path

    @property
    def document_id(self) -> str:
        return self.document.document_id


@dataclass(frozen=True)
class SpecificationSummary:
    document_id: str
    original_name: str
    created_at: datetime
    page_count: int
    extraction_status: TextExtractionStatus
    latest_run_id: str | None = None
    requirement_count: int = 0
    review_version: int = 0
    confirmed_count: int = 0
    needs_review_count: int = 0


class FileSpecStore:
    """Store under ``root/specifications``; constructing the store performs no I/O."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute() / "specifications"

    def import_pdf(self, filename: str, data: bytes) -> StoredSpecification:
        """Validate, preserve, and parse a PDF; identical bytes reuse the first upload."""

        self._validate_filename(filename)
        self._validate_pdf(data)
        digest = hashlib.sha256(data).hexdigest()
        document_id = digest[:16]
        try:
            with self._locked():
                directory = self._document_directory(document_id)
                if directory.exists():
                    existing = self.load_document(document_id)
                    if existing.document.sha256 != digest:
                        raise SpecStoreError("Document ID collision; no file was replaced.")
                    return existing
                with tempfile.TemporaryDirectory(prefix=".import-", dir=self.root) as temporary:
                    staging = Path(temporary)
                    pdf_path = staging / "original.pdf"
                    pdf_path.write_bytes(data)
                    document = PyMuPDFTextIngestor().ingest(pdf_path)
                    document.file_name = filename
                    metadata = _Metadata(
                        document_id=document_id,
                        original_name=filename,
                        created_at=datetime.now(UTC),
                    )
                    self._write_model(staging / "document.json", document)
                    self._write_model(staging / "metadata.json", metadata)
                    self._check_directory(directory)
                    os.rename(staging, directory)
                return self.load_document(document_id)
        except (OSError, ValueError, IngestionError) as exc:
            raise SpecStoreError(f"Unable to import specification: {exc}") from exc

    def list_documents(self) -> list[SpecificationSummary]:
        """Return newest uploads first; report corrupt records instead of hiding them."""

        self._check_directory(self.root)
        if not self.root.exists():
            return []
        summaries: list[SpecificationSummary] = []
        try:
            for directory in self.root.iterdir():
                if directory.name == ".write.lock" or directory.name.startswith(".import-"):
                    continue
                if not re.fullmatch(r"[a-f0-9]{16}", directory.name):
                    raise SpecStoreError(f"Unexpected specification artifact: {directory.name}")
                item = self.load_document(directory.name)
                run = self.load_latest_extraction(directory.name)
                review = self.load_review(directory.name, run.run_id) if run else None
                summaries.append(
                    SpecificationSummary(
                        document_id=item.document_id,
                        original_name=item.original_name,
                        created_at=item.created_at,
                        page_count=item.document.page_count,
                        extraction_status=item.document.extraction_status,
                        latest_run_id=run.run_id if run else None,
                        requirement_count=len(run.extraction.requirements) if run else 0,
                        review_version=review.version if review else 0,
                        confirmed_count=sum(
                            entry.status == "confirmed" for entry in review.decisions.values()
                        )
                        if review
                        else 0,
                        needs_review_count=sum(
                            entry.status == "needs_review" for entry in review.decisions.values()
                        )
                        if review
                        else 0,
                    )
                )
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to list specifications: {exc}") from exc
        return sorted(summaries, key=lambda item: item.created_at, reverse=True)

    def load_document(self, document_id: str) -> StoredSpecification:
        directory = self._document_directory(document_id)
        try:
            document = IngestedDocument.model_validate_json(
                self._read_bytes(directory / "document.json")
            )
            metadata = _Metadata.model_validate_json(self._read_bytes(directory / "metadata.json"))
            pdf = self._read_bytes(directory / "original.pdf")
            if len(pdf) > MAX_PDF_BYTES:
                raise SpecStoreError("Saved PDF exceeds the 25 MB limit.")
            if (
                document.document_id != document_id
                or metadata.document_id != document_id
                or document.sha256[:16] != document_id
                or hashlib.sha256(pdf).hexdigest() != document.sha256
                or document.file_name != metadata.original_name
                or len(document.pages) != document.page_count
                or not 1 <= document.page_count <= MAX_PDF_PAGES
                or [page.page_number for page in document.pages]
                != list(range(1, document.page_count + 1))
            ):
                raise SpecStoreError(
                    "Saved specification identity, PDF, or page data is inconsistent."
                )
            self._validate_filename(metadata.original_name)
            return StoredSpecification(
                document=document,
                original_name=metadata.original_name,
                created_at=metadata.created_at,
                pdf_path=directory / "original.pdf",
            )
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to load specification {document_id}: {exc}") from exc

    def read_pdf(self, document_id: str) -> bytes:
        """Return the exact uploaded bytes after validating their saved hash."""

        item = self.load_document(document_id)
        return self._read_bytes(item.pdf_path)

    def save_extraction(self, document_id: str, run: CustomerExtractionRun) -> None:
        """Commit an immutable extraction directory, then atomically update latest."""

        try:
            checked = CustomerExtractionRun.model_validate(run.model_dump())
            with self._locked():
                item = self.load_document(document_id)
                if checked.document_id != item.document_id:
                    raise SpecStoreError("Extraction belongs to a different specification.")
                run_directory = self._run_directory(document_id, checked.run_id)
                self._check_directory(run_directory)
                if run_directory.exists():
                    raise SpecStoreError("This extraction run already exists and is immutable.")
                # Check an existing pointer before saving, never silently repair corruption.
                self.load_latest_extraction(document_id)
                run_directory.parent.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=".run-", dir=run_directory.parent
                ) as temporary:
                    staging = Path(temporary)
                    self._write_model(staging / "run.json", checked)
                    self._write_model(staging / "prediction.json", checked.extraction)
                    (staging / "raw_response.txt").write_bytes(checked.raw_response.encode("utf-8"))
                    os.rename(staging, run_directory)
                self._write_model(
                    self._document_directory(document_id) / "latest_run.json",
                    _LatestRun(run_id=checked.run_id),
                )
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to save extraction: {exc}") from exc

    def load_extraction(self, document_id: str, run_id: str) -> CustomerExtractionRun:
        """Load a specific historical run without changing the latest selection."""

        self.load_document(document_id)
        try:
            run = CustomerExtractionRun.model_validate_json(
                self._read_bytes(self._run_directory(document_id, run_id) / "run.json")
            )
            if run.document_id != document_id or run.run_id != run_id:
                raise SpecStoreError("Saved extraction identity does not match its directory.")
            return run
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to load extraction {run_id}: {exc}") from exc

    def list_extractions(self, document_id: str) -> list[CustomerExtractionRun]:
        """Read this document's saved runs newest first without selecting a run.

        In-progress staging directories are omitted. Corrupt runs, unexpected
        artifacts, and symlinks are reported rather than silently hidden. Listing
        history never moves the latest pointer or creates/changes human reviews.
        """

        self.load_document(document_id)
        directory = self._document_directory(document_id) / "runs"
        self._check_directory(directory)
        if not directory.exists():
            return []
        try:
            runs: list[CustomerExtractionRun] = []
            for entry in directory.iterdir():
                self._check_directory(entry)
                if entry.name.startswith(".run-"):
                    continue
                runs.append(self.load_extraction(document_id, entry.name))
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to list extractions: {exc}") from exc

        # Historical metadata may predate timezone-aware timestamps. Interpret
        # those timestamps consistently as UTC, never the machine's local zone.
        return sorted(
            runs,
            key=lambda run: (
                run.started_at.replace(tzinfo=UTC)
                if run.started_at.tzinfo is None
                else run.started_at.astimezone(UTC),
                run.run_id,
            ),
            reverse=True,
        )

    def load_latest_extraction(self, document_id: str) -> CustomerExtractionRun | None:
        self.load_document(document_id)
        directory = self._document_directory(document_id)
        pointer = directory / "latest_run.json"
        if not pointer.exists() and not pointer.is_symlink():
            runs = directory / "runs"
            self._check_directory(runs)
            if runs.exists() and any(not path.name.startswith(".run-") for path in runs.iterdir()):
                raise SpecStoreError(
                    "Saved runs exist without a latest-run pointer; data preserved."
                )
            return None
        try:
            latest = _LatestRun.model_validate_json(self._read_bytes(pointer))
            return self.load_extraction(document_id, latest.run_id)
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to load latest extraction: {exc}") from exc

    def load_review(self, document_id: str, run_id: str) -> RequirementReviewSet:
        run = self.load_extraction(document_id, run_id)
        path = self._run_directory(document_id, run_id) / "review.json"
        if not path.exists() and not path.is_symlink():
            return RequirementReviewSet(document_id=document_id, run_id=run_id)
        try:
            review = RequirementReviewSet.model_validate_json(self._read_bytes(path))
            valid_ids = {item.id for item in run.extraction.requirements}
            if (
                review.document_id != document_id
                or review.run_id != run_id
                or not set(review.decisions) <= valid_ids
                or any(key != value.requirement_id for key, value in review.decisions.items())
            ):
                raise SpecStoreError("Saved review does not match its extraction run.")
            return review
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to load human review: {exc}") from exc

    def save_review(
        self,
        document_id: str,
        run_id: str,
        requirement_id: str,
        decision: Literal["confirmed", "needs_review", "excluded"],
        note: str = "",
        *,
        expected_version: int | None = None,
    ) -> RequirementReviewSet:
        """Save an explicit decision separately; stale editors cannot overwrite reviews."""

        try:
            with self._locked():
                run = self.load_extraction(document_id, run_id)
                if requirement_id not in {item.id for item in run.extraction.requirements}:
                    raise SpecStoreError("Unknown requirement in this extraction run.")
                review = self.load_review(document_id, run_id)
                if expected_version is not None and review.version != expected_version:
                    raise SpecVersionConflict("Review changed. Reload before saving again.")
                review.decisions[requirement_id] = RequirementReview(
                    requirement_id=requirement_id,
                    status=decision,
                    note=note,
                    updated_at=datetime.now(UTC),
                )
                review.version += 1
                self._write_model(self._run_directory(document_id, run_id) / "review.json", review)
                return review
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to save human review: {exc}") from exc

    def save_failure(self, failure: ExtractionFailure) -> None:
        """Persist a diagnostic without replacing any result or review."""

        try:
            checked = ExtractionFailure.model_validate(failure.model_dump())
            with self._locked():
                self.load_document(checked.document_id)
                self._write_model(
                    self._document_directory(checked.document_id) / "latest_failure.json", checked
                )
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to save analysis diagnostic: {exc}") from exc

    def load_failure(self, document_id: str) -> ExtractionFailure | None:
        self.load_document(document_id)
        path = self._document_directory(document_id) / "latest_failure.json"
        if not path.exists() and not path.is_symlink():
            return None
        try:
            failure = ExtractionFailure.model_validate_json(self._read_bytes(path))
            if failure.document_id != document_id:
                raise SpecStoreError("Saved failure does not match its document.")
            return failure
        except (OSError, ValueError) as exc:
            raise SpecStoreError(f"Unable to load analysis diagnostic: {exc}") from exc

    @staticmethod
    def _validate_filename(filename: str) -> None:
        if (
            not isinstance(filename, str)
            or not filename.strip()
            or len(filename) > 240
            or Path(filename).name != filename
            or "\\" in filename
            or "\x00" in filename
            or Path(filename).suffix.lower() != ".pdf"
        ):
            raise SpecStoreError("Upload a PDF using a plain filename without path components.")

    @staticmethod
    def _validate_pdf(data: bytes) -> None:
        if not isinstance(data, bytes) or not data or len(data) > MAX_PDF_BYTES:
            raise SpecStoreError("Upload a nonempty PDF of at most 25 MB.")
        if b"%PDF-" not in data[:1024]:
            raise SpecStoreError("Uploaded file is not a PDF.")
        try:
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass or (pdf.metadata or {}).get("encryption"):
                    raise SpecStoreError("Encrypted or password-protected PDFs are not supported.")
                if not 1 <= len(pdf) <= MAX_PDF_PAGES:
                    raise SpecStoreError("PDF must contain between 1 and 200 pages.")
        except (RuntimeError, ValueError) as exc:
            raise SpecStoreError(f"Unable to read uploaded PDF: {exc}") from exc

    def _document_directory(self, document_id: str) -> Path:
        if not isinstance(document_id, str) or not re.fullmatch(r"[a-f0-9]{16}", document_id):
            raise SpecStoreError("Invalid specification ID.")
        directory = self.root / document_id
        self._check_directory(directory)
        return directory

    def _run_directory(self, document_id: str, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id
        ):
            raise SpecStoreError("Invalid extraction run ID.")
        directory = self._document_directory(document_id) / "runs" / run_id
        self._check_directory(directory)
        return directory

    @staticmethod
    def _check_directory(path: Path) -> None:
        if ".." in path.parts:
            raise SpecStoreError("Specification storage must not contain parent traversal.")
        for part in [*reversed(path.parents), path]:
            if part.is_symlink():
                raise SpecStoreError("Specification storage must not traverse symlinks.")
            if part.exists() and not part.is_dir():
                raise SpecStoreError("Specification storage path must be a directory.")

    @classmethod
    def _read_bytes(cls, path: Path) -> bytes:
        cls._check_directory(path.parent)
        if path.is_symlink():
            raise SpecStoreError("Refusing to read a specification artifact through a symlink.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SpecStoreError("Specification artifact must be a regular file.")
            return handle.read()

    @classmethod
    def _write_model(cls, path: Path, model: BaseModel) -> None:
        cls._check_directory(path.parent)
        if path.is_symlink():
            raise SpecStoreError("Refusing to replace a symlink specification artifact.")
        descriptor, filename = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    model.model_dump(mode="json", exclude_computed_fields=True),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if path.is_symlink():
                raise SpecStoreError("Refusing to replace a symlink specification artifact.")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._check_directory(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._check_directory(self.root)
        lock = self.root / ".write.lock"
        try:
            descriptor = os.open(
                lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
        except FileExistsError as exc:
            raise SpecStoreError(
                "Another specification update is active; retry after it finishes."
            ) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink()

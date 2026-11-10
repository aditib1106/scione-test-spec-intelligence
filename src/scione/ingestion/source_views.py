"""Versioned geometric views of legacy native PDFs, without replacing history.

The uploaded bytes and original document.json remain immutable. A derived view
can repair content-stream order (for example a value drawn as a later overlay)
for current analysis/review; it is not OCR or a document revision.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pymupdf
from pydantic import ValidationError

from scione.ingestion.base import IngestionError
from scione.ingestion.pdf_text import PyMuPDFTextIngestor
from scione.schemas import IngestedDocument, TextExtractionStatus, TextReadingOrder

SOURCE_VIEW_POLICY = "native_geometric_source_view_v1"
SOURCE_VIEW_CACHE_NAME = "geometric_v1.json"


class SourceViewError(IngestionError):
    """The current source view could not be prepared safely; history is kept."""


def _payload(document: IngestedDocument) -> dict:
    return document.model_dump(
        mode="json", exclude={"total_character_count", "total_non_whitespace_character_count"}
    )


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _native_legacy(document: IngestedDocument) -> bool:
    if (
        document.media_type != "application/pdf"
        or document.extraction_status != TextExtractionStatus.READY
        or document.text_reading_order != TextReadingOrder.CONTENT_STREAM
    ):
        return False
    backend_keys = {
        "ingestion_backend",
        "text_extractor",
        "extractor",
        "text_source",
        "extraction_method",
        "ocr_engine",
        "ocr_backend",
    }
    native = {"pymupdf", "pymupdf_text", "native_pdf", "native", "pdf_text"}
    for key, value in document.metadata.items():
        key = key.casefold()
        value = value.casefold().strip()
        if key in backend_keys and value and value not in native:
            return False
        if "ocr" in key and value not in {"", "false", "none", "0", "no"}:
            return False
    return True


def _ordered_pages(document: IngestedDocument) -> bool:
    return (
        document.page_count == len(document.pages)
        and [page.page_number for page in document.pages] == list(range(1, document.page_count + 1))
        and all(
            page.character_count == len(page.text)
            and page.non_whitespace_character_count == sum(not c.isspace() for c in page.text)
            for page in document.pages
        )
    )


def _guard_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise SourceViewError("The derived source cache path is unsafe. Original files are kept.")


def _guard_cache(path: Path) -> None:
    _guard_directory(path.parent.parent)
    _guard_directory(path.parent)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SourceViewError("The derived source cache file is unsafe. Original files are kept.")


def _read_cached(path: Path, expected: dict, original: IngestedDocument) -> IngestedDocument | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or any(
            value.get(key) != item for key, item in expected.items()
        ):
            return None
        data = value.get("document")
        if not isinstance(data, dict) or value.get("view_sha256") != _digest(data):
            return None
        view = IngestedDocument.model_validate(data)
        if (
            view.document_id != original.document_id
            or view.sha256 != original.sha256
            or view.file_name != original.file_name
            or view.media_type != original.media_type
            or view.page_count != original.page_count
            or not _ordered_pages(view)
            or view.text_reading_order != TextReadingOrder.GEOMETRIC
            or view.extraction_status != TextExtractionStatus.READY
            or view.metadata.get("source_view_policy") != SOURCE_VIEW_POLICY
        ):
            return None
        return view
    except (OSError, ValueError, TypeError, ValidationError):
        return None


def _write_cached(path: Path, expected: dict, view: IngestedDocument) -> None:
    _guard_directory(path.parent.parent)
    path.parent.parent.mkdir(exist_ok=True)
    _guard_directory(path.parent)
    path.parent.mkdir(exist_ok=True)
    _guard_cache(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = _payload(view)
    envelope = {**expected, "view_sha256": _digest(payload), "document": payload}
    try:
        # Exclusive creation never follows an existing temporary symlink.
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _guard_cache(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def current_document_view(
    workspace_root: Path, original: IngestedDocument, pdf_path: Path
) -> IngestedDocument:
    """Return the current native text view; keep the stored ingestion unchanged.

    Non-native/OCR/sparse inputs and already geometric records are returned as
    supplied. Reordering is cached by physical PDF, source representation,
    reading policy and parser version. Cache corruption only regenerates derived
    data; an altered PDF or unsafe path is a controlled failure.
    """
    if not _native_legacy(original):
        return original
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", original.document_id):
        raise SourceViewError("The source document identifier is invalid. Original files are kept.")
    if not _ordered_pages(original):
        raise SourceViewError("The saved source page order is invalid. Original files are kept.")
    source = Path(pdf_path)
    if source.is_symlink() or not source.is_file():
        raise SourceViewError("The saved source PDF is unavailable. Original records are kept.")
    try:
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise SourceViewError(
            "The saved source PDF could not be read. Original records are kept."
        ) from exc
    if source_sha != original.sha256:
        raise SourceViewError(
            "The saved PDF differs from the uploaded source. Original records are kept."
        )
    root = Path(workspace_root)
    _guard_directory(root)
    cache = root / "derived_documents" / original.document_id / SOURCE_VIEW_CACHE_NAME
    _guard_cache(cache)
    expected = {
        "policy_version": SOURCE_VIEW_POLICY,
        "pdf_sha256": source_sha,
        "original_document_sha256": _digest(_payload(original)),
        "pymupdf_version": pymupdf.VersionBind,
        "reading_order": "geometric",
    }
    if cached := _read_cached(cache, expected, original):
        return cached
    try:
        fresh = PyMuPDFTextIngestor(reading_order=TextReadingOrder.GEOMETRIC).ingest(source)
    except IngestionError as exc:
        raise SourceViewError(
            "The current PDF reading order could not be prepared. Original files are kept."
        ) from exc
    if (
        fresh.sha256 != source_sha
        or fresh.page_count != original.page_count
        or fresh.text_reading_order != TextReadingOrder.GEOMETRIC
        or fresh.extraction_status != TextExtractionStatus.READY
        or not _ordered_pages(fresh)
    ):
        raise SourceViewError(
            "The current PDF reading order could not be verified. Original files are kept."
        )
    view = fresh.model_copy(
        update={
            "document_id": original.document_id,
            "file_name": original.file_name,
            "metadata": {
                **fresh.metadata,
                "source_view_policy": SOURCE_VIEW_POLICY,
                "source_view_original_order": str(original.text_reading_order),
                "source_view_engine": f"pymupdf-{pymupdf.VersionBind}",
            },
        }
    )
    try:
        _write_cached(cache, expected, view)
    except OSError:
        # A derived cache is optional. A verified view can still be used when
        # storage is read-only/full; no source or accepted result is replaced.
        pass
    return view

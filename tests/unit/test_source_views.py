"""Current reading order is derived data, never a replacement for source history."""

import json
import re
from hashlib import sha256

import pymupdf
import pytest

from scione.ingestion import source_views
from scione.ingestion.pdf_text import PyMuPDFTextIngestor
from scione.ingestion.source_views import (
    SOURCE_VIEW_CACHE_NAME,
    SOURCE_VIEW_POLICY,
    SourceViewError,
    current_document_view,
)
from scione.schemas import TextExtractionStatus, TextReadingOrder


def overlay_source(tmp_path):
    path = tmp_path / "customer-overlay.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((40, 90), "Initial gloss must be")
        page.insert_text((240, 90), "GU on every panel.")
        page.insert_text(
            (40, 130), "Record each individual panel reading and retain the arithmetic mean."
        )
        page.insert_text(
            (40, 170),
            "Use the stated source requirements and keep independent conditions separate.",
        )
        page.insert_text((40, 730), "Customer document / page 1 / revision B")
        # This overlay is written last even though it belongs inside line one.
        page.insert_text((180, 90), "85-95")
        second = pdf.new_page(width=600, height=800)
        second.insert_text(
            (40, 100), "Second physical page: keep material and measurement conditions together."
        )
        second.insert_text(
            (40, 130), "The customer retains release approval after reviewing the measured results."
        )
        pdf.save(path)
    stored = PyMuPDFTextIngestor().ingest(path)
    assert stored.extraction_status == TextExtractionStatus.READY
    original_json = tmp_path / "document.json"
    original_json.write_text(stored.model_dump_json(indent=2))
    return path, stored, original_json


def cache_path(root, stored):
    return root / "derived_documents" / stored.document_id / SOURCE_VIEW_CACHE_NAME


def test_overlay_value_returns_to_its_visual_line_without_touching_source_history(tmp_path):
    path, stored, document_file = overlay_source(tmp_path)
    originals = {item: item.read_bytes() for item in (path, document_file)}
    view = current_document_view(tmp_path, stored, path)
    assert stored.text_reading_order == TextReadingOrder.CONTENT_STREAM
    assert not re.search(r"must be\s+85-95\s+GU", stored.pages[0].text)
    assert re.search(r"must be\s+85-95\s+GU", view.pages[0].text)
    assert view.text_reading_order == TextReadingOrder.GEOMETRIC
    assert view.document_id == stored.document_id
    assert view.file_name == stored.file_name
    assert view.sha256 == stored.sha256 == sha256(path.read_bytes()).hexdigest()
    assert view.metadata["source_view_policy"] == SOURCE_VIEW_POLICY
    assert view.metadata["source_view_original_order"] == "content_stream"
    envelope = json.loads(cache_path(tmp_path, stored).read_text())
    assert envelope["pdf_sha256"] == stored.sha256
    assert envelope["reading_order"] == "geometric"
    assert envelope["pymupdf_version"] == pymupdf.VersionBind
    assert {item: item.read_bytes() for item in originals} == originals


def test_verified_cache_hit_does_not_reparse_the_pdf(tmp_path, monkeypatch):
    path, stored, _ = overlay_source(tmp_path)
    first = current_document_view(tmp_path, stored, path)

    def no_reparse(*_args, **_kwargs):
        raise AssertionError("The existing verified native view should be reused.")

    monkeypatch.setattr(source_views.PyMuPDFTextIngestor, "ingest", no_reparse)
    assert current_document_view(tmp_path, stored, path) == first


@pytest.mark.parametrize("corruption", ["malformed", "engine", "policy", "view_hash", "pages"])
def test_invalid_derived_cache_is_regenerated_with_source_and_archive_unchanged(
    tmp_path, corruption
):
    path, stored, original_json = overlay_source(tmp_path)
    first = current_document_view(tmp_path, stored, path)
    originals = {item: item.read_bytes() for item in (path, original_json)}
    cache = cache_path(tmp_path, stored)
    envelope = json.loads(cache.read_text())
    if corruption == "malformed":
        cache.write_text("{unfinished")
    else:
        if corruption == "engine":
            envelope["pymupdf_version"] = "older-parser"
        elif corruption == "policy":
            envelope["policy_version"] = "other-policy"
        elif corruption == "view_hash":
            envelope["view_sha256"] = "0" * 64
        elif corruption == "pages":
            envelope["document"]["pages"].reverse()
            envelope["view_sha256"] = sha256(
                json.dumps(
                    envelope["document"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        cache.write_text(json.dumps(envelope))
    assert current_document_view(tmp_path, stored, path) == first
    assert {item: item.read_bytes() for item in originals} == originals


def test_mismatched_physical_source_never_replaces_a_valid_cache(tmp_path):
    path, stored, _ = overlay_source(tmp_path)
    current_document_view(tmp_path, stored, path)
    cache = cache_path(tmp_path, stored)
    old_cache = cache.read_bytes()
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(SourceViewError, match="differs from the uploaded source"):
        current_document_view(tmp_path, stored, path)
    assert cache.read_bytes() == old_cache


def test_reordered_archived_pages_and_unsafe_ids_are_not_silently_repaired(tmp_path):
    path, stored, _ = overlay_source(tmp_path)
    for source in (
        stored.model_copy(update={"pages": list(reversed(stored.pages))}),
        stored.model_copy(update={"document_id": "../outside"}),
    ):
        with pytest.raises(SourceViewError):
            current_document_view(tmp_path, source, path)


@pytest.mark.parametrize(
    "updates",
    [
        {"text_reading_order": TextReadingOrder.GEOMETRIC},
        {"extraction_status": TextExtractionStatus.POSSIBLE_SCANNED_PDF},
        {"extraction_status": TextExtractionStatus.EMPTY},
        {"media_type": "text/plain"},
        {"metadata": {"text_source": "ocr"}},
        {"metadata": {"ocr_engine": "tesseract"}},
        {"metadata": {"extraction_method": "manual_transcription"}},
    ],
)
def test_already_geometric_ocr_and_non_native_sources_are_not_reparsed(tmp_path, updates):
    _path, stored, _ = overlay_source(tmp_path)
    source = stored.model_copy(update=updates)
    assert current_document_view(tmp_path, source, tmp_path / "does-not-exist.pdf") is source
    assert not (tmp_path / "derived_documents").exists()


@pytest.mark.parametrize("unsafe", ["directory", "file", "pdf"])
def test_symlinked_cache_or_source_paths_are_rejected_without_touching_target(tmp_path, unsafe):
    path, stored, _ = overlay_source(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / "private.json"
    target.write_text("untouched")
    if unsafe == "directory":
        (tmp_path / "derived_documents").symlink_to(elsewhere, target_is_directory=True)
    elif unsafe == "file":
        cache = cache_path(tmp_path, stored)
        cache.parent.mkdir(parents=True)
        cache.symlink_to(target)
    else:
        link = tmp_path / "source-link.pdf"
        link.symlink_to(path)
        path = link
    with pytest.raises(SourceViewError):
        current_document_view(tmp_path, stored, path)
    assert target.read_text() == "untouched"


def test_read_only_derived_cache_does_not_prevent_a_verified_source_view(tmp_path, monkeypatch):
    path, stored, original_json = overlay_source(tmp_path)
    archived = original_json.read_bytes()

    def read_only(*_args):
        raise PermissionError("Derived cache is read only")

    monkeypatch.setattr(source_views, "_write_cached", read_only)
    view = current_document_view(tmp_path, stored, path)
    assert view.text_reading_order == TextReadingOrder.GEOMETRIC
    assert original_json.read_bytes() == archived


def test_wrong_ingestor_result_is_not_labelled_as_geometric(tmp_path, monkeypatch):
    path, stored, _ = overlay_source(tmp_path)
    monkeypatch.setattr(source_views.PyMuPDFTextIngestor, "ingest", lambda *_args: stored)
    with pytest.raises(SourceViewError, match="could not be verified"):
        current_document_view(tmp_path, stored, path)

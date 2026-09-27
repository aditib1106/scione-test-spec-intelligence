"""Unit tests for text-based PDF ingestion."""

from pathlib import Path

import pymupdf
import pytest

from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.schemas import TextExtractionStatus


def test_ingests_page_aware_text_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "example.pdf"
    with pymupdf.open() as pdf:
        first_page = pdf.new_page()
        first_page.insert_text((72, 72), "Method A requires five exposure cycles.")
        second_page = pdf.new_page()
        second_page.insert_text((72, 72), "Report the final classification.")
        pdf.save(pdf_path)

    document = PyMuPDFTextIngestor(minimum_document_characters=20).ingest(pdf_path)

    assert document.page_count == 2
    assert document.pages[0].page_number == 1
    assert "five exposure cycles" in document.pages[0].text
    assert "=== PAGE 2 ===" in document.as_prompt_text()
    assert document.extraction_status == TextExtractionStatus.READY
    assert len(document.sha256) == 64


def test_flags_image_only_pdf_for_future_ocr(tmp_path: Path) -> None:
    pdf_path = tmp_path / "image-only.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(pdf_path)

    document = PyMuPDFTextIngestor().ingest(pdf_path)

    assert document.extraction_status == TextExtractionStatus.EMPTY
    assert "image-only" in document.warnings[0]


def test_rejects_non_pdf_input(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text("not a pdf")

    with pytest.raises(IngestionError, match=r"Expected a \.pdf file"):
        PyMuPDFTextIngestor().ingest(text_path)

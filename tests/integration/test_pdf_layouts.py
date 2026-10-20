"""Keep layout regressions without shipping an obsolete standard PDF fixture."""

from pathlib import Path

import pymupdf
import pytest

from scione.ingestion import PyMuPDFTextIngestor
from scione.schemas import TextExtractionStatus, TextReadingOrder


@pytest.fixture
def customer_layout_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "fictional-customer-layouts.pdf"
    with pymupdf.open() as pdf:
        single = pdf.new_page()
        single.insert_textbox(
            pymupdf.Rect(45, 45, 550, 350),
            "Fictional customer specification.\n"
            "Single-column overview: coated panels require cross-cut adhesion and pencil "
            "hardness testing. Preserve the physical source pages for human review. "
            "This text is an offline fixture, not a licensed standard or a real order.",
        )
        columns = pdf.new_page()
        # Deliberately insert the right column first: reading-order modes must
        # stay explicit instead of assuming every PDF's content stream is visual order.
        columns.insert_textbox(
            pymupdf.Rect(320, 45, 550, 400),
            "RIGHT PANEL: Pencil hardness\nMethod: ISO 15184\nLoad: 750 g\n"
            "Acceptance: at least F. Applies to flat coated panels.",
        )
        columns.insert_textbox(
            pymupdf.Rect(45, 45, 275, 400),
            "LEFT PANEL: Cross-cut adhesion\nMethod: ISO 2409\nSpacing: 2 mm\n"
            "Acceptance: GT 0 to 1. Applies to steel panels.",
        )
        pdf.save(path)
    return path


@pytest.mark.parametrize("reading_order", list(TextReadingOrder))
def test_current_customer_pdf_layouts_keep_pages_and_both_columns(
    customer_layout_pdf: Path,
    reading_order: TextReadingOrder,
) -> None:
    original = customer_layout_pdf.read_bytes()
    document = PyMuPDFTextIngestor(reading_order=reading_order).ingest(customer_layout_pdf)

    assert document.page_count == 2
    assert document.extraction_status == TextExtractionStatus.READY
    assert "Single-column overview" in document.pages[0].text
    text = document.pages[1].text
    for fragment in ("LEFT PANEL", "RIGHT PANEL", "ISO 2409", "ISO 15184", "750 g", "2 mm"):
        assert fragment in text
    assert "=== PAGE 2 ===" in document.as_prompt_text()
    assert customer_layout_pdf.read_bytes() == original
    if reading_order == TextReadingOrder.CONTENT_STREAM:
        assert text.index("RIGHT PANEL") < text.index("LEFT PANEL")
    else:
        assert text.index("LEFT PANEL") < text.index("RIGHT PANEL")

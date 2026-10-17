"""Page previews are local PNG bytes with controlled size and page bounds."""

import hashlib
import struct

import pymupdf
import pytest

from scione.specifications.pdf_preview import (
    PDFPreviewError,
    inspect_pdf_highlights,
    render_pdf_page,
)


def pdf_bytes() -> bytes:
    with pymupdf.open() as pdf:
        pdf.new_page(width=600, height=900).insert_text((50, 50), "First page")
        pdf.new_page(width=600, height=900).insert_text((50, 50), "Second page")
        return pdf.tobytes()


def test_renders_png_for_each_page_and_bounds_dimensions() -> None:
    data = pdf_bytes()
    before = hashlib.sha256(data).digest()
    first = render_pdf_page(data, 1, scale=20)
    second = render_pdf_page(data, 2)
    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert second.startswith(b"\x89PNG\r\n\x1a\n")
    assert first != second
    width, height = struct.unpack(">II", first[16:24])
    assert max(width, height) <= 1500
    assert hashlib.sha256(data).digest() == before


@pytest.mark.parametrize("page", [0, -1, 3, True, "1"])
def test_invalid_page_numbers_rejected(page: object) -> None:
    with pytest.raises(PDFPreviewError, match="Page"):
        render_pdf_page(pdf_bytes(), page)  # type: ignore[arg-type]


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_invalid_render_scales_rejected(scale: float) -> None:
    with pytest.raises(PDFPreviewError, match="scale"):
        render_pdf_page(pdf_bytes(), scale=scale)


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-corrupt"])
def test_invalid_document_rejected(data: bytes) -> None:
    with pytest.raises(PDFPreviewError):
        render_pdf_page(data)


def test_encrypted_preview_rejected() -> None:
    with pymupdf.open() as pdf:
        pdf.new_page()
        data = pdf.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret"
        )
    with pytest.raises(PDFPreviewError, match="Encrypted"):
        render_pdf_page(data)


def table_pdf_bytes() -> bytes:
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.insert_text((30, 40), "Mechanical tests")
        page.insert_text((30, 90), "Cross-cut adhesion")
        page.insert_text((210, 90), "ISO 2409, 2 mm spacing, all\nbake conditions, all substrates")
        page.insert_text((460, 90), "GT 0 to 1")
        page.insert_text((30, 170), "Pencil hardness")
        page.insert_text((210, 170), "ISO 15184, 750 g")
        page.insert_text((460, 170), ">= F")
        page.insert_text((30, 230), "Not part of the highlighted row")
        pdf.new_page().insert_text((30, 40), "Other page only")
        return pdf.tobytes()


def test_locates_a_wrapped_table_quote_despite_added_pipes_and_unicode_hyphen() -> None:
    quote = (
        "Cross\u2011cut adhesion | ISO 2409, 2 mm spacing, all bake conditions, "
        "all substrates | GT 0 to 1"
    )
    result = inspect_pdf_highlights(table_pdf_bytes(), 1, [quote])

    assert result.matched_count == 1
    assert result.partial_count == result.unmatched_count == result.ambiguous_count == 0
    match = result.matches[0]
    assert match.quote == quote
    assert match.matched_segments == match.total_segments == 3
    assert len(result.rectangles) == 4  # Name, two wrapped method lines, criterion.
    assert all(70 < rect[1] < 110 and rect[3] < 120 for rect in result.rectangles)
    # Highlight separate cells, never a rectangle spanning the whole row.
    assert all(rect[2] - rect[0] < 220 for rect in result.rectangles)


def test_highlights_change_pixels_but_never_change_the_original_pdf() -> None:
    data = table_pdf_bytes()
    before = hashlib.sha256(data).digest()
    normal = render_pdf_page(data)
    highlighted = render_pdf_page(data, highlight_quotes=["Cross-cut adhesion"])

    assert highlighted != normal
    assert highlighted.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(data).digest() == before
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert list(pdf[0].annots()) == []


def test_partial_table_quote_reports_unmatched_cells_instead_of_inventing_locations() -> None:
    result = inspect_pdf_highlights(
        table_pdf_bytes(), 1, ["Cross-cut adhesion | ISO 99999, 8 mm spacing | GT 0 to 1"]
    )
    match = result.matches[0]

    assert match.status == "partial"
    assert result.partial_count == 1
    assert match.matched_segments == 2
    assert match.total_segments == 3
    assert len(match.rectangles) == 2
    assert not any(200 < rect[0] < 400 for rect in match.rectangles)


def test_repeated_quote_is_ambiguous_and_does_not_highlight_an_arbitrary_occurrence() -> None:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 50), "Same test")
        page.insert_text((30, 100), "Same test")
        data = pdf.tobytes()

    result = inspect_pdf_highlights(data, quotes=["Same test"])
    assert result.matches[0].status == "ambiguous"
    assert result.ambiguous_count == 1
    assert result.rectangles == ()
    assert render_pdf_page(data, highlight_quotes=["Same test"]) == render_pdf_page(data)


def test_normalizes_inequality_typography_without_losing_its_direction() -> None:
    data = table_pdf_bytes()
    correct = inspect_pdf_highlights(data, quotes=["\u2265 F"])
    wrong = inspect_pdf_highlights(data, quotes=["\u2264 F"])

    assert correct.matched_count == 1
    assert wrong.unmatched_count == 1
    assert wrong.rectangles == ()


def test_no_cross_page_or_fuzzy_semantic_match() -> None:
    result = inspect_pdf_highlights(
        table_pdf_bytes(),
        page_number=1,
        quotes=["Other page only", "Cross-cut adhesion | ISO 2409, 20 mm spacing"],
    )
    assert result.matches[0].status == "unmatched"
    assert result.matches[0].rectangles == ()
    assert result.matches[1].status == "partial"
    assert result.matches[1].matched_segments == 1


def test_whitespace_and_case_are_normalized_without_matching_substrings() -> None:
    data = table_pdf_bytes()
    result = inspect_pdf_highlights(data, quotes=["  CROSS-cut\n adhesion\t", "cut adhes"])
    assert result.matches[0].status == "matched"
    assert result.matches[1].status == "unmatched"


def test_cannot_drop_a_sign_or_unit_from_inside_a_source_word() -> None:
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30, 50), "Condition -20 at >=5 and 60%")
        data = pdf.tobytes()
    result = inspect_pdf_highlights(data, quotes=["20", "5", "60", "-20", ">=5", "60%"])
    assert [match.status for match in result.matches] == [
        "unmatched",
        "unmatched",
        "unmatched",
        "matched",
        "matched",
        "matched",
    ]


def test_does_not_merge_wide_table_gaps_even_in_one_native_pdf_text_line() -> None:
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=200)
        page.insert_text(
            (30, 80), "Adhesion                       ISO 2409                       GT 1"
        )
        data = pdf.tobytes()
    result = inspect_pdf_highlights(data, quotes=["Adhesion | ISO 2409 | GT 1"])
    assert result.matched_count == 1
    assert len(result.rectangles) == 3
    assert all(rect[2] - rect[0] < 70 for rect in result.rectangles)


@pytest.mark.parametrize("quotes", ["one quote", [17], ["x" * 8001], ["x"] * 101])
def test_invalid_highlight_inputs_rejected(quotes: object) -> None:
    with pytest.raises(PDFPreviewError, match="[Hh]ighlight"):
        inspect_pdf_highlights(pdf_bytes(), quotes=quotes)  # type: ignore[arg-type]


def test_no_text_and_empty_quotes_give_no_highlights() -> None:
    with pymupdf.open() as pdf:
        pdf.new_page()
        data = pdf.tobytes()
    assert inspect_pdf_highlights(data).matches == ()
    result = inspect_pdf_highlights(data, quotes=["Requested test", " | \n | "])
    assert result.unmatched_count == 2
    assert result.rectangles == ()

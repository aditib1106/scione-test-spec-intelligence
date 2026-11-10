"""Only exact bank-label formatting may be removed with physical-source proof."""

import pytest

from scione.catalogs.models import CanonicalTest, ReferenceCatalog, TestMethod
from scione.schemas.documents import DocumentPage, IngestedDocument
from scione.specifications.method_labels import canonicalize_method_label
from scione.specifications.models import RequirementEvidence


def document(*texts):
    return IngestedDocument(
        document_id="example",
        file_name="example.pdf",
        sha256="a" * 64,
        page_count=len(texts),
        text_reading_order="geometric",
        extraction_status="ready",
        pages=[
            DocumentPage(
                page_number=index,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
            for index, text in enumerate(texts, start=1)
        ],
    )


def proof(raw, text=None, *, quote=None, page=1, doc=None, **kwargs):
    text = raw if text is None else text
    return canonicalize_method_label(
        raw,
        [RequirementEvidence(page=page, quote=quote or text)],
        doc or document(text),
        **kwargs,
    )


@pytest.mark.parametrize(
    "raw,code",
    [
        ("TEX-05 Rub colorfastness - ISO 60147:2020", "ISO 60147:2020"),
        ("PLR-06 Heat deflection temperature: ISO 60221:2017", "ISO 60221:2017"),
        ("COR-04 Dry film thickness: ISO 63883-2:2016", "ISO 63883-2:2016"),
        ("COR-02 Cross-cut adhesion: DIN EN ISO 61689:2026-02", "DIN EN ISO 61689:2026-02"),
        ("COR-06 Specular gloss: ASTM D1779-25", "ASTM D1779-25"),
        ("ISO 60570:2025 (COR-11)", "ISO 60570:2025"),
    ],
)
def test_complete_source_identifier_and_offsets_are_preserved(raw, code):
    text = "Page header\n  " + raw + "\nPage footer"
    result = proof(raw, text)
    assert result is not None
    assert result.code == code
    assert result.raw_reference == raw
    assert text[slice(*result.source_span)] == raw
    assert text[slice(*result.source_code_span)] == code
    assert text[slice(*result.parent_evidence_span)] == text
    assert result.bank_record_id
    assert result.page == 1


@pytest.mark.parametrize(
    "raw",
    [
        "modified TEX-05 Rub colorfastness - ISO 60147:2020",
        "alternative TEX-05 Rub colorfastness - ISO 60147:2020",
        "except TEX-05 Rub colorfastness - ISO 60147:2020",
        "custom TEX-05 Rub colorfastness - ISO 60147:2020",
        "TEX-05 Rub colorfastness - ISO 60147:2020 modified",
        "TEX-05 Rub colorfastness - ISO 60147:2020, approval required",
        "TEX-05 Rub colorfastness - ISO 60147:2020 or ASTM D1136-20",
        "TEX-05 Rub colorfastness - ISO 60147:2021",
        "TEX-05 My rubbing test - ISO 60147:2020",
        "TEX-05 Rub Colorfastness - ISO 60147:2020",
        "PLR-10 Shore durometer hardness (Shore-A-Härte) - ISO 60181:2021",
        "ENV-02 pH de l’eau - ISO 60591:2024",
        "ISO 60570:2025 (Color difference)",
        "ISO 60570:2025 (COR-04)",
        "ISO 60570:2025 (COR-11) modified",
        "ISO 60570:2025 (COR-11) or ISO 60152:2025",
        "COR-11 Color difference: ISO 60152:2025",
        "COR-11 Color difference: ISO 60570",
        "COR-11 Color difference: ISO 60570:2025.",
    ],
)
def test_unknown_description_revision_family_or_semantic_qualifier_is_not_stripped(raw):
    assert proof(raw) is None


def test_model_cannot_hide_source_heading_modifier_or_trailing_qualification():
    raw = "TEX-05 Rub colorfastness - ISO 60147:2020"
    assert proof(raw, "modified " + raw) is None
    assert proof(raw, raw + " (buyer approval required)") is None


def test_parent_evidence_must_own_exact_physical_page_and_contiguous_span():
    raw = "TEX-05 Rub colorfastness - ISO 60147:2020"
    assert proof(raw, doc=document(raw, "Unrelated page"), page=2, quote=raw) is None
    assert proof(raw, text=raw, quote="Another method") is None
    split = "TEX-05 Rub colorfastness\nUnrelated section\n- ISO 60147:2020"
    assert proof(raw, split, quote=raw) is None
    assert proof(raw, raw + "\n" + raw) is None


def test_family_suffix_needs_a_real_complete_literal_source_label():
    raw = "ISO 60570:2025 (COR-11)"
    text = "COR-11 Color difference: ISO 60570:2025"
    assert proof(raw, text, quote=text) is None
    # Matching the code elsewhere on the page cannot make a fabricated suffix literal.
    text = "Measure by " + raw + " against the master."
    assert proof(raw, text) is not None


def test_only_existing_whitespace_changes_are_allowed_and_original_offsets_survive():
    raw = "TEX-05 Rub colorfastness - ISO 60147:2020"
    physical = "  TEX-05  Rub colorfastness - ISO\n60147:2020\n"
    result = proof(raw, physical)
    assert result is not None
    assert result.code == "ISO\n60147:2020"
    assert physical[slice(*result.source_span)] == physical.strip()
    assert physical[slice(*result.source_code_span)] == result.code
    assert proof(raw, physical.replace("TEX-05", "TEX–05")) is None


def test_runtime_identifier_needs_exact_family_and_existing_runtime_association():
    catalog = ReferenceCatalog(
        id="example",
        name="Example",
        version=1,
        source_note="Explicit synthetic runtime row",
        tests=[
            CanonicalTest(
                id="rub",
                name="Rub colorfastness",
                description="Known family",
                method_ids=["custom_edition"],
            )
        ],
        methods=[TestMethod(id="custom_edition", identifier="XYZ 12-3:2026", name="Custom")],
    )
    raw = "TEX-05 Rub colorfastness: XYZ 12-3:2026"
    assert proof(raw) is None
    result = proof(raw, catalog=catalog)
    assert result is not None and result.code == "XYZ 12-3:2026"
    assert result.bank_record_id is None
    assert proof(raw.replace(":2026", ":2025"), catalog=catalog) is None
    assert proof("XYZ 12-3:2026 (TEX-05)", catalog=catalog) is None


def test_family_suffix_cannot_extract_an_iso_fragment_from_an_adoption_or_identifier():
    raw = "ISO 60570:2025 (COR-11)"
    assert proof(raw, "DIN EN " + raw) is None
    assert proof(raw, "BS EN " + raw) is None
    assert proof(raw, "X" + raw) is None
    assert proof(raw, "7" + raw) is None

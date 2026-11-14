"""Whole physical cells establish layout ownership without semantic guessing."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from scione.ingestion.pdf_text import PyMuPDFTextIngestor
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.specifications.table_cell_grounding import ground_table_cell_citations
from scione.structured.models import AtomicClaim


def _requirement(label, raw, page=1):
    return CustomerRequirement(
        id="req_probe", original_test_name=label,
        evidence=[RequirementEvidence(page=page, quote="Retained original row citation")],
        atomic_claims=[AtomicClaim(
            id="req_probe_1", parameter_id="temperature", label="Temperature",
            value="60", operator="=", unit="°C", stage="measurement",
            raw_text=raw, source_pages=[page], source_context="Retained model context",
        )],
    )


def _table(tmp_path, rows):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=500)
        for y in range(40, 41 + 50 * len(rows), 50):
            page.draw_line((30, y), (570, y))
        for x in (30, 210, 390, 570):
            page.draw_line((x, 40), (x, 40 + 50 * len(rows)))
        for index, row in enumerate(rows):
            for column, text in enumerate(row):
                page.insert_textbox(
                    pymupdf.Rect(35 + 180 * column, 45 + index * 50,
                                 205 + 180 * column, 85 + index * 50),
                    text, fontsize=10,
                )
        path = tmp_path / "table.pdf"
        path.write_bytes(pdf.tobytes())
    return PyMuPDFTextIngestor().ingest(path), path.read_bytes()


@pytest.mark.parametrize("label,raw,page", [
    ("Pressure water jet adhesion", "ISO 16925, X-scribe, 60 °C water, 10 cm, 60 s", 6),
    ("Xenon-arc weathering", "SAE J2527, 0.55 W/m² at 340 nm, 3000 kJ/m²", 8),
])
def test_original_client_pdf_supports_exact_wrapped_cells_without_changing_atoms(label, raw, page):
    path = Path(__file__).resolve().parents[2] / "docs/samples/MS-PNT-410_demo_spec.pdf"
    if not path.exists():
        pytest.skip("Optional client PDF is not included in this checkout.")
    document = PyMuPDFTextIngestor().ingest(path)
    requirement = _requirement(label, raw, page)
    before = requirement.model_dump(mode="json")
    detached, actions = ground_table_cell_citations([requirement], document, path.read_bytes())
    assert requirement.model_dump(mode="json") == before
    assert detached[0].atomic_claims == requirement.atomic_claims
    assert len(detached[0].evidence) == 2 and len(actions) == 1
    action = actions[0]
    assert action["source_page"] == page and action["original_atoms_preserved"] is True
    assert action["establishes_lab_support"] is False
    assert action["source_document_sha256"] == document.sha256
    assert action["source_quote_sha256"] == hashlib.sha256(
        detached[0].evidence[-1].quote.encode()
    ).hexdigest()
    assert len(action["source_cell_bbox"]) == len(action["source_row_bbox"]) == 4
    repeated, audits = ground_table_cell_citations(detached, document, path.read_bytes())
    assert repeated[0].evidence == detached[0].evidence
    assert audits[0]["evidence_added"] is False


@pytest.mark.parametrize("label,raw", [
    ("test a", "At 60 C for 2 h"),  # Case remains meaningful.
    ("Different test", "At 60 C for 2 h"),
    ("Test A", "At 61 C for 2 h"),
    ("Test A", "At 60 C"),  # A subcell is not a whole-cell quotation.
    ("Test A", "At 60 C for 2 h At 40 C for 1 h"),
])
def test_wrong_label_changed_number_partial_cell_or_cross_row_join_cannot_prove(
    tmp_path, label, raw,
):
    document, data = _table(tmp_path, [
        ("Test A", "At 60 C for 2 h", "Report A"),
        ("Test B", "At 40 C for 1 h", "Report B"),
    ])
    requirement = _requirement(label, raw)
    detached, actions = ground_table_cell_citations([requirement], document, data)
    assert not actions and detached[0] == requirement and detached[0] is not requirement


def test_duplicate_rows_and_duplicate_cells_remain_ambiguous(tmp_path):
    document, data = _table(tmp_path, [
        ("Test A", "At 60 C", "Report A"), ("Test A", "At 60 C", "Report A"),
    ])
    assert ground_table_cell_citations([_requirement("Test A", "At 60 C")], document, data)[1] == []
    document, data = _table(tmp_path, [("Test A", "At 60 C", "At 60 C")])
    assert ground_table_cell_citations([_requirement("Test A", "At 60 C")], document, data)[1] == []


def test_sha_mismatch_is_rejected_before_any_layout_inspection(tmp_path, monkeypatch):
    document, data = _table(tmp_path, [("Test A", "At 60 C", "Report A")])
    monkeypatch.setattr(pymupdf, "open", lambda *a, **kw: pytest.fail("No PDF inspection allowed"))
    with pytest.raises(ValueError, match="do not match"):
        ground_table_cell_citations([_requirement("Test A", "At 60 C")], document, data + b"x")


def test_same_page_is_parsed_once_and_only_declared_source_pages_are_eligible(
    tmp_path, monkeypatch,
):
    import scione.specifications.table_cell_grounding as module

    document, data = _table(tmp_path, [("Test A", "At 60 C", "Report A")])
    original = module._page_rows
    calls = []

    def counted(page):
        calls.append(page.number)
        return original(page)

    monkeypatch.setattr(module, "_page_rows", counted)
    requirement = _requirement("Test A", "At 60 C")
    requirement.atomic_claims.append(requirement.atomic_claims[0].model_copy(
        update={"id": "req_probe_2"}
    ))
    _, actions = ground_table_cell_citations([requirement], document, data)
    assert calls == [0] and actions[0]["claim_ids"] == ["req_probe_1", "req_probe_2"]
    requirement.atomic_claims[0].source_pages = [2]
    requirement.atomic_claims = requirement.atomic_claims[:1]
    assert ground_table_cell_citations([requirement], document, data)[1] == []


def test_vertical_merge_is_not_a_complete_physical_row(tmp_path, monkeypatch):
    document, data = _table(tmp_path, [("Test A", "At 60 C", "Report A")])
    fake = SimpleNamespace(tables=[SimpleNamespace(
        col_count=2,
        rows=[SimpleNamespace(cells=[(30, 40, 210, 140), (210, 40, 390, 90)])],
    )])
    monkeypatch.setattr(pymupdf.Page, "find_tables", lambda self: fake)
    detached, actions = ground_table_cell_citations(
        [_requirement("Test A", "At 60 C")], document, data,
    )
    assert not actions and len(detached[0].evidence) == 1

"""Section-local comparison projections require literal physical source proof."""

import hashlib

import pymupdf
import pytest

from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.models import CustomerRequirement, RequirementEvidence, RequirementValue
from scione.specifications.section_scopes import apply_section_scopes, source_section_intros
from scione.structured.models import AtomicClaim

CLAUSE = "Use flat 20 x 30 mm panels."


def fixture(*, clause=CLAUSE, descendant=False, duplicate_row=False, duplicate_clause=False):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=700)
        page.insert_text((30, 50), "2.1 Surface checks")
        page.insert_text((30, 80), clause, fontsize=10)
        heading = "2.1.1 Mechanical checks" if descendant else "2.2 Mechanical checks"
        page.insert_text((30, 240), heading)
        if duplicate_clause:
            page.insert_text((30, 260), clause, fontsize=10)

        def table(y, name, method, criterion, duplicate=False):
            xs = (30, 230, 390, 570)
            count = 3 if duplicate else 2
            ys = [y + 35 * n for n in range(count + 1)]
            for x in xs:
                page.draw_line((x, ys[0]), (x, ys[-1]))
            for line in ys:
                page.draw_line((xs[0], line), (xs[-1], line))
            rows = [("Test", "Method", "Criterion"), (name, method, criterion)]
            if duplicate:
                rows.append(rows[1])
            for index, cells in enumerate(rows):
                for column, text in enumerate(cells):
                    page.insert_text((xs[column] + 5, y + 23 + 35 * index), text, fontsize=10)

        table(100, "Surface test", "Visual", "No defect")
        table(280, "Force test", "20 N", ">= 5", duplicate_row)
        page.insert_text((30, 460), "3. End of test requirements")
        text = page.get_text("text", sort=True)
        data = pdf.tobytes()
    source = IngestedDocument(
        document_id="scope-test", file_name="scope.pdf", sha256=hashlib.sha256(data).hexdigest(),
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(page_number=1, text=text, character_count=len(text),
                            non_whitespace_character_count=len("".join(text.split())))],
    )
    row = CustomerRequirement(
        id="req_001", original_test_name="Force test",
        conditions=[
            RequirementValue(name="Panel", raw_value=clause),
            RequirementValue(name="Force", raw_value="20 N"),
        ],
        evidence=[RequirementEvidence(page=1, quote="Force test 20 N >= 5"),
                  RequirementEvidence(page=1, quote=clause)],
        atomic_claims=[AtomicClaim(
            id="inherited_panel", parameter_id="panel_shape", label="Panel", kind="condition",
            stage="measurement", operator="text", text_value=clause, raw_text=clause,
            source_pages=[1], normalization_state="parsed",
        )],
    )
    return source, data, row


def test_foreign_section_exact_inheritance_is_excluded_only_from_detached_view():
    source, pdf, row = fixture()
    before, document_before = row.model_dump_json(), source.model_dump_json()
    views, actions = apply_section_scopes([row], source, pdf)
    assert row.model_dump_json() == before and source.model_dump_json() == document_before
    assert views[0] is not row
    assert views[0].atomic_claims == []
    assert [field.raw_value for field in views[0].conditions] == ["20 N"]
    assert views[0].evidence == row.evidence
    assert len(actions) == 1
    proof = actions[0]
    assert proof["source_section"] == "2.1" and proof["body_section"] == "2.2"
    assert proof["removed_atoms"][0]["id"] == "inherited_panel"
    assert proof["removed_fields"][0]["index"] == 0
    span = proof["source_span"]
    assert source.pages[0].text[span["start"]:span["end"]] == CLAUSE
    assert proof["body_row_proof"]["first_cell_tokens"] == ["force", "test"]
    assert proof["establishes_lab_support"] is False


@pytest.mark.parametrize("case", ["same_section", "descendant", "unknown_owner", "duplicate_row",
                                  "duplicate_clause", "wrong_pdf", "paraphrase"])
def test_unproved_or_valid_scope_is_never_excluded(case):
    source, pdf, row = fixture(
        descendant=case == "descendant", duplicate_row=case == "duplicate_row",
        duplicate_clause=case == "duplicate_clause",
    )
    if case == "same_section":
        row.original_test_name = "Surface test"
        row.evidence[0].quote = "Surface test Visual No defect"
    elif case == "unknown_owner":
        row.original_test_name = "Unknown force operation"
    elif case == "wrong_pdf":
        source.sha256 = "a" * 64
    elif case == "paraphrase":
        row.atomic_claims[0].raw_text = "Flat panels have dimensions 20 x 30 mm."
        row.conditions[0].raw_value = "Flat panels have dimensions 20 x 30 mm."
    original = row.model_dump_json()
    views, actions = apply_section_scopes([row], source, pdf)
    assert views[0].model_dump_json() == original
    assert actions == []


@pytest.mark.parametrize("clause", [
    "All tests use flat 20 x 30 mm panels.",
    "Use flat 20 x 30 mm panels unless otherwise agreed.",
    "Document-wide use of flat 20 x 30 mm panels is required.",
    "Use flat 20 x 30 mm panels except for immersion.",
    "Use flat panels as specified in Section 2.2.",
])
def test_global_exception_or_outward_reference_clause_has_no_local_scope_recovery(clause):
    source, pdf, row = fixture(clause=clause)
    assert source_section_intros(source, pdf) == ()
    views, actions = apply_section_scopes([row], source, pdf)
    assert actions == [] and views[0].model_dump_json() == row.model_dump_json()


@pytest.mark.parametrize("annotation,removed", [
    ("Surface checks tests, Section 2.1", True),
    ("Section 2.1", True),
    ("Surface checks measurements, Section 2.1", True),
    ("Surface checks tests, Section 2.2", False),
    ("Surface checks tests, Section 2.1 at 25 C", False),
])
def test_only_closed_caption_of_proven_section_can_match_a_legacy_condition(annotation, removed):
    source, pdf, row = fixture()
    row.conditions[0].raw_value = CLAUSE.rstrip(".") + f" ({annotation})"
    views, actions = apply_section_scopes([row], source, pdf)
    assert bool(actions[0]["removed_fields"]) is removed
    assert len(views[0].conditions) == (1 if removed else 2)
    # The separately exact atom can still be excluded; an enriched condition
    # containing other scientific information remains available for review.
    assert views[0].atomic_claims == []


def test_explicit_body_reference_to_the_intro_section_preserves_possible_transfer():
    source, pdf, row = fixture()
    row.evidence[0].quote += "; use Section 2.1 panels"
    views, actions = apply_section_scopes([row], source, pdf)
    assert actions == [] and views[0].model_dump_json() == row.model_dump_json()


def test_reviewed_inheritance_is_preserved_with_explicit_conflict_instead_of_removal():
    source, pdf, row = fixture()
    row.atomic_claims[0].normalization_state = "reviewed"
    before = row.model_dump_json()
    views, actions = apply_section_scopes([row], source, pdf)
    assert views[0].model_dump_json() == before
    assert actions[0]["disposition"] == "retained_reviewed_scope_conflict"
    assert actions[0]["requires_review"] is True
    assert actions[0]["reviewed_claim_ids"] == ["inherited_panel"]
    assert actions[0]["removed_fields"] == actions[0]["removed_atoms"] == []


@pytest.mark.parametrize("case", ["no_atom", "unresolved_atom"])
def test_condition_without_parsed_model_inheritance_is_not_removed(case):
    source, pdf, row = fixture()
    if case == "no_atom":
        row.atomic_claims = []
    else:
        row.atomic_claims[0].normalization_state = "unresolved"
    views, actions = apply_section_scopes([row], source, pdf)
    assert actions == [] and views[0].model_dump_json() == row.model_dump_json()

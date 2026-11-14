"""Only physical, correctly owned matching thresholds reconcile this summary."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.review import assess_requirements
from scione.specifications.summary_reconciliation import source_summary_reconciliation

TOP = (
    "Record total dry film build and the separate topcoat thickness; "
    "the latter must be between 42 and 55 µm on every panel."
)
ISO_METHOD = "For this grid-and-pull assessment the receiving site should use ISO 60038:2021."
ISO = "If the ISO procedure is used, only class 0 or 1 is acceptable"
PHASE = (
    "Repeat the check both before and after five cycles between -20 °C for two hours "
    "and 70 °C for two hours, with a 30-minute room-temperature recovery between transfers."
)
GLOSS_INITIAL = "The arithmetic mean must be 85-95 GU as delivered"
GLOSS_RETENTION = "after cycling, each location must retain at least 90% of its own original value"
GLOSS = (
    "Record the 60° specular-gloss reading using ASTM D1627-16 at five marked locations. "
    f"{GLOSS_INITIAL}; {GLOSS_RETENTION}."
)
COLOUR_ATOM = (
    "the overall colour difference, calculated from measured L*, a* and b*, may not exceed 1.0"
)
COLOUR = (
    "Separately, compare the same panel with an untouched master under D65 lighting: "
    f"{COLOUR_ATOM}."
)
TOP_ROW = "Topcoat film thickness 42-55 µm on each panel"
ISO_ROW = "Scored-grid retention ISO class 0-1 before and after cycles"
GLOSS_ROW = "60° reflected gloss 85-95 GU initial; >=90% site retention"
COLOUR_ROW = "Master-panel colour drift ΔE*ab <=1.0"
TABLE = (
    "Acceptance summary for the receiving report\nCustomer checkpoint Release threshold\n"
    f"{TOP_ROW}\n{ISO_ROW}\n{GLOSS_ROW}\n{COLOUR_ROW}\n"
)
COMMENT = (
    "The acceptance summary table repeats body limits (topcoat 42-55 µm, ISO class 0-1, "
    "85-95 GU / >=90% retention, ΔE*ab <=1.0) without adding new requirements; "
    "treated as consistent repeats."
)
BANK_ISSUE = "req_top: ISO 60152:2025 is cited only in the test-item bank alignment."
REVISION_ISSUE = "Initial gloss revision uncertainty remains to be checked."


def _atom(identifier, parameter, raw, *, stage="general", operator="range", **values):
    return {
        "id": identifier,
        "parameter_id": parameter,
        "label": {
            "topcoat_thickness": "Topcoat thickness",
            "adhesion_class": "Adhesion class",
            "gloss": "Gloss",
            "gloss_retention": "Gloss retention",
            "color_difference": "Color difference",
        }[parameter],
        "kind": "criterion",
        "stage": stage,
        "operator": operator,
        "raw_text": raw,
        "source_pages": [1 if parameter == "topcoat_thickness" else 2],
        **values,
    }


def fixture():
    pages = (
        TOP,
        f"{ISO_METHOD}\n{PHASE}\n{ISO}; future ASTM acceptance is unspecified.\n"
        f"{GLOSS}\n{COLOUR}\n{TABLE}\nFor release review, retain observations.",
    )
    document = IngestedDocument(
        document_id="summary_fixture",
        file_name="summary.pdf",
        sha256=sha256("".join(pages).encode()).hexdigest(),
        page_count=2,
        pages=[
            DocumentPage(
                page_number=i,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
            for i, text in enumerate(pages, 1)
        ],
        text_reading_order=TextReadingOrder.GEOMETRIC,
        extraction_status=TextExtractionStatus.READY,
    )
    rows = [
        (
            "req_top",
            "Topcoat thickness",
            "ISO 60152:2025",
            [TOP, TOP_ROW],
            [
                _atom(
                    "top",
                    "topcoat_thickness",
                    TOP,
                    minimum=42,
                    maximum=55,
                    unit="µm",
                    source_context="separate topcoat thickness; on every panel",
                ),
            ],
        ),
        (
            "req_iso",
            "Scored-grid retention",
            "ISO 60038:2021",
            [ISO_METHOD, ISO, ISO_ROW, PHASE],
            [
                _atom(
                    "iso_initial",
                    "adhesion_class",
                    ISO,
                    stage="initial",
                    operator="one_of",
                    values=[0, 1],
                    source_context="ISO procedure; before and after cycles",
                ),
                _atom(
                    "iso_after",
                    "adhesion_class",
                    ISO_ROW,
                    stage="after_cycle",
                    operator="one_of",
                    values=[0, 1],
                    source_context="ISO procedure; after cycles",
                ),
            ],
        ),
        (
            "req_gloss",
            "Specular gloss",
            "ASTM D1627-16",
            [GLOSS, GLOSS_ROW],
            [
                _atom(
                    "gloss",
                    "gloss",
                    GLOSS_INITIAL,
                    stage="initial",
                    minimum=85,
                    maximum=95,
                    unit="GU",
                    source_context="arithmetic mean; as delivered",
                ),
                _atom(
                    "retention",
                    "gloss_retention",
                    GLOSS_RETENTION,
                    stage="after_cycle",
                    operator=">=",
                    value=90,
                    unit="%",
                    source_context="each location; of its own original value",
                ),
            ],
        ),
        (
            "req_colour",
            "Colour difference",
            "ISO 60570:2025",
            [COLOUR, COLOUR_ROW],
            [
                _atom(
                    "colour",
                    "color_difference",
                    COLOUR_ATOM,
                    stage="measurement",
                    operator="<=",
                    value=1,
                    text_value="ΔE*ab",
                    source_context=(
                        "calculated from measured L*, a* and b*; versus untouched master"
                    ),
                ),
            ],
        ),
    ]
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": "summary_run",
            "started_at": datetime.now(UTC),
            "document_id": document.document_id,
            "prompt_version": "frozen_fixture",
            "schema_version": "0.1",
            "provider": "static",
            "model": "fixture",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "UNCHANGED provider raw",
            "extraction": {
                "schema_version": "0.1",
                "document": {},
                "review_issues": [COMMENT, BANK_ISSUE, REVISION_ISSUE],
                "requirements": [
                    {
                        "id": identifier,
                        "original_test_name": name,
                        "requested_methods": [{"code": method, "role": "primary"}],
                        "acceptance_criteria": [{"name": name, "raw_value": evidence[0]}],
                        "evidence": [
                            {"page": 1 if text == TOP else 2, "quote": text} for text in evidence
                        ],
                        "atomic_claims": atoms,
                    }
                    for identifier, name, method, evidence, atoms in rows
                ],
            },
        }
    )
    return run, document


def _replace_source(document, old, new):
    for page in document.pages:
        if old in page.text:
            page.text = page.text.replace(old, new)
            page.character_count = len(page.text)
            page.non_whitespace_character_count = len("".join(page.text.split()))


def test_closed_physical_body_summary_native_proof_is_audited_without_using_model_as_evidence():
    run, document = fixture()
    original = deepcopy(run.model_dump(mode="json"))
    projection = source_summary_reconciliation(run, document)
    assert projection.nonblocking_issues == {COMMENT}
    assert len(projection.proofs) == 1
    proof = projection.proofs[0]
    assert proof["model_comment_used_as_evidence"] is False
    assert len(proof["physical_body"]) == 6
    assert len(proof["native_criterion_claims"]) == 5
    assert proof["physical_named_scale_proof"]["canonical"] == "ΔE*ab"
    for item in [proof["summary"], *proof["physical_body"]]:
        page = document.pages[item["page"] - 1]
        assert page.text[item["source_start"] : item["source_end"]] == item["quote"]
    assert run.model_dump(mode="json") == original
    assessments = assess_requirements(run, document)
    assert BANK_ISSUE in assessments["req_top"].reasons
    assert REVISION_ISSUE in assessments["req_gloss"].reasons
    assert all(COMMENT not in result.reasons for result in assessments.values())


def test_different_source_owned_limits_reconcile_without_any_expected_value_constants():
    run, document = fixture()
    replacements = {"42": "22", "55": "35", "85": "70", "95": "88", "90": "93"}

    def change(value):
        if isinstance(value, str):
            for old, new in replacements.items():
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [change(item) for item in value]
        if isinstance(value, dict):
            return {key: change(item) for key, item in value.items()}
        return value

    run = CustomerExtractionRun.model_validate(change(run.model_dump(mode="json")))
    for old, new in replacements.items():
        _replace_source(document, old, new)
    projection = source_summary_reconciliation(run, document)
    assert len(projection.proofs) == 1
    assert projection.nonblocking_issues == {run.extraction.review_issues[0]}


@pytest.mark.parametrize(
    "old,new",
    [
        (TOP_ROW, TOP_ROW.replace("55", "56")),
        (TOP_ROW, TOP_ROW.replace("µm", "mm")),
        (TOP_ROW, TOP_ROW.replace("on each", "on an average")),
        (ISO_ROW, ISO_ROW.replace("0-1", "0-2")),
        (ISO_ROW, ISO_ROW.replace("before and after", "after")),
        (GLOSS_ROW, GLOSS_ROW.replace(">=90", ">90")),
        (GLOSS_ROW, GLOSS_ROW.replace("initial", "after cycling")),
        (GLOSS_ROW, GLOSS_ROW.replace("site retention", "batch mean retention")),
        (COLOUR_ROW, COLOUR_ROW.replace("ΔE*ab", "ΔE00")),
        (COLOUR_ROW, COLOUR_ROW.replace("<=1.0", ">=1.0")),
        (GLOSS_INITIAL, GLOSS_INITIAL.replace("85-95", "82-92")),
        (GLOSS_RETENTION, GLOSS_RETENTION.replace("each location", "batch mean")),
        (PHASE, PHASE.replace("both before and after", "only after")),
        (TOP, "Do not " + TOP),
        (TABLE, TABLE + "Extra requirement 30 °C.\n"),
    ],
)
def test_any_physical_value_unit_operator_scope_phase_or_extra_row_conflict_stays_pending(old, new):
    run, document = fixture()
    _replace_source(document, old, new)
    assert source_summary_reconciliation(run, document).proofs == ()
    assert any(COMMENT in item.reasons for item in assess_requirements(run, document).values())


@pytest.mark.parametrize(
    "defect",
    [
        "value",
        "unit",
        "operator",
        "role",
        "phase",
        "owner",
        "ghost_evidence",
        "wrong_label",
        "extra_contradiction",
        "foreign_context",
        "colour_operator",
        "colour_phase",
        "colour_owner",
        "iso_method",
        "missing_body_native",
        "model_comment_wrong_number",
        "model_comment_action",
    ],
)
def test_native_scientific_owner_or_model_comment_mismatch_cannot_reconcile(defect):
    run, document = fixture()
    top = run.extraction.requirements[0].atomic_claims[0]
    colour = run.extraction.requirements[-1].atomic_claims[0]
    if defect == "value":
        top.maximum = 56
    elif defect == "unit":
        top.unit = "mm"
    elif defect == "operator":
        top.operator = "one_of"
    elif defect == "role":
        top.kind = "condition"
    elif defect == "phase":
        top.stage = "after_cycle"
    elif defect == "owner":
        run.extraction.requirements[1].atomic_claims.append(
            top.model_copy(update={"id": "wrong_owner"})
        )
    elif defect == "ghost_evidence":
        run.extraction.requirements[0].evidence[0].quote = "Ghost proof."
    elif defect == "wrong_label":
        top.label = "Water temperature"
    elif defect == "extra_contradiction":
        run.extraction.requirements[0].atomic_claims.append(
            top.model_copy(update={"id": "extra", "maximum": 60})
        )
    elif defect == "foreign_context":
        top.source_context = "after 30 days at 80 °C"
    elif defect == "colour_operator":
        colour.operator = ">="
    elif defect == "colour_phase":
        colour.stage = "after_cycle"
    elif defect == "colour_owner":
        run.extraction.requirements[0].atomic_claims.append(
            colour.model_copy(update={"id": "other_colour"})
        )
    elif defect == "iso_method":
        run.extraction.requirements[1].requested_methods[0].code = "ISO 99999"
    elif defect == "missing_body_native":
        run.extraction.requirements[2].atomic_claims = []
    elif defect == "model_comment_wrong_number":
        run.extraction.review_issues[0] = COMMENT.replace("42-55", "42-56")
    else:
        run.extraction.review_issues[0] = COMMENT + " Please confirm the uncertainty."
    assert source_summary_reconciliation(run, document).proofs == ()

"""A consistent revision repeats a fact; it does not waive source uncertainty."""

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.review import assess_requirements
from scione.specifications.revision_notes import source_revision_notes

NOTE = "SYNTHETIC DEMO REVISION B: initial gloss target is 85-95 GU. Other requirements unchanged."
BODY = "The arithmetic mean must be 85-95 GU as delivered."
PARENT = f"Read specular gloss by ASTM D1627-16. {BODY}"
ERROR = (
    "Document notice needs review: Provenance notice contains other content "
    "that still requires review. " + NOTE
)
CONSISTENT = (
    "Revision B initial gloss target matches the body (85-95 GU mean); both are consistent."
)
RESTATEMENT = (
    "The page-1 note 'SYNTHETIC DEMO REVISION B: initial gloss target is 85-95 GU' "
    "restates the gloss initial limit; no conflicting printed revision of the gloss limit "
    "was found."
)


def source(first=NOTE, second=PARENT):
    return IngestedDocument(
        document_id="revision_source",
        file_name="revision.pdf",
        sha256=sha256((first + second).encode()).hexdigest(),
        page_count=2,
        pages=[
            DocumentPage(
                page_number=i,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
            for i, text in enumerate((first, second), 1)
        ],
        text_reading_order=TextReadingOrder.GEOMETRIC,
        extraction_status=TextExtractionStatus.READY,
    )


def run():
    return CustomerExtractionRun.model_validate(
        {
            "run_id": "revision_run",
            "started_at": datetime.now(UTC),
            "document_id": "revision_source",
            "schema_version": "0.1",
            "provider": "static",
            "model": "fixture",
            "prompt_version": "fixture",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "ORIGINAL RAW",
            "extraction": {
                "schema_version": "0.1",
                "document": {"identifier": "CSP / Issue B", "revision": "Issue B"},
                "review_issues": [ERROR, CONSISTENT],
                "requirements": [
                    {
                        "id": "req_gloss",
                        "original_test_name": "Specular gloss",
                        "requested_methods": [{"code": "ASTM D1627-16"}],
                        "acceptance_criteria": [{"name": "Initial gloss", "raw_value": BODY}],
                        "evidence": [{"page": 2, "quote": PARENT}],
                        "atomic_claims": [
                            {
                                "id": "native_gloss",
                                "parameter_id": "gloss",
                                "label": "Gloss",
                                "kind": "criterion",
                                "stage": "initial",
                                "operator": "range",
                                "minimum": 85,
                                "maximum": 95,
                                "unit": "GU",
                                "raw_text": BODY,
                                "source_pages": [2],
                            }
                        ],
                    }
                ],
            },
            "notice_audits": [
                {
                    "call_index": 1,
                    "notice_index": 1,
                    "proposal": {"kind": "synthetic_provenance", "page": 1, "quote": NOTE},
                    "status": "needs_review",
                    "issues": [
                        "Provenance notice contains other content that still requires review."
                    ],
                }
            ],
        }
    )


def test_consistent_note_is_one_information_notice_without_mutating_raw_or_provenance_audit():
    saved = run()
    before = deepcopy(saved.model_dump(mode="json"))
    projection = source_revision_notes(saved, source())
    assert projection.nonblocking_issues == {ERROR, CONSISTENT}
    assert list(projection.notices) == ["req_gloss"]
    assert len(projection.notices["req_gloss"]) == 1
    assert projection.proofs[0]["independent_body_claim_ids"] == ["native_gloss"]
    assessment = assess_requirements(saved, source())["req_gloss"]
    assert assessment.state == "ready"
    assert len(assessment.notices) == 1
    assert saved.model_dump(mode="json") == before
    assert saved.extraction.document_notices == []  # Scientific text did not become provenance.


def test_closed_restatement_uses_physical_annotation_and_body_without_inventing_provenance_audit():
    saved = run()
    saved.notice_audits = []
    saved.extraction.review_issues = [RESTATEMENT]
    before = deepcopy(saved.model_dump(mode="json"))
    projection = source_revision_notes(saved, source())
    assert projection.nonblocking_issues == {RESTATEMENT}
    assert len(projection.proofs) == 1
    assert projection.proofs[0]["selector_kind"] == "physical_annotation"
    assert projection.proofs[0]["original_notice_status"] is None
    assert projection.proofs[0]["model_comment_used_as_evidence"] is False
    assert assess_requirements(saved, source())["req_gloss"].state == "ready"
    assert saved.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "defect",
    [
        "comment_value",
        "comment_unit",
        "comment_page",
        "comment_revision",
        "comment_wording",
        "body_value",
        "body_unit",
        "body_phase",
        "native_value",
        "native_unit",
        "native_role",
        "native_phase",
        "native_owner",
        "metadata_revision",
        "unparsed_metadata_revision",
        "native_context",
        "extra_note_content",
    ],
)
def test_restatement_with_unproved_value_unit_owner_revision_phase_or_wording_stays_pending(defect):
    saved = run()
    saved.notice_audits = []
    saved.extraction.review_issues = [RESTATEMENT]
    doc = source()
    atom = saved.extraction.requirements[0].atomic_claims[0]
    if defect == "comment_value":
        saved.extraction.review_issues[0] = RESTATEMENT.replace("85-95", "82-92")
    elif defect == "comment_unit":
        saved.extraction.review_issues[0] = RESTATEMENT.replace(" GU", " µm")
    elif defect == "comment_page":
        saved.extraction.review_issues[0] = RESTATEMENT.replace("page-1", "page-2")
    elif defect == "comment_revision":
        saved.extraction.review_issues[0] = RESTATEMENT.replace("REVISION B", "REVISION A")
    elif defect == "comment_wording":
        saved.extraction.review_issues[0] += " Please check the conflict."
    elif defect == "body_value":
        doc = source(second=PARENT.replace("85-95", "82-92"))
    elif defect == "body_unit":
        doc = source(second=PARENT.replace("GU", "µm"))
    elif defect == "body_phase":
        doc = source(second=PARENT.replace("as delivered", "after cycling"))
    elif defect == "native_value":
        atom.minimum = 82
    elif defect == "native_unit":
        atom.unit = "µm"
    elif defect == "native_role":
        atom.kind = "condition"
    elif defect == "native_phase":
        atom.stage = "after_cycle"
    elif defect == "native_owner":
        saved.extraction.requirements.append(
            saved.extraction.requirements[0].model_copy(update={"id": "other"})
        )
    elif defect == "metadata_revision":
        saved.extraction.document.revision = "A"
    elif defect == "unparsed_metadata_revision":
        saved.extraction.document.revision = "unknown version pending confirmation"
    elif defect == "native_context":
        atom.source_context = "after 30 days"
    else:
        doc = source(first=NOTE + " Except initial gloss phase is not as delivered.")
    assert source_revision_notes(saved, doc).proofs == ()
    assert saved.extraction.review_issues[0] in assess_requirements(saved, doc)["req_gloss"].reasons


@pytest.mark.parametrize(
    "first,second",
    [
        (NOTE, PARENT.replace("85-95", "82-92")),
        (NOTE, PARENT + " Instrument range 0-200 GU."),
        (NOTE + "\nRevision A is also printed.", PARENT),
        (NOTE + " Extra test condition 30 °C.", PARENT),
        ("No source note.", PARENT),
        (NOTE, "Read gloss without a printed initial limit."),
    ],
)
def test_conflicting_mixed_unlocated_or_unproved_source_note_remains_actionable(first, second):
    projection = source_revision_notes(run(), source(first, second))
    # A mixed source line must be rejected if it changes the claimed closed quote.
    if first.startswith(NOTE + " Extra"):
        saved = run()
        saved.notice_audits[0].proposal.quote = first
        projection = source_revision_notes(saved, source(first, second))
    assert projection.nonblocking_issues == set()
    assert projection.proofs == ()


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_meta_revision",
        "wrong_bare_meta_revision",
        "wrong_body_value",
        "wrong_body_role",
        "post_cycle_body",
        "missing_typed_audit",
        "competing_owner",
    ],
)
def test_unproved_owner_revision_or_scientific_body_cannot_clear_the_warning(mutation):
    saved = run()
    row = saved.extraction.requirements[0]
    if mutation == "wrong_meta_revision":
        saved.extraction.document.revision = "Issue A"
    elif mutation == "wrong_bare_meta_revision":
        saved.extraction.document.revision = "A"
    elif mutation == "wrong_body_value":
        row.atomic_claims[0] = row.atomic_claims[0].model_copy(update={"minimum": 80})
    elif mutation == "wrong_body_role":
        row.atomic_claims[0].kind = "instruction"
    elif mutation == "post_cycle_body":
        row.atomic_claims[0].stage = "after_cycle"
    elif mutation == "missing_typed_audit":
        saved.notice_audits = []
    else:
        saved.extraction.requirements.append(row.model_copy(update={"id": "req_other"}))
    assert source_revision_notes(saved, source()).nonblocking_issues == set()


@pytest.mark.parametrize(
    "issue",
    [
        "Revision B gloss is consistent but temperature is uncertain.",
        "Revision B gloss has a conflict and needs confirmation.",
        "Revision B gloss is consistent except after cycling.",
        "Revision B initial gloss target matches the body 82-92 GU; both are consistent.",
        'Revision B gloss agrees with a "different source".',
        "Revision B gloss is not consistent with the body.",
        "Revision B gloss is consistent but official approval is missing.",
    ],
)
def test_a_consistent_revision_does_not_erase_other_model_or_scientific_concerns(issue):
    saved = run()
    saved.extraction.review_issues.append(issue)
    projection = source_revision_notes(saved, source())
    assert ERROR in projection.nonblocking_issues
    assert issue not in projection.nonblocking_issues
    assert issue in assess_requirements(saved, source())["req_gloss"].reasons

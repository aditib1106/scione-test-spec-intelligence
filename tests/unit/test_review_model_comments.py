"""Generic model handling notes are not scientific evidence or lab questions."""

from copy import deepcopy

import pytest
from test_revision_notes import run, source

from scione.specifications.review import assess_requirements
from scione.specifications.review_comments import review_model_comment_audits


@pytest.mark.parametrize(
    "comment",
    [
        "The acceptance summary table repeats body limits; treated as consistent repeats.",
        "Acceptance summary table repeats the body requirements; treated as consistent repeats.",
        "The summary table repeats the body limits.",
        "Summary table contains consistent repeats of the body requirements.",
    ],
)
def test_generic_positive_comment_is_unused_audited_commentary_without_source_or_raw_changes(
    comment,
):
    saved = run()
    saved.extraction.review_issues = [comment]
    before = deepcopy(saved.model_dump(mode="json"))
    audits = review_model_comment_audits(saved)
    assert len(audits) == 1
    assert audits[0]["kind"] == "unused_model_comment"
    assert audits[0]["original_comment"] == comment
    assert audits[0]["used_as_evidence"] is False
    assert audits[0]["row_blocking"] is False
    assert assess_requirements(saved, source())["req_gloss"].state == "ready"
    assert saved.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "comment",
    [
        "The acceptance summary table repeats body limits (85-95 GU); "
        "treated as consistent repeats.",
        "The acceptance summary table repeats body limits (85-95); treated as consistent repeats.",
        "The acceptance summary table repeats body limits GU; treated as consistent repeats.",
        "The acceptance summary table repeats temperature limits; treated as consistent repeats.",
        "The acceptance summary table repeats ISO 60152 requirements; "
        "treated as consistent repeats.",
        "The acceptance summary table repeats body limits without adding new requirements; "
        "treated as consistent repeats.",
        "The acceptance summary table does not repeat body limits; treated as consistent repeats.",
        "The acceptance summary table may repeat body limits; treated as consistent repeats.",
        "The acceptance summary table repeats body limits but conflicts with body scope.",
        "The acceptance summary table repeats body limits; please confirm.",
        "req_gloss: The acceptance summary table repeats body limits; "
        "treated as consistent repeats.",
        "The acceptance summary table repeats body limits; treated as consistent repeats. "
        "Check the method.",
        "The acceptance summary table repeats body limits; "
        "treated as consistent repeats; mismatch.",
        "The acceptance summary table repeats body limits; treated as consistent repeats².",
        "The acceptance summary table repeats body limits; treated as conſistent repeats.",
    ],
)
def test_specific_scientific_uncertain_conflicting_scoped_or_actionable_issue_remains_blocking(
    comment,
):
    saved = run()
    saved.extraction.review_issues = [comment]
    assert review_model_comment_audits(saved) == []
    assert comment in assess_requirements(saved, source())["req_gloss"].reasons


def test_comment_routing_does_not_waive_an_independent_native_scientific_error():
    saved = run()
    saved.extraction.review_issues = ["The summary table repeats the body limits."]
    saved.extraction.requirements[0].atomic_claims[0].unit = "°C"
    assessment = assess_requirements(saved, source())["req_gloss"]
    assert assessment.state == "needs_attention"
    assert any("against the PDF" in reason for reason in assessment.reasons)

"""Source association is evidence; a model's concern about its location is not."""

from types import SimpleNamespace

import pytest
from test_conditional_source_notes import OPTIONAL, PRIMARY, fixture

from scione.catalogs.bank import identifier_key
from scione.specifications.models import RequirementEvidence
from scione.specifications.review import assess_requirements
from scione.specifications.review_comments import method_reference_comment_audits

FOOTER = f"TST-01 Surface retention: {PRIMARY} on steel panels."
COMMENT = (
    f"req_001: {PRIMARY} is cited only in the test-item bank alignment note "
    "(TST-01 Surface retention on steel panels); the body of Sections 1 and 2 "
    "does not itself name a method."
)


def footer_fixture(monkeypatch):
    run, document = fixture(COMMENT)
    document.pages[0].text += "\n" + FOOTER
    run.extraction.requirements[0].evidence.append(RequirementEvidence(page=1, quote=FOOTER))
    entry = SimpleNamespace(identifier=PRIMARY, family_id="TST-01", family_name="Surface retention")
    bank = SimpleNamespace(lookup_identifier=lambda code: entry if identifier_key(code)
                           == identifier_key(PRIMARY) else None)
    monkeypatch.setattr("scione.catalogs.bank.load_synthetic_method_bank", lambda: bank)
    return run, document


def test_exact_owned_bank_reference_is_a_visible_notice_not_a_source_error(monkeypatch):
    run, document = footer_fixture(monkeypatch)
    original = run.model_dump_json()
    audits = method_reference_comment_audits(run, document)
    assert len(audits) == 1
    assert audits[0]["used_as_evidence"] is False
    assert audits[0]["source_evidence"][0]["quote"] == FOOTER
    result = assess_requirements(run, document)["req_001"]
    assert result.state == "ready"
    assert result.notices == (COMMENT,)
    assert run.model_dump_json() == original


def test_body_request_without_repeated_method_number_is_still_linked_to_owned_footer(monkeypatch):
    run, document = footer_fixture(monkeypatch)
    comment = (
        f"req_001: {PRIMARY} is cited only in the test-item bank alignment; "
        "the body of Section 2 requests surface retention recording without naming a method."
    )
    run.extraction.review_issues = [comment]
    assert assess_requirements(run, document)["req_001"].notices == (comment,)


@pytest.mark.parametrize("change", [
    "unlocated", "unowned", "wrong_family", "withdrawn", "modified", "extra_quantity",
    "scientific_conflict", "wrong_owner", "changed_edition",
])
def test_unproved_or_mixed_footer_concerns_still_block(monkeypatch, change):
    run, document = footer_fixture(monkeypatch)
    row = run.extraction.requirements[0]
    if change == "unlocated":
        document.pages[0].text = document.pages[0].text.replace(FOOTER, "")
    elif change == "unowned":
        row.evidence.pop()
    elif change == "wrong_family":
        replacement = FOOTER.replace("TST-01", "TST-02")
        document.pages[0].text = document.pages[0].text.replace(FOOTER, replacement)
        row.evidence[-1].quote = replacement
    elif change in {"withdrawn", "modified"}:
        replacement = change + ": " + FOOTER
        document.pages[0].text = document.pages[0].text.replace(FOOTER, replacement)
        row.evidence[-1].quote = replacement
    elif change == "extra_quantity":
        run.extraction.review_issues[0] += " Temperature 25 °C is missing."
    elif change == "scientific_conflict":
        run.extraction.review_issues[0] += " The method is not applicable to this material."
    elif change == "wrong_owner":
        run.extraction.review_issues[0] = COMMENT.replace("req_001", "req_other")
    else:
        run.extraction.review_issues[0] = COMMENT.replace(PRIMARY, "ISO 2468:2023")
    assert method_reference_comment_audits(run, document) == []
    assert assess_requirements(run, document)["req_001"].state == "needs_attention"


@pytest.mark.parametrize("non_equivalence", [False, True])
def test_source_proved_inactive_route_class_caveats_allow_before_testing_wording(non_equivalence):
    comment = (
        f"{OPTIONAL} is an approval-dependent alternative for Surface retention whose "
        "acceptance class is not defined in this document; "
        "the customer will issue it before testing."
    )
    run, document = fixture(comment)
    if non_equivalence:
        note = "We are not declaring those two procedures interchangeable."
        document.pages[0].text += "\n" + note
        run.extraction.requirements[0].evidence.append(RequirementEvidence(page=1, quote=note))
        run.extraction.review_issues[0] += (
            " The two procedures are explicitly not declared interchangeable."
        )
    result = assess_requirements(run, document)["req_001"]
    assert result.state == "ready"
    assert result.notices == tuple(run.extraction.review_issues)


def test_a_non_equivalence_claim_without_source_proof_does_not_become_a_notice():
    comment = (
        f"{OPTIONAL} has no stated acceptance class; the customer will issue it before testing. "
        "The two procedures are not interchangeable."
    )
    run, document = fixture(comment)
    assert assess_requirements(run, document)["req_001"].state == "needs_attention"

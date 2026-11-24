"""Retained prose quantities need literal ownership before omission auditing."""

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_requirement

READINGS = "All three readings must be recorded, not just the computed percent."


def requirement(raw=READINGS, *, text=None, context=None, state="parsed", pages=(1,)):
    return CustomerRequirement(
        id="req_literal_text",
        original_test_name="Source-defined reporting request",
        special_instructions=[raw],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[
            AtomicClaim(
                id="native_reporting",
                parameter_id="custom_reporting_obligation",
                label="Reporting obligation",
                kind="reporting",
                operator="required",
                text_value=raw if text is None else text,
                raw_text=raw,
                source_context=context,
                source_pages=list(pages),
                normalization_state=state,
            )
        ],
    )


@pytest.mark.parametrize(
    "raw",
    [
        READINGS,
        "Include five-cycle wash records.",
    ],
)
def test_literal_native_reporting_text_retains_its_quantities_without_asserting_lab_support(raw):
    source = requirement(raw)
    before = source.model_dump(mode="json")
    normalized = normalize_requirement(source, native_first=True)
    assert not any("Unrepresented source quantity:" in issue for issue in normalized.review_issues)
    assert normalized.claims[0].text_value == raw
    assert normalized.claims[0].raw_text == raw
    assert source.model_dump(mode="json") == before

    # Clearing a false omission diagnostic does not establish any lab capability.
    comparison = compare_requirement(normalized, [], method_status="supported")
    retained = next(check for check in comparison.checks if check.id == normalized.claims[0].id)
    assert retained.status == "unknown"


@pytest.mark.parametrize(
    "text",
    [
        "The readings must be recorded.",
        "All two readings must be recorded, not just the computed percent.",
        "All 3 readings must be recorded, not just the computed percent.",
    ],
)
def test_omitted_changed_or_nonliteral_quantity_still_requires_source_review(text):
    normalized = normalize_requirement(requirement(text=text), native_first=True)
    assert any(
        "Unrepresented source quantity: three readings." in issue
        for issue in normalized.review_issues
    )


def test_a_custom_context_or_broad_source_quote_cannot_supply_an_omitted_text_value_quantity():
    normalized = normalize_requirement(
        requirement(text="The readings must be recorded.", context=READINGS),
        native_first=True,
    )
    assert any(
        "Unrepresented source quantity: three readings." in issue
        for issue in normalized.review_issues
    )


def test_a_text_obligation_does_not_replace_a_missing_typed_physical_quantity():
    raw = "One HDT result below 87 °C or one unacceptable impact result places the run on hold."
    normalized = normalize_requirement(requirement(raw), native_first=True)
    assert any(
        "Unrepresented source quantity: 87 °C." in issue for issue in normalized.review_issues
    )


@pytest.mark.parametrize("changes", [{"state": "unresolved"}, {"pages": (2,)}])
def test_unresolved_or_differently_cited_text_cannot_clear_source_quantity_audit(changes):
    normalized = normalize_requirement(requirement(**changes), native_first=True)
    assert any(
        "Unrepresented source quantity: three readings." in issue
        for issue in normalized.review_issues
    )

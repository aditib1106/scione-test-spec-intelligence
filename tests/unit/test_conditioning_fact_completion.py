"""Closed conditioning clauses can fill omitted atoms with literal-source proof."""

from decimal import Decimal

import pytest

from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.conditioning_facts import complete_conditioning_facts
from scione.specifications.models import CustomerRequirement, RequirementEvidence, RequirementValue

CLAUSE = "Conditioning    7 days at 23 °C, 50 % RH (ISO 3270)"


def fixture(clause=CLAUSE, *, physical=None, evidence=None):
    text = physical if physical is not None else clause
    document = IngestedDocument(
        document_id="conditioning_source", file_name="source.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        )],
    )
    requirement = CustomerRequirement(
        id="req_conditioning", original_test_name="Any customer test",
        conditions=[RequirementValue(name="Condition", raw_value=clause)],
        evidence=evidence if evidence is not None else [RequirementEvidence(page=1, quote=text)],
    )
    return requirement, document


def test_complete_literal_conditioning_sentence_adds_three_native_facts_on_a_detached_copy():
    requirement, document = fixture()
    before = requirement.model_dump_json()
    derived, actions = complete_conditioning_facts(requirement, document)
    assert [(c.parameter_id, c.value, c.unit) for c in derived.atomic_claims] == [
        ("duration", Decimal(7), "days"), ("temperature", Decimal(23), "°C"),
        ("relative_humidity", Decimal(50), "% RH"),
    ]
    assert all(c.stage == "conditioning" and c.kind == "condition" for c in derived.atomic_claims)
    assert all(c.raw_text == CLAUSE and c.source_pages == [1] for c in derived.atomic_claims)
    assert len(actions) == 1
    action = actions[0]
    assert action["source_quote"] == document.pages[0].text[
        action["source_start"]:action["source_end"]
    ]
    assert action["applicability_basis"] == "existing_requirement_condition"
    assert action["claim_ids"] == [c.id for c in derived.atomic_claims]
    assert requirement.model_dump_json() == before
    repeated, more_actions = complete_conditioning_facts(derived, document)
    assert repeated == derived and more_actions == []
    again, _ = complete_conditioning_facts(requirement, document)
    assert again == derived


@pytest.mark.parametrize("clause", [
    "Conditioning 7 days at 23 ± 2 °C and 50 ± 5 % RH",
    "Conditioning 7 days at23±2C and50±5%RH",
])
def test_tolerances_and_compact_celsius_are_copied_from_the_complete_closed_expressions(clause):
    requirement, document = fixture(clause)
    derived, actions = complete_conditioning_facts(requirement, document)
    assert len(actions) == 1
    assert [(c.value, c.tolerance) for c in derived.atomic_claims] == [
        (Decimal(7), None), (Decimal(23), Decimal(2)), (Decimal(50), Decimal(5)),
    ]
    assert derived.atomic_claims[1].unit == "°C"
    assert all(c.raw_text == clause for c in derived.atomic_claims)


def test_explicit_ranges_keep_printed_endpoints_without_sorting_or_calculating_new_values():
    requirement, document = fixture("Conditioning 2–7 days at 20–25 °C, 40–60 % RH.")
    derived, actions = complete_conditioning_facts(requirement, document)
    assert len(actions) == 1
    assert [(c.operator, c.minimum, c.maximum) for c in derived.atomic_claims] == [
        ("range", Decimal(2), Decimal(7)), ("range", Decimal(20), Decimal(25)),
        ("range", Decimal(40), Decimal(60)),
    ]


@pytest.mark.parametrize("parameter", ["relative_humidity", "custom_humidity_field"])
def test_an_already_represented_humidity_fact_is_not_duplicated_for_percent_typography(parameter):
    requirement, document = fixture()
    complete, _ = complete_conditioning_facts(requirement, document)
    existing = complete.atomic_claims[-1].model_copy(update={
        "id": "old_rh", "parameter_id": parameter, "unit": "%",
    })
    requirement.atomic_claims = [existing]
    derived, actions = complete_conditioning_facts(requirement, document)
    assert len(derived.atomic_claims) == 3
    assert derived.atomic_claims[0] == existing
    assert len(actions[0]["claim_ids"]) == 2


@pytest.mark.parametrize("change", ["different_value", "unresolved", "extra_scope"])
def test_existing_conditioning_conflict_or_uncertainty_is_never_overwritten(change):
    requirement, document = fixture()
    completed, _ = complete_conditioning_facts(requirement, document)
    existing = completed.atomic_claims[1].model_copy(deep=True)
    if change == "different_value":
        existing.value = Decimal(24)
    elif change == "unresolved":
        existing.normalization_state = "unresolved"
    else:
        existing.source_context = "steel specimens only"
    requirement.atomic_claims = [existing]
    before = requirement.model_dump_json()
    derived, actions = complete_conditioning_facts(requirement, document)
    assert derived == requirement and actions == []
    assert requirement.model_dump_json() == before


@pytest.mark.parametrize("clause", [
    "Do not condition for 7 days at 23 °C, 50 % RH.",
    CLAUSE + " except visual tests",
    CLAUSE + " unless the customer agrees otherwise",
    CLAUSE + "; use batch 9",
    "Conditioning 7 days under nitrogen at 23 °C, 50 % RH",
    "Conditioning 7 days at 23 °C, 50 %",
    "Conditioning 7 days at 23 °C, 50 % RH and 5 mm",
    "Conditioning 7 days at 23 °C, 50 % RH with an unknown requirement",
    "Conditioning 7–2 days at 23 °C, 50 % RH",
])
def test_unclosed_qualified_or_extra_scientific_content_is_left_for_review(clause):
    requirement, document = fixture(clause)
    derived, actions = complete_conditioning_facts(requirement, document)
    assert derived == requirement and actions == []


@pytest.mark.parametrize("physical", [
    CLAUSE.replace("23 °C", "24 °C"),
    CLAUSE.split(",")[0],
    "Do not " + CLAUSE,
    CLAUSE + "; batch 9 must be used",
])
def test_fragment_changes_missing_source_and_omitted_physical_qualifiers_cannot_supply_proof(
    physical,
):
    requirement, document = fixture(physical=physical)
    derived, actions = complete_conditioning_facts(requirement, document)
    assert derived == requirement and actions == []


def test_a_wrong_physical_evidence_page_cannot_be_repaired_by_searching_elsewhere():
    requirement, document = fixture(evidence=[RequirementEvidence(page=2, quote=CLAUSE)])
    derived, actions = complete_conditioning_facts(requirement, document)
    assert derived == requirement and actions == []


def test_unique_server_document_span_can_locate_an_already_attached_condition():
    requirement, document = fixture(
        physical="Test objective\n" + CLAUSE,
        evidence=[RequirementEvidence(page=1, quote="Test objective")],
    )
    derived, actions = complete_conditioning_facts(requirement, document)
    assert len(derived.atomic_claims) == 3
    assert actions[0]["location_kind"] == "unique_document_literal"
    assert RequirementEvidence(page=1, quote=CLAUSE) in derived.evidence


def test_repeated_unowned_source_condition_stays_ambiguous():
    requirement, document = fixture(
        physical="Test objective\n" + CLAUSE + "\n" + CLAUSE,
        evidence=[RequirementEvidence(page=1, quote="Test objective")],
    )
    derived, actions = complete_conditioning_facts(requirement, document)
    assert derived == requirement and actions == []

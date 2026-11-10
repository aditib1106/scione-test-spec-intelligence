"""Clause segmentation and metadata ownership do not erase independent demands."""

from copy import deepcopy
from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured import normalize_requirement
from scione.structured.lexical_grounding import source_quantities
from scione.structured.models import AtomicClaim
from scione.structured.source_clauses import context_owned_word_spans, source_clauses


def test_independent_cure_conditioning_and_map_sentences_keep_their_own_stages():
    raw = "Cure at 146 °C for 18 min. Allow 6 days at 22 °C and 48% RH. Mark 4 positions."
    source = CustomerRequirement(
        id="req_stages", original_test_name="Coating evaluation",
        conditions=[dict(name="Preparation", raw_value=raw)],
        evidence=[dict(page=1, quote=raw)],
    )
    normalized = normalize_requirement(source)
    assert any(c.parameter_id == "temperature" and c.value == 146 and c.stage == "curing"
               for c in normalized.claims)
    assert any(c.parameter_id == "temperature" and c.value == 22 and c.stage == "conditioning"
               for c in normalized.claims)
    assert any(c.parameter_id == "measurement_locations" and c.value == 4
               and c.stage == "measurement" for c in normalized.claims)
    assert not any(c.parameter_id == "temperature" and c.value == 146
                   and c.stage == "conditioning" for c in normalized.claims)


def test_abbreviations_decimals_and_anaphoric_antecedents_are_retained():
    assert source_clauses("Use Fig. 3. Measure at 23.5 °C.") == [
        "Use Fig. 3.", "Measure at 23.5 °C.",
    ]
    assert source_clauses("Record topcoat thickness. The latter must be 40-50 µm.") == [
        "Record topcoat thickness. The latter must be 40-50 µm.",
    ]


@pytest.mark.parametrize("text,value,unit", [
    ("Seven cycles between -35 °C and 80 °C.", 7, "cycles"),
    ("An eight-position map on each panel.", 8, "locations"),
    ("Use a 6-position map; retain tool GX-8.", 6, "locations"),
    ("Record gloss at five marked locations.", 5, "locations"),
])
def test_count_classifiers_are_not_lost_to_prose_or_hyphens(text, value, unit):
    assert any(q.value == value and q.unit == unit for q in source_quantities(text))


def test_units_compounds_ranges_and_identifier_suffixes_remain_distinct():
    assert not source_quantities("Velocity 12 mm s-1.")
    assert not source_quantities("Fixture QX-eight-position.")
    counts = source_quantities("Use 3 to 5 cycles.")
    assert {(q.value, q.role) for q in counts} == {
        (Decimal(3), "range_endpoint"), (Decimal(5), "range_endpoint"),
    }
    assert not source_quantities("Fixture QX-five marked locations.")
    assert not source_quantities("Use five certified locations.")
    marked_range = source_quantities("Use 3 to 5 marked locations.")
    assert {(q.value, q.role) for q in marked_range} == {
        (Decimal(3), "range_endpoint"), (Decimal(5), "range_endpoint"),
    }


def test_flat_witness_count_does_not_consume_its_independent_dimensions():
    raw = "Coat nine flat 100 × 200 mm steel witnesses, using material GX-7."
    quantities = source_quantities(raw)
    assert any(q.value == 9 and q.unit == "panels" for q in quantities)
    assert any(q.value == 200 and q.unit == "mm" for q in quantities)
    requested = CustomerRequirement(
        id="req_panels", original_test_name="Panel assessment",
        conditions=[dict(name="Prepared specimens", raw_value=raw)],
        atomic_claims=[AtomicClaim(
            id="panels", parameter_id="panel_count", label="Panel count", operator="=",
            value=9, unit="panels", raw_text=raw, source_pages=[1],
        )], evidence=[dict(page=1, quote=raw)],
    )
    normalized = normalize_requirement(requested)
    assert normalized.claims[0].normalization_state == "parsed"
    assert any("GX-7".casefold() in (c.text_value or "").casefold() for c in normalized.claims)
    assert any("100" in (c.text_value or "") and "200" in (c.text_value or "")
               and "mm" in (c.text_value or "") for c in normalized.claims)
    # No parser is permitted to multiply this into 20,000 mm or infer which
    # unlabelled axis the first value belongs to.
    assert not any(c.value == 20000 for c in normalized.claims)


def test_context_projection_owns_words_only_not_intervening_material_or_negation():
    source = "The arithmetic mean must be 81-91 GU as delivered, except on aluminium."
    spans = context_owned_word_spans("arithmetic mean, as delivered", source, "initial")
    remainder = list(source)
    for start, end in spans:
        remainder[start:end] = " " * (end - start)
    assert "except on aluminium" in "".join(remainder)
    assert "81-91" in "".join(remainder)


def test_custom_numeric_unit_stays_literal_and_wrong_operator_stays_blocked():
    def source(operator, raw):
        return CustomerRequirement(
            id="req_flux", original_test_name="Twilight flux",
            conditions=[dict(name="Twilight flux", raw_value="27", unit="flarps")],
            atomic_claims=[AtomicClaim(
                id="flux", parameter_id="twilight_flux", label="Twilight flux",
                operator=operator, value=27, unit="flarps", raw_text=raw, source_pages=[1],
            )],
            evidence=[dict(page=1, quote=raw)],
        )
    correct = source("=", "Twilight flux 27 flarps.")
    before = deepcopy(correct.model_dump())
    normalized = normalize_requirement(correct)
    assert len(normalized.claims) == 1
    assert normalized.claims[0].parameter_id == "twilight_flux"
    assert normalized.claims[0].unit == "flarps"
    assert normalized.claims[0].normalization_state == "parsed"
    assert correct.model_dump() == before
    wrong = normalize_requirement(source("=", "Twilight flux at least 27 flarps."))
    assert wrong.claims[0].normalization_state == "unresolved"
    assert wrong.review_issues

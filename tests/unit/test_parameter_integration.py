"""Adversarial checks at the production comparator/AI-link boundary."""

from decimal import Decimal

from scione.matching.service import _compatible_context
from scione.specifications.models import CompactAtomicClaim, CustomerRequirement
from scione.structured import normalize_requirement
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def fact(identifier, parameter="custom_flux", **updates):
    return AtomicClaim.model_validate(
        {
            "id": identifier,
            "parameter_id": parameter,
            "label": parameter,
            "kind": "condition",
            "operator": "=",
            "value": "10",
            "unit": None,
            "raw_text": "A stated value of 10",
            **updates,
        }
    )


def test_unknown_custom_numbers_without_units_cannot_silently_support():
    request, lab = fact("requested"), fact("lab")
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_one", test_name="A test", claims=[request]), [lab]
    )
    assert result.checks[1].status == "unknown"


def test_numeric_instruction_is_not_dropped_by_workflow_wording():
    request = fact(
        "requested",
        parameter="distance",
        kind="instruction",
        unit="cm",
        raw_text="Retain raw observations at a distance of 10 cm",
    )
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_one", test_name="A test", claims=[request]), []
    )
    assert result.checks[1].status == "unknown"


def test_semantic_guard_accepts_curated_multilingual_context_without_redundant_string_check():
    request = fact("requested", parameter="temperature", unit="°C", source_context="钢板")
    lab = fact("lab", parameter="temperature", unit="°C", source_context="steel panel")
    assert _compatible_context(request, lab) is None


def test_semantic_guard_rejects_different_media_and_mass_force():
    water = fact("water", parameter="temperature", unit="°C", raw_text="Test water at 10 °C")
    air = fact("air", parameter="temperature", unit="°C", raw_text="Air temperature 10 °C")
    assert _compatible_context(water, air) is not None
    mass = fact("mass", parameter="load", unit="g")
    force = fact("force", parameter="load", unit="N")
    assert _compatible_context(mass, force) is not None


def test_canonical_alias_matching_retains_original_source_claims():
    request = fact("requested", parameter="温度", unit="°C", label="温度", value=Decimal(10))
    lab = fact(
        "lab",
        parameter="temperature",
        unit="°C",
        operator="range",
        value=None,
        minimum=Decimal(0),
        maximum=Decimal(30),
    )
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_one", test_name="A test", claims=[request]), [lab]
    )
    assert result.checks[1].status == "supported"
    assert result.checks[1].requirement_claim == request
    assert request.parameter_id == "温度"


def test_compact_extraction_wire_retains_named_context_and_configuration():
    claim = CompactAtomicClaim(
        parameter_id="water_temperature",
        kind="condition",
        stage="conditioning",
        operator="=",
        values=[60],
        unit="°C",
        text_value=None,
        raw_text="steel panel in water at 60 °C",
        source_pages=[1],
        source_context="steel panel",
        configuration="water bath A",
    ).expand("claim_one")
    assert claim.source_context == "steel panel"
    assert claim.configuration == "water bath A"
    assert claim.value == Decimal(60)


def test_machine_atom_cannot_relabel_a_source_unit_without_converting_numbers():
    for source_unit, invented_unit in [
        ("°C", "°F"),
        ("mm", "m"),
        ("MPa", "Pa"),
        ("Mm", "mm"),
        ("S", "s"),
    ]:
        raw = f"Operating value 23 {source_unit}"
        claim = fact(
            "model_claim",
            parameter="temperature",
            unit=invented_unit,
            value=Decimal(23),
            raw_text=raw,
            source_pages=[1],
        )
        requirement = CustomerRequirement(
            id="req_units",
            original_test_name="Test",
            conditions=[{"name": "Condition", "raw_value": raw}],
            evidence=[{"page": 1, "quote": raw}],
            atomic_claims=[claim],
        )
        normalized = normalize_requirement(requirement)
        assert normalized.claims[0].normalization_state == "unresolved"


def test_machine_fahrenheit_atom_with_literal_source_unit_stays_grounded():
    raw = "Test water at 140 °F"
    claim = fact(
        "model_claim",
        parameter="water_temperature",
        unit="°F",
        value=Decimal(140),
        raw_text=raw,
        source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_units",
        original_test_name="Test",
        conditions=[{"name": "Condition", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[claim],
    )
    assert normalize_requirement(requirement).claims[0].normalization_state == "parsed"


def test_compound_source_does_not_pair_a_temperature_number_with_the_duration_unit():
    raw = "Condition at 23 °C for 2 h"
    claim = fact(
        "model_claim",
        parameter="duration",
        unit="h",
        value=Decimal(23),
        raw_text=raw,
        source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_units",
        original_test_name="Test",
        conditions=[{"name": "Condition", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[claim],
    )
    assert normalize_requirement(requirement).claims[0].normalization_state == "unresolved"


def test_textual_model_alternatives_are_retained_unresolved_without_inventing_values():
    from scione.specifications.extraction import _expand_compact_extraction
    from scione.specifications.models import CompactCustomerExtraction

    raw = "only class 0 or 1 is acceptable"
    compact = CompactCustomerExtraction.model_validate(
        {
            "document": {},
            "requirements": [
                {
                    "test": "Adhesion",
                    "criteria": [raw],
                    "evidence": [{"page": 1, "quote": raw}],
                    "atomic_claims": [
                        {
                            "parameter_id": "adhesion_class",
                            "kind": "criterion",
                            "stage": "measurement",
                            "operator": "one_of",
                            "values": [],
                            "unit": None,
                            "text_value": "class 0 or 1",
                            "raw_text": raw,
                            "source_pages": [1],
                        }
                    ],
                }
            ],
        }
    )
    extraction = _expand_compact_extraction(compact)
    claim = extraction.requirements[0].atomic_claims[0]
    assert claim.operator == "text"
    assert claim.normalization_state == "unresolved"
    assert claim.parameter_id is None and claim.values == []
    assert claim.raw_text == raw
    assert "req_001:" in extraction.review_issues[0]


def test_generic_reporting_unavailability_cannot_be_hidden_by_a_requested_setup():
    requested = fact(
        "requested",
        parameter="gloss_retention",
        kind="criterion",
        operator=">=",
        value=90,
        unit="%",
        stage="after_cycle",
        source_context="each marked position",
        configuration="customer fixture",
    )
    declared = fact(
        "lab",
        parameter="gloss_retention",
        kind="reporting",
        operator="required",
        value=None,
        unit="%",
        stage="after_cycle",
        availability="unsupported",
        raw_text="Post-cycle gloss retention is not reportable.",
    )
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_one", test_name="Gloss", claims=[requested]),
        [declared],
    )
    assert result.checks[1].status == "gap"

"""Quantity occurrence identity is separate from metadata and restriction proof."""

from decimal import Decimal

import pytest

from scione.structured.models import AtomicClaim
from scione.structured.source_occurrences import prove_same_numeric_occurrence


def atom(raw, *, identifier="atom", parameter="temperature", value=23, unit="°C", **extra):
    return AtomicClaim(
        id=identifier,
        parameter_id=parameter,
        label=parameter or "Unresolved",
        operator=extra.pop("operator", "="),
        value=Decimal(str(value)) if value is not None else None,
        raw_text=raw,
        unit=unit,
        source_pages=extra.pop("source_pages", [1]),
        **extra,
    )


def prove(raw, first, second):
    return prove_same_numeric_occurrence(first, second, source_text=raw, source_pages=[1])


@pytest.mark.parametrize(
    "parameter,value,unit,tolerance,quote,stage,context",
    [
        (
            "duration",
            22,
            "h",
            None,
            "25% nominal compression for 22 hours at 100 ± 2 °C",
            "measurement",
            "compression dwell duration",
        ),
        (
            "temperature",
            100,
            "°C",
            2,
            "for 22 hours at 100 ± 2 °C",
            "measurement",
            "compression dwell temperature",
        ),
        (
            "duration",
            30,
            "min",
            None,
            "followed by 30 minutes of unloaded recovery at 23 ± 2 °C",
            "cycle_recovery",
            "unloaded recovery after compression",
        ),
        (
            "temperature",
            23,
            "°C",
            2,
            "30 minutes of unloaded recovery at 23 ± 2 °C",
            "cycle_recovery",
            "unloaded recovery temperature",
        ),
    ],
)
def test_real_multistage_clause_locates_single_quantity_without_approving_paraphrase(
    parameter, value, unit, tolerance, quote, stage, context
):
    raw = (
        "The customer requires a 25% nominal compression for 22 hours at 100 ± 2 °C, "
        "followed by 30 minutes of unloaded recovery at 23 ± 2 °C."
    )
    fields = {"parameter": parameter, "value": value, "unit": unit, "tolerance": tolerance}
    fallback = atom(raw, identifier="fallback", **fields)
    supplied = atom(quote, identifier="supplied", stage=stage, source_context=context, **fields)
    proof = prove(raw, fallback, supplied)
    assert proof is not None
    assert proof.fallback_id == "fallback"
    assert proof.supplied_id == "supplied"
    assert raw[slice(*proof.supplied_quote_span)] == quote
    assert raw[slice(*proof.unit_span)] in {"hours", "minutes", "°C"}
    assert "stage_differs" in proof.metadata_flags
    assert "supplied_source_context_not_literal" in proof.metadata_flags
    assert proof.requires_metadata_review
    # This function is evidence only; it does not mutate either claim.
    assert supplied.source_context == context
    assert supplied.normalization_state == "parsed"


def test_matching_metadata_can_be_literal_but_does_not_consume_restriction():
    raw = "Conditioning at 23 °C except nitrogen."
    supplied = atom(raw, stage="conditioning", source_context="except nitrogen")
    proof = prove(raw, supplied, supplied)
    assert proof is not None
    assert not proof.requires_metadata_review
    assert raw[slice(*proof.expression_span)] == "23 °C"
    assert proof.expression_span[1] < raw.index("except")
    assert "except nitrogen" in raw


def test_repeated_equal_quantities_need_unambiguous_quote_positions():
    raw = "Condition at 23 °C; recover at 23 °C."
    assert prove(raw, atom(raw), atom("Condition at 23 °C")) is None
    assert prove(raw, atom("23 °C"), atom("Condition at 23 °C")) is None
    assert prove(raw, atom("Condition at 23 °C"), atom("recover at 23 °C")) is None
    proof = prove(raw, atom("recover at 23 °C"), atom("recover at 23 °C"))
    assert proof is not None
    assert proof.expression_span[0] == raw.rindex("23")


@pytest.mark.parametrize(
    "change",
    [
        {"parameter": "duration"},
        {"kind": "criterion"},
        {"value": 99},
        {"unit": "°F"},
        {"availability": "unknown"},
        {"source_pages": [2]},
        {"normalization_state": "unresolved", "parameter": None},
        {"operator": "<=", "kind": "condition"},
    ],
)
def test_different_identity_untrusted_state_or_pages_do_not_prove_a_copy(change):
    raw = "Condition at 23 °C."
    assert prove(raw, atom(raw), atom(raw, **change)) is None


def test_equal_dimension_unit_conversion_is_not_same_source_occurrence():
    raw = "Dwell 60 min."
    assert (
        prove(
            raw,
            atom(raw, parameter="duration", value=60, unit="min"),
            atom(raw, parameter="duration", value=1, unit="h"),
        )
        is None
    )


def test_curated_stage_prefixed_alias_can_share_one_literal_quantity():
    raw = "Cure duration: 20 minutes at 140 °C."
    fallback = atom(raw, parameter="duration", value=20, unit="min")
    supplied = atom(
        "20 minutes at 140 °C",
        parameter="cure_duration",
        value=20,
        unit="minutes",
        stage="curing",
    )
    proof = prove(raw, fallback, supplied)
    assert proof is not None
    assert "stage_differs" in proof.metadata_flags
    assert supplied.parameter_id == "cure_duration"


def test_unfamiliar_custom_name_cannot_replace_a_known_quantity():
    raw = "Cure duration: 20 minutes at 140 °C."
    assert (
        prove(
            raw,
            atom(raw, parameter="duration", value=20, unit="min"),
            atom("20 minutes at 140 °C", parameter="unfamiliar_duration_a", value=20, unit="min"),
        )
        is None
    )


def test_equal_number_cannot_merge_conflicting_known_length_parameters():
    raw = "Panel length 20 mm."
    assert (
        prove(
            raw,
            atom(raw, parameter="specimen_length", value=20, unit="mm"),
            atom(raw, parameter="spacing", value=20, unit="mm"),
        )
        is None
    )


def test_tolerance_cannot_be_omitted_or_added_by_dedup():
    raw = "At 23 ± 2 °C."
    assert prove(raw, atom(raw), atom(raw)) is None
    proof = prove(raw, atom(raw, tolerance=2), atom(raw, tolerance=2))
    assert proof is not None
    assert len(proof.number_spans) == 2
    assert [raw[slice(*span)] for span in proof.number_spans] == ["23", "2"]


def test_range_cannot_be_replaced_by_an_endpoint_or_wrong_bound():
    raw = "Film thickness 65–85 µm."
    fields = {
        "parameter": "film_thickness",
        "value": None,
        "unit": "µm",
        "operator": "range",
        "minimum": Decimal(65),
        "maximum": Decimal(85),
    }
    proof = prove(raw, atom(raw, **fields), atom(raw, **fields))
    assert proof is not None
    assert [raw[slice(*span)] for span in proof.number_spans] == ["65", "85"]
    assert (
        prove(
            raw,
            atom(raw, parameter="film_thickness", value=65, unit="µm"),
            atom(raw, parameter="film_thickness", value=65, unit="µm"),
        )
        is None
    )
    wrong = fields | {"maximum": Decimal(95)}
    assert prove(raw, atom(raw, **wrong), atom(raw, **wrong)) is None


def test_source_operator_and_parameter_roles_stay_distinct():
    raw = "Compressive strength at least 38 MPa."
    first = atom(raw, parameter="compressive_strength", value=38, unit="MPa", operator=">=")
    assert prove(raw, first, first) is not None
    wrong = atom(raw, parameter="compressive_strength", value=38, unit="MPa", operator="<=")
    assert prove(raw, wrong, wrong) is None


def test_ambiguous_locale_quantity_does_not_get_a_proof():
    raw = "1,020 MPa"
    first = atom(raw, parameter="tensile_strength", value=1020, unit="MPa")
    assert prove(raw, first, first) is None


def test_cjk_whitespace_mapping_keeps_original_offsets():
    raw = "  调节\n温度为 23 ± 2 °C，\n保持 24 h。  "
    quote = "调节温度为23 ± 2 °C，保持24 h。"
    first = atom(raw, tolerance=2)
    second = atom(quote, tolerance=2)
    proof = prove(raw, first, second)
    assert proof is not None
    assert raw[slice(*proof.expression_span)] == "23 ± 2 °C"
    assert raw[slice(*proof.unit_span)] == "°C"
    assert proof.number_spans[0] == (raw.index("23"), raw.index("23") + 2)


def test_latin_token_boundary_case_and_sign_are_not_fuzzy():
    raw = "At -23 °C; identifier TEMP23 °C."
    first = atom("23 °C", value=23)
    assert prove(raw, first, first) is None
    assert (
        prove("Condition at 23 °C", atom("condition at 23 °C"), atom("condition at 23 °C")) is None
    )


def test_missing_quantity_and_unsupported_compound_unit_are_not_invented():
    raw = "Pressure rate 3 MPa/s."
    first = atom(raw, parameter="pressure", value=3, unit="MPa")
    assert prove(raw, first, first) is None
    assert (
        prove("At room temperature.", atom("At room temperature."), atom("At room temperature."))
        is None
    )


def test_metadata_conflicts_are_flags_never_silent_metadata_acceptance():
    raw = "Conditioning step 2 at 23 °C on steel."
    fallback = atom(raw, stage="conditioning", sequence=2, source_context="steel")
    supplied = atom(
        raw, stage="thermal_cycle", sequence=1, source_context="aluminium", configuration="flat"
    )
    proof = prove(raw, fallback, supplied)
    assert proof is not None
    assert set(proof.metadata_flags) >= {
        "stage_differs",
        "sequence_differs",
        "source_context_differs",
        "supplied_stage_not_literal",
        "supplied_sequence_not_literal",
        "supplied_source_context_not_literal",
        "supplied_configuration_not_literal",
    }


def test_normalized_unique_copy_keeps_source_atom_and_blocks_false_support():
    from scione.specifications.models import CustomerRequirement
    from scione.structured.comparison import compare_requirement
    from scione.structured.normalization import normalize_requirement

    raw = "Conditioning stage: step 2 at 23 °C on steel."
    original = atom(
        "step 2 at 23 °C", stage="thermal_cycle", sequence=2, source_context="aluminium"
    )
    requirement = CustomerRequirement(
        id="req_one",
        original_test_name="Example",
        atomic_claims=[original],
        conditions=[{"name": "Condition", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
    )
    before = requirement.model_dump(mode="json")
    normalized = normalize_requirement(requirement)
    assert requirement.model_dump(mode="json") == before
    numeric = [claim for claim in normalized.claims if claim.value is not None]
    assert len(numeric) == 1
    assert numeric[0].stage == original.stage
    assert numeric[0].source_context == original.source_context
    assert normalized.review_issues
    # Even a declaration matching the model's wrong metadata cannot certify support.
    compared = compare_requirement(normalized, [numeric[0].model_copy(deep=True)])
    assert compared.status == "needs_information"
    assert (
        len([check for check in compared.checks if check.id == "req_one_normalization_review"]) == 1
    )
    assert any("steel" in (claim.text_value or "") for claim in normalized.claims)


def test_french_apostrophe_typography_maps_back_to_unchanged_original_source_offsets():
    from scione.source_text import citation_text
    from scione.structured.source_occurrences import _canonical_source_with_offsets

    raw = "  La température\n de l’eau est de 23 ± 2 °C. AX’17 reste inchangé.  "
    quote = "La température de l'eau est de 23 ± 2 °C."
    first = atom(quote, tolerance=2)
    proof = prove(raw, first, first)
    assert proof is not None
    assert raw[slice(*proof.supplied_quote_span)] == "La température\n de l’eau est de 23 ± 2 °C."
    assert raw[slice(*proof.expression_span)] == "23 ± 2 °C"
    assert raw[slice(*proof.number_spans[0])] == "23"
    assert raw[slice(*proof.unit_span)] == "°C"
    canonical, offsets = _canonical_source_with_offsets(raw)
    assert canonical == citation_text(raw)
    assert len(canonical) == len(offsets)
    apostrophe = canonical.index("'")
    assert raw[slice(*offsets[apostrophe])] == "’"
    assert "AX’17" in canonical


def test_french_typography_cannot_prove_a_wrong_numeric_value_or_wrong_word():
    raw = "La température de l’eau est de 23 °C."
    assert (
        prove(
            raw,
            atom("La température de l'eau est de 33 °C.", value=33),
            atom("La température de l'eau est de 33 °C.", value=33),
        )
        is None
    )
    assert (
        prove(
            raw,
            atom("La température de l'air est de 23 °C."),
            atom("La température de l'air est de 23 °C."),
        )
        is None
    )

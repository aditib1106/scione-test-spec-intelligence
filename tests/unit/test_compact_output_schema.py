"""Real semantic shape regressions enforced before compact JSON generation."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from scione.specifications.extraction import _structured_customer_schema
from scione.specifications.models import CompactCustomerExtraction
from scione.specifications.output_schema import constrain_compact_extraction_schema


def wire_schema():
    return constrain_compact_extraction_schema(
        _structured_customer_schema(CompactCustomerExtraction.model_json_schema())
    )


def recorded_sail_claim(**updates):
    # Minimal, unchanged offending shape from the first live GLM-5.3 response.
    # Keep it version-controlled here; tests never depend on ignored run files.
    claim = {
        "parameter_id": "illuminant",
        "kind": "condition",
        "stage": "measurement",
        "operator": "=",
        "values": [],
        "unit": None,
        "text_value": "D65",
        "raw_text": "D65 illuminant",
        "source_pages": [2],
        "sequence": None,
        "source_context": "T4 measurement stage",
        "configuration": None,
    }
    claim.update(updates)
    return claim


def payload(claim):
    return {
        "document": {"title": None, "identifier": None, "revision": None},
        "shared_requirements": [],
        "document_notices": [],
        "requirements": [
            {
                "test": "Colour difference",
                "methods": [],
                "method_roles": [],
                "conditions": [claim["raw_text"]],
                "criteria": [],
                "scope": [],
                "instructions": [],
                "evidence": [{"page": 2, "quote": claim["raw_text"]}],
                "uncertainty": None,
                "atomic_claims": [claim],
            }
        ],
        "review_issues": [],
    }


def test_recorded_sail_d65_numeric_shape_is_rejected_without_repairing_response():
    invalid = payload(recorded_sail_claim())
    untouched = deepcopy(invalid)
    validator = Draft202012Validator(wire_schema())
    assert not validator.is_valid(invalid)
    assert invalid == untouched

    valid_category = payload(recorded_sail_claim(operator="text"))
    assert validator.is_valid(valid_category)
    expanded = CompactCustomerExtraction.model_validate(valid_category).requirements[0]
    assert expanded.atomic_claims[0].values == []
    assert expanded.atomic_claims[0].text_value == "D65"
    assert expanded.atomic_claims[0].raw_text == "D65 illuminant"


@pytest.mark.parametrize(
    "operator,values,text,valid",
    [
        ("=", [], None, False),
        ("=", [23], None, True),
        ("=", [23, 2], None, True),
        ("=", [23, 2, 1], None, False),
        ("range", [], None, False),
        ("range", [20], None, False),
        ("range", [20, 26], None, True),
        ("range", [20, 26, 30], None, False),
        ("<=", [], None, False),
        ("<=", [1], None, True),
        (">=", [1, 2], None, False),
        ("<", [1], None, True),
        (">", [1], None, True),
        ("text", [], "Miralum-B", True),
        ("text", [65], "D65", False),
        ("required", [], "AX-17", True),
        ("required", [17], "AX-17", False),
        ("one_of", [5, 10], None, True),
        ("one_of", [], "aluminium or steel", True),
        ("one_of", [], None, False),
        ("one_of", [], "", False),
    ],
)
def test_operator_arity_is_expressed_in_provider_schema(operator, values, text, valid):
    value = payload(recorded_sail_claim(operator=operator, values=values, text_value=text))
    assert Draft202012Validator(wire_schema()).is_valid(value) is valid


def test_unknown_qualifier_and_textual_alternatives_remain_source_preserving():
    source = "Use the QX-9 insert only on Miralum-B; other materials need approval."
    claim = recorded_sail_claim(
        parameter_id=None,
        operator="text",
        text_value=source,
        raw_text=source,
        source_context="on Miralum-B only",
    )
    value = payload(claim)
    assert Draft202012Validator(wire_schema()).is_valid(value)
    compact = CompactCustomerExtraction.model_validate(value)
    expanded = compact.requirements[0].atomic_claims[0].expand("req_001_claim_1")
    assert expanded.normalization_state == "unresolved"
    assert expanded.raw_text == source
    assert expanded.text_value == source
    assert expanded.source_context == "on Miralum-B only"


def test_native_unit_limit_matches_the_expanded_atomic_contract():
    claim = recorded_sail_claim(operator="text", unit="a" * 41)
    assert not Draft202012Validator(wire_schema()).is_valid(payload(claim))
    claim["unit"] = "a" * 40
    compact = CompactCustomerExtraction.model_validate(payload(claim))
    assert compact.requirements[0].atomic_claims[0].expand("atom_bounded_unit").unit == "a" * 40


def test_textual_alternatives_remain_unresolved_without_invented_numeric_values():
    alternate = recorded_sail_claim(
        parameter_id="substrate",
        operator="one_of",
        values=[],
        text_value="aluminium or steel",
        raw_text="aluminium or steel",
    )
    assert Draft202012Validator(wire_schema()).is_valid(payload(alternate))
    expanded = CompactCustomerExtraction.model_validate(payload(alternate))
    fact = expanded.requirements[0].atomic_claims[0].expand("req_001_claim_1")
    assert fact.parameter_id is None
    assert fact.normalization_state == "unresolved"
    assert fact.text_value == "aluminium or steel"
    assert fact.raw_text == "aluminium or steel"


def test_schema_builder_keeps_input_detached_and_preserves_strict_object_fields():
    original = _structured_customer_schema(CompactCustomerExtraction.model_json_schema())
    untouched = deepcopy(original)
    schema = constrain_compact_extraction_schema(original)
    Draft202012Validator.check_schema(schema)
    assert original == untouched
    for branch in schema["$defs"]["CompactAtomicClaim"]["anyOf"]:
        assert set(branch["required"]) == set(branch["properties"])
        assert branch["additionalProperties"] is False
        assert all("default" not in field for field in branch["properties"].values())
    schema["$defs"]["CompactAtomicClaim"]["anyOf"][0]["properties"]["values"]["minItems"] = 99
    assert original == untouched


def test_positive_physical_pages_and_unknown_fields_remain_enforced():
    validator = Draft202012Validator(wire_schema())
    assert not validator.is_valid(payload(recorded_sail_claim(operator="text", source_pages=[0])))
    value = payload(recorded_sail_claim(operator="text"))
    value["requirements"][0]["atomic_claims"][0]["invented_fact"] = "not allowed"
    assert not validator.is_valid(value)


def test_wrong_schema_fails_before_a_provider_can_be_called():
    with pytest.raises(ValueError, match="strict compact extraction schema"):
        constrain_compact_extraction_schema({"type": "object", "properties": {}})

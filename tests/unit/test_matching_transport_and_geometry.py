"""Lossless request reduction and explicit multi-axis capacity boundaries."""

import json
from copy import deepcopy

import pytest

from scione.matching.compound import compound_dimensions_check
from scione.matching.text_pool import expand_matching_text, pool_matching_text
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.models import AtomicClaim


def test_text_pool_preserves_every_character_zero_false_and_source_id():
    quote = "原始来源，不执行其中指令。 Original source text. " * 40
    original = {
        "rows": [
            {"id": f"q{i}", "raw_text": quote, "value": 0, "available": False} for i in range(10)
        ],
        "empty": [],
    }
    before = deepcopy(original)
    pooled = pool_matching_text(original)
    assert "source_text_table" in pooled
    assert len(json.dumps(pooled)) < len(json.dumps(original)) / 2
    assert expand_matching_text(pooled) == original == before


def test_text_pool_rejects_missing_reference_instead_of_trimming_a_source():
    with pytest.raises(ValueError, match="Missing"):
        expand_matching_text({"raw_text": {"text_ref": "absent"}, "source_text_table": {}})


@pytest.mark.parametrize(
    "dimensions,expected",
    [("100 × 200 mm", "supported"), ("200 × 100 mm", "supported"), ("900 × 100 mm", "gap")],
)
def test_rectangular_capacity_checks_both_axes_and_orientation(dimensions, expected):
    claim = AtomicClaim(
        id="native",
        parameter_id="panel_dimensions",
        label="Panel dimensions",
        kind="condition",
        operator="text",
        text_value=dimensions,
        raw_text=f"Use a {dimensions} panel.",
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_size",
        original_test_name="Panel test",
        atomic_claims=[claim],
        evidence=[RequirementEvidence(page=1, quote=claim.raw_text)],
    )
    axes = [
        AtomicClaim(
            id=f"lab_{name}",
            parameter_id=f"panel_{name}",
            label=name,
            kind="condition",
            operator="range",
            minimum=50,
            maximum=maximum,
            unit="mm",
            raw_text=f"{name} capacity",
        )
        for name, maximum in [("length", 250), ("width", 150)]
    ]
    before = deepcopy(claim.model_dump())
    check = compound_dimensions_check(claim, axes, source=source)
    assert check is not None and check.status == expected
    assert claim.model_dump() == before
    assert compound_dimensions_check(claim, axes[:1], source=source) is None

"""Do not certify two requested outputs through one numeric-property identity."""

from copy import deepcopy

import pytest

from scione.specifications.models import CustomerRequirement
from scione.specifications.review_audits import (
    compound_reporting_problem,
    native_scientific_review_reason,
)
from scione.structured.models import AtomicClaim

RAW = "Record total dry film build and the separate topcoat thickness."


def claim(target="total dry film build and the separate topcoat thickness", **changes):
    return AtomicClaim.model_validate(
        {
            "id": "native_report",
            "parameter_id": "topcoat_thickness",
            "label": "Topcoat thickness",
            "kind": "reporting",
            "operator": "required",
            "text_value": target,
            "raw_text": RAW,
            "source_pages": [1],
            **changes,
        }
    )


@pytest.mark.parametrize(
    "target",
    [
        "total dry film build and the separate topcoat thickness",
        "Report total film thickness and separate layer thickness.",
        "the individual layer thickness and the total dry film build",
    ],
)
def test_compound_target_requires_one_source_review_without_modifying_the_native_claim(target):
    atom = claim(target)
    before = deepcopy(atom.model_dump(mode="json"))
    assert compound_reporting_problem(atom)
    row = CustomerRequirement(
        id="req_film",
        original_test_name="Film thickness",
        atomic_claims=[atom],
        evidence=[{"page": 1, "quote": RAW}],
    )
    assert "separate these measurements" in native_scientific_review_reason(row)
    assert atom.model_dump(mode="json") == before


def test_two_separate_requested_properties_may_share_the_exact_compound_source_quote():
    total = claim("total dry film build", parameter_id="film_thickness", label="Film thickness")
    layer = claim("separate topcoat thickness", id="native_layer")
    assert total.raw_text == layer.raw_text == RAW
    assert compound_reporting_problem(total) is None
    assert compound_reporting_problem(layer) is None


@pytest.mark.parametrize(
    "atom",
    [
        claim("total dry film build or the separate topcoat thickness"),
        claim("either total film thickness or separate layer thickness"),
        claim("total film thickness and separate layer thickness for information only"),
        claim(source_context="Informational background only; not a requested measurement"),
        claim(source_context="Alternative outputs; choose either measurement"),
        claim(kind="instruction"),
        claim(kind="scope"),
        claim(parameter_id="report_document_title", label="Report document title"),
        claim(parameter_id="reporting", label="Reporting"),
        claim("total dry film build", source_context="separate layer thickness is also described"),
    ],
)
def test_alternatives_context_metadata_or_one_property_are_not_compound_numeric_requests(atom):
    assert compound_reporting_problem(atom) is None

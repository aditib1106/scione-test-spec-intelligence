"""A compound layer-output request cannot inherit one layer's reporting range."""

import pytest

from scione.parameters.engine import evaluate_pair
from scione.specifications.models import CustomerRequirement
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim
from scione.structured.normalization import known_source_fact_problem, normalize_requirement

RAW = "Record total dry film build and the separate topcoat thickness."
COMPOUND = "total dry film build and the separate topcoat thickness"


def source(text=COMPOUND, parameter="topcoat_thickness"):
    return AtomicClaim(
        id="native_report",
        parameter_id=parameter,
        label="Topcoat thickness" if parameter == "topcoat_thickness" else "Film thickness",
        kind="reporting",
        operator="required",
        text_value=text,
        raw_text=RAW,
        source_pages=[1],
    )


def row(*claims):
    return CustomerRequirement(
        id="req_layer",
        original_test_name="Thickness",
        atomic_claims=list(claims),
        evidence=[dict(page=1, quote=RAW)],
    )


@pytest.mark.parametrize("parameter", ["topcoat_thickness", "film_thickness"])
@pytest.mark.parametrize("state", ["parsed", "reviewed"])
def test_one_total_or_layer_range_cannot_certify_the_combined_request(parameter, state):
    request = source(parameter=parameter).model_copy(update={"normalization_state": state})
    declaration = AtomicClaim(
        id="lab_range",
        parameter_id=parameter,
        label=request.label,
        kind="reporting",
        operator="range",
        minimum=0,
        maximum=300,
        unit="µm",
        raw_text="Lab reporting range",
    )
    assert evaluate_pair(request, declaration).status == "unknown"
    normalized = normalize_requirement(row(request))
    assert normalized.claims[0].normalization_state == "unresolved"
    assert all(
        check.status != "supported"
        for check in compare_requirement(normalized, [declaration], "supported").checks[1:]
    )
    assert request.text_value == COMPOUND and request.raw_text == RAW


def test_shared_broad_raw_quote_does_not_quarantine_two_correctly_separated_properties():
    total = source("total dry film build", "film_thickness")
    topcoat = source("separate topcoat thickness")
    requirement = row(total, topcoat)
    before = requirement.model_dump_json()
    assert known_source_fact_problem(total, requirement) is None
    assert known_source_fact_problem(topcoat, requirement) is None
    normalized = normalize_requirement(requirement)
    assert all(c.normalization_state == "parsed" for c in normalized.claims)
    declarations = [
        AtomicClaim(
            id=f"lab_{i}",
            parameter_id=c.parameter_id,
            label=c.label,
            kind="reporting",
            operator="range",
            minimum=0,
            maximum=300,
            unit="µm",
            raw_text="Lab reporting range",
        )
        for i, c in enumerate([total, topcoat])
    ]
    assert all(
        c.status == "supported"
        for c in compare_requirement(normalized, declarations, "supported").checks[1:]
    )
    assert requirement.model_dump_json() == before

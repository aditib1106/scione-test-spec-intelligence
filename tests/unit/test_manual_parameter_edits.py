"""Human parameter edits need source numbers even for an unfamiliar property."""

import pytest

from scione.specifications.manual_atoms import validate_parameter_edits
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.models import AtomicClaim


def edited_source(raw, *, value, unit):
    return CustomerRequirement(
        id="req_manual",
        original_test_name="Customer-defined test",
        evidence=[RequirementEvidence(page=1, quote=raw)],
        atomic_claims=[
            AtomicClaim(
                id="human_atom",
                parameter_id="custom_glint",
                label="Glint index",
                operator="=",
                value=value,
                unit=unit,
                raw_text=raw,
                source_pages=[1],
            )
        ],
    )


@pytest.mark.parametrize(
    "raw,value,unit",
    [
        ("Use the documented conditioning procedure.", 999, "mm"),
        ("Temperature is 60 °C.", 60, "mm"),
        ("Glint index is 27 flarps.", 999, "flarps"),
        ("Use the documented conditioning procedure.", 999, None),
    ],
)
def test_new_numeric_edits_cannot_invent_a_number_or_unit(raw, value, unit):
    with pytest.raises(ValueError, match="not in this parameter's source quotation"):
        validate_parameter_edits(None, edited_source(raw, value=value, unit=unit))


@pytest.mark.parametrize(
    "raw,value,unit",
    [
        ("Glint index is 27 flarps.", 27, "flarps"),
        ("The customer glint index is 27.", 27, None),
        ("The measurement is 3 mm.", 3, "mm"),
    ],
)
def test_literal_custom_numbers_are_retained_without_certifying_their_identity(raw, value, unit):
    validate_parameter_edits(None, edited_source(raw, value=value, unit=unit))

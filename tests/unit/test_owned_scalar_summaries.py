"""Summary deduplication preserves native quantities and rejects novel scope."""

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim
from scione.structured.summary_coverage import represented_scalar_summary


def scenario(family):
    if family == "topcoat":
        summary = "Topcoat film thickness: 42-55 µm on each panel."
        native = AtomicClaim(
            id="n",
            parameter_id="topcoat_thickness",
            label="Topcoat thickness",
            kind="criterion",
            operator="range",
            minimum=42,
            maximum=55,
            unit="µm",
            source_context="separate topcoat thickness; on every panel",
            raw_text="The topcoat thickness must be 42-55 µm on every panel.",
            source_pages=[1],
        )
        atoms = [native]
        physical = "Topcoat film thickness            42-55 µm on each panel"
    elif family == "gloss":
        summary = "60° reflected gloss: 85-95 GU initial"
        native = AtomicClaim(
            id="n",
            parameter_id="gloss",
            label="Gloss",
            kind="criterion",
            stage="initial",
            operator="range",
            minimum=85,
            maximum=95,
            unit="GU",
            source_context="arithmetic mean; as delivered",
            raw_text="The arithmetic mean must be 85-95 GU as delivered.",
            source_pages=[1],
        )
        angle = AtomicClaim(
            id="angle",
            parameter_id="measurement_angle",
            label="Measurement angle",
            kind="condition",
            stage="measurement",
            operator="=",
            value=60,
            unit="°",
            raw_text="Use a 60° specular-gloss measurement.",
            source_pages=[1],
        )
        atoms = [native, angle]
        physical = "60° reflected gloss   85-95 GU initial; >=90% site retention"
    else:
        summary = ">=90% site retention"
        native = AtomicClaim(
            id="n",
            parameter_id="gloss_retention",
            label="Retention",
            kind="criterion",
            stage="after_cycle",
            operator=">=",
            value=90,
            unit="%",
            source_context="each location; of its own original value",
            raw_text=(
                "After cycling, each location must retain at least 90% of its own original value."
            ),
            source_pages=[1],
        )
        atoms = [native]
        physical = "60° reflected gloss   85-95 GU initial; >=90% site retention"
    row = CustomerRequirement(
        id="req_summary",
        original_test_name=family,
        atomic_claims=atoms,
        evidence=[*[dict(page=1, quote=a.raw_text) for a in atoms], dict(page=2, quote=physical)],
    )
    return summary, row, atoms


@pytest.mark.parametrize("family", ["topcoat", "gloss", "retention"])
def test_whole_closed_owned_summary_is_a_duplicate_not_another_capability(family):
    summary, row, atoms = scenario(family)
    before = row.model_dump_json()
    proof = represented_scalar_summary(summary, "criterion", row, atoms)
    assert proof and proof["retained_claim_ids"]
    assert row.model_dump_json() == before


@pytest.mark.parametrize("family", ["topcoat", "gloss", "retention"])
@pytest.mark.parametrize(
    "change",
    [
        "wrong_scalar",
        "wrong_unit",
        "wrong_phase",
        "extra_scope",
        "unowned_summary",
        "unowned_native",
    ],
)
def test_changed_science_scope_phase_or_owner_cannot_be_hidden_by_summary_dedup(family, change):
    summary, row, atoms = scenario(family)
    if change == "wrong_scalar":
        if atoms[0].value is not None:
            atoms[0].value += 1
        else:
            atoms[0].maximum += 1
    elif change == "wrong_unit":
        atoms[0].unit = "nm"
    elif change == "wrong_phase":
        atoms[0].stage = "exposure"
    elif change == "extra_scope":
        summary += " except aluminium"
    elif change == "unowned_summary":
        row.evidence = row.evidence[:-1]
    else:
        atoms[0].source_pages = [3]
    assert represented_scalar_summary(summary, "criterion", row, atoms) is None


def test_same_property_in_two_phases_is_not_a_unique_summary_owner():
    summary, row, atoms = scenario("gloss")
    atoms.append(atoms[0].model_copy(update={"id": "other", "stage": "after_cycle"}))
    assert represented_scalar_summary(summary, "criterion", row, atoms) is None

"""Complete source-bound allocation prose is planning, not missing machine capacity."""

from types import SimpleNamespace

import pytest

from scione.matching.workflow_roles import compound_plan_review_issues, compound_sample_plan_proofs
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.models import AtomicClaim, NormalizedRequirement
from scione.structured.planning import planning_quantities


def source_case():
    quote = "assign two to baseline checks, four to thermal cycling and one to reserve samples"
    atom = AtomicClaim(
        id="allocation", parameter_id="panel_allocation", label="Panel allocation",
        kind="condition", operator="text", text_value=quote, raw_text=quote,
        source_context="per lot", source_pages=[1],
    )
    req = CustomerRequirement(
        id="req_plan", original_test_name="Coating assessment",
        atomic_claims=[atom], evidence=[RequirementEvidence(page=1, quote=quote + ".")],
    )
    proposals = [
        AtomicClaim(
            id=f"field_{index}", kind="instruction", operator="=", raw_text=quote + ".",
            source_pages=[1], **item,
        ).model_dump(mode="json")
        for index, item in enumerate(planning_quantities(quote))
    ]
    audit = {
        "rule": "native_source_clause_audit", "audit_id": "plan_audit",
        "source_clause": quote + ".", "source_pages": [1],
        "issues": ["Unrepresented source quantity: 2."], "parser_proposals": proposals,
    }
    normalized = NormalizedRequirement(
        requirement_id=req.id, test_name=req.original_test_name, claims=[atom],
        review_issues=[
            "req_plan plan_audit: Unrepresented source quantity: 2.",
            "req_plan angle_audit: Unrepresented source quantity: 60 °.",
        ], normalization_actions=[audit],
    )
    return SimpleNamespace(requirement=req, normalized=normalized)


def test_full_prose_preserves_every_allocation_and_resolves_only_its_own_count_audit():
    source = source_case()
    before = source.normalized.model_dump(mode="json")
    proof = compound_sample_plan_proofs(source)
    assert [row["value"] for row in proof["allocation"]["allocations"]] == ["2", "4", "1"]
    assert proof["allocation"]["establishes_lab_support"] is False
    assert compound_plan_review_issues(source, proof) == {
        "req_plan plan_audit: Unrepresented source quantity: 2."
    }
    assert source.normalized.model_dump(mode="json") == before


@pytest.mark.parametrize("change", ["changed_value", "mixed_execution", "unlocated", "capacity"])
def test_partial_or_changed_or_operating_clauses_cannot_become_sample_planning(change):
    source = source_case()
    atom = source.normalized.claims[0]
    if change == "changed_value":
        atom.text_value = atom.text_value.replace("two", "three")
    elif change == "mixed_execution":
        atom.raw_text += " and hold the chamber at70°C"
        atom.text_value = atom.raw_text
    elif change == "unlocated":
        atom.source_pages = [2]
    else:
        atom.raw_text = "assign two to simultaneous chamber load and four to thermal cycling"
        atom.text_value = atom.raw_text
        source.requirement.evidence[0].quote = atom.raw_text
    assert not compound_sample_plan_proofs(source)


@pytest.mark.parametrize("change", ["changed_parser_value", "unitful_issue", "other_issue"])
def test_unproved_quantity_or_scientific_warning_is_never_suppressed(change):
    source = source_case()
    audit = source.normalized.normalization_actions[0]
    if change == "changed_parser_value":
        audit["parser_proposals"][0]["value"] = "3"
    elif change == "unitful_issue":
        audit["issues"] = ["Unrepresented source quantity: 2 mm."]
    else:
        audit["issues"].append("The temperature is inconsistent.")
    assert not compound_plan_review_issues(source, compound_sample_plan_proofs(source))

"""A physical named-scale proof augments a copy and remains auditable."""

from copy import deepcopy

import pytest
from test_named_scale_units import BODY, PARENT, ROW, document, requirement

from scione.matching.projection import _retains_source_fact
from scione.specifications.models import RequirementEvidence, RequirementValue
from scione.specifications.named_scale_units import physical_named_scale_unit_evidence
from scione.structured.normalization import known_source_fact_problem, normalize_requirement


def proven_source(unit=None):
    source = requirement()
    doc = document(PARENT + "\n" + ROW)
    native = source.atomic_claims[0]
    proof = physical_named_scale_unit_evidence(native, source, doc, requirements=[source])
    source.evidence.append(RequirementEvidence(page=1, quote=proof["source_quote"]))
    if unit:
        source.atomic_claims[0].unit = unit
        proof["source"] = unit
    return source, proof


@pytest.mark.parametrize("unit", [None, "ΔE*ab"])
def test_physical_named_scale_copy_preserves_native_fact_and_downstream_guard(unit):
    source, proof = proven_source(unit)
    before = deepcopy(source.model_dump(mode="json"))
    result = normalize_requirement(source, source_unit_proofs={"native_colour": proof})
    same = [row for row in result.claims if row.raw_text == BODY]
    assert same and same[0].unit == "ΔE*ab"
    assert same[0].normalization_state == "parsed"
    assert known_source_fact_problem(same[0], source) is None
    assert source.model_dump(mode="json") == before
    assert proof in result.normalization_actions
    assert _retains_source_fact(
        source.atomic_claims[0], same[0], source, result.normalization_actions
    )


@pytest.mark.parametrize(
    "change",
    [
        {"source_claim_id": "other_claim"},
        {"requirement_id": "other_test"},
        {"value": "999"},
        {"operator": ">="},
        {"canonical": "ΔE00"},
        {"source_quote_sha256": "f" * 64},
        {"document_sha256": "not a digest"},
        {"body_quote": "An unrelated colour metric."},
        {"source_page": 2},
    ],
)
def test_stale_or_foreign_unit_proof_cannot_approve_a_source_quantity(change):
    source, proof = proven_source()
    proof.update(change)
    result = normalize_requirement(source, source_unit_proofs={"native_colour": proof})
    assert not any(
        a.get("rule") == "source_named_scale_summary_unit" for a in result.normalization_actions
    )
    native = next(row for row in result.claims if row.raw_text == BODY)
    assert native.unit is None
    assert native.normalization_state == "unresolved"


def test_no_parent_owned_unit_citation_means_no_projection_relaxation():
    source, proof = proven_source()
    result = normalize_requirement(source, source_unit_proofs={"native_colour": proof})
    normalized = next(row for row in result.claims if row.raw_text == BODY)
    source.evidence = source.evidence[:1]
    assert not _retains_source_fact(
        source.atomic_claims[0], normalized, source, result.normalization_actions
    )


def test_only_the_entire_strict_physically_proved_scalar_summary_row_is_deduplicated():
    source, proof = proven_source()
    source.acceptance_criteria = [RequirementValue(name="Summary", raw_value=ROW)]
    normalized = normalize_requirement(source, source_unit_proofs={"native_colour": proof})
    assert len(normalized.claims) == 1
    assert normalized.claims[0].raw_text == BODY
    source.acceptance_criteria = [
        RequirementValue(name="Summary", raw_value=ROW + " except aluminium")
    ]
    retained = normalize_requirement(source, source_unit_proofs={"native_colour": proof})
    assert any("aluminium" in c.raw_text for c in retained.claims)

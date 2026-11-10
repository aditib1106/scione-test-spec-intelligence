"""Lossless duplicate removal for model input, with full saved source artifacts."""

import json
from decimal import Decimal

import pytest

from scione.catalogs.models import CanonicalTest, LabCapability, ReferenceCatalog
from scione.matching.models import (
    CapabilityCandidate,
    MatchingInput,
    RequirementCandidates,
)
from scione.matching.projection import MatchingProjectionError, project_matching_input
from scione.matching.service import (
    SemanticMatchingError,
    SemanticMatchingRunner,
    input_fingerprint,
    prepare_matching_request,
)
from scione.providers.static import StaticModelProvider
from scione.specifications.models import CustomerRequirement, RequirementEvidence, RequirementValue
from scione.structured.models import AtomicClaim, NormalizedRequirement


def snapshot():
    claim = AtomicClaim(
        id="normalized_atom",
        parameter_id="temperature",
        label="Temperature",
        kind="condition",
        stage="general",
        operator="=",
        value=60,
        unit="°C",
        raw_text="Temperature 60 °C",
        source_pages=[1],
    )
    declared = claim.model_copy(update={"id": "lab_atom", "source_pages": []}, deep=True)
    return MatchingInput(
        lab_id="lab_one",
        extraction_run_id="extract_one",
        catalog_revision="catalog_one",
        capability_revision="lab_revision_one",
        catalog=ReferenceCatalog(
            id="catalog",
            name="Catalog",
            version=1,
            source_note="Synthetic test data",
            tests=[CanonicalTest(id="test_one", name="Test one", description="Test one")],
            methods=[],
        ),
        capabilities=[
            CapabilityCandidate(
                capability=LabCapability(
                    id="cap_one",
                    canonical_test_id="test_one",
                    atomic_claims=[declared],
                    declared_constraints="Temperature 60 °C",
                    notes="Retain source note",
                ),
                claims=[declared.model_copy(deep=True)],
            )
        ],
        requirements=[
            RequirementCandidates(
                requirement=CustomerRequirement(
                    id="req_one",
                    original_test_name="Test one",
                    conditions=[RequirementValue(name="Condition", raw_value="Temperature 60 °C")],
                    evidence=[RequirementEvidence(page=1, quote="Temperature 60 °C")],
                    atomic_claims=[claim.model_copy(update={"id": "source_atom"}, deep=True)],
                ),
                normalized=NormalizedRequirement(
                    requirement_id="req_one",
                    test_name="Test one",
                    claims=[claim],
                ),
                catalog_test_ids=["test_one"],
                capability_ids=["cap_one"],
            )
        ],
    )


def test_projection_removes_only_duplicate_fact_rows_and_retains_authoritative_ids():
    original = snapshot()
    before = original.model_dump(mode="json")
    projected = project_matching_input(original)
    assert original.model_dump(mode="json") == before
    assert not projected.requirements[0].requirement.atomic_claims
    assert not projected.capabilities[0].capability.atomic_claims
    assert projected.requirements[0].normalized == original.requirements[0].normalized
    assert projected.capabilities[0].claims == original.capabilities[0].claims
    assert (
        projected.requirements[0].requirement.evidence
        == original.requirements[0].requirement.evidence
    )
    assert (
        projected.requirements[0].requirement.conditions
        == original.requirements[0].requirement.conditions
    )
    assert projected.capabilities[0].capability.notes == "Retain source note"
    assert projected.catalog == original.catalog
    assert len(projected.model_dump_json()) < len(original.model_dump_json())
    projected.requirements[0].normalized.claims[0].value = 999
    assert original.requirements[0].normalized.claims[0].value == 60


def test_projection_omits_software_audit_logs_but_keeps_all_facts_and_original_proofs():
    original = snapshot()
    action = {"rule": "native_source_fact_coverage", "source_note": "Long parser audit " * 100}
    original.requirements[0].normalized.normalization_actions = [action]
    projected = project_matching_input(original)
    assert projected.requirements[0].normalized.normalization_actions == []
    assert projected.requirements[0].normalized.claims == original.requirements[0].normalized.claims
    assert original.requirements[0].normalized.normalization_actions == [action]


def test_projection_retains_all_distinct_facts_in_the_authoritative_view():
    original = snapshot()
    atom = original.requirements[0].requirement.atomic_claims[0]
    variants = [
        atom.model_copy(update={"id": "context_atom", "source_context": "Water"}),
        atom.model_copy(update={"id": "value_atom", "value": Decimal(80)}),
        atom.model_copy(update={"id": "stage_atom", "stage": "measurement"}),
        atom.model_copy(update={"id": "role_atom", "kind": "criterion"}),
        atom.model_copy(update={"id": "state_atom", "normalization_state": "unresolved"}),
    ]
    original.requirements[0].requirement.atomic_claims.extend(variants)
    original.requirements[0].normalized.claims.extend(
        atom.model_copy(update={"id": f"normalized_{atom.id}"}, deep=True) for atom in variants
    )
    lab = original.capabilities[0].capability.atomic_claims[0]
    configured = lab.model_copy(update={"id": "other_configuration", "configuration": "route_two"})
    original.capabilities[0].capability.atomic_claims.append(configured)
    original.capabilities[0].claims.append(configured.model_copy(deep=True))
    projected = project_matching_input(original)
    assert not projected.requirements[0].requirement.atomic_claims
    assert not projected.capabilities[0].capability.atomic_claims
    assert projected.requirements[0].normalized == original.requirements[0].normalized
    assert projected.capabilities[0].claims == original.capabilities[0].claims


@pytest.mark.parametrize(
    "changes",
    [
        {"value": Decimal(80)},
        {"source_context": "Water"},
        {"stage": "measurement"},
        {"kind": "criterion"},
        {"source_pages": [2]},
        {"raw_text": "Another source fragment"},
    ],
)
def test_projection_refuses_an_external_snapshot_that_loses_a_source_fact(changes):
    original = snapshot()
    original.requirements[0].requirement.atomic_claims[0] = (
        original.requirements[0].requirement.atomic_claims[0].model_copy(update=changes)
    )
    with pytest.raises(MatchingProjectionError, match="does not retain a source atom"):
        project_matching_input(original)


def test_grounding_identity_downgrade_does_not_reintroduce_competing_source_ids():
    original = snapshot()
    normalized = original.requirements[0].normalized.claims[0]
    original.requirements[0].normalized.claims[0] = normalized.model_copy(
        update={
            "parameter_id": None,
            "label": "Unresolved source",
            "normalization_state": "unresolved",
            "availability": "unknown",
            "stage": "measurement",
            "source_context": "Water",
        }
    )
    projected = project_matching_input(original)
    assert not projected.requirements[0].requirement.atomic_claims
    assert projected.requirements[0].normalized.claims[0].normalization_state == "unresolved"
    assert projected.requirements[0].normalized.claims[0].raw_text == "Temperature 60 °C"


def test_projection_roundtrips_matching_input_and_is_idempotent():
    projected = project_matching_input(snapshot())
    assert MatchingInput.model_validate_json(projected.model_dump_json()) == projected
    assert project_matching_input(projected) == projected


@pytest.mark.parametrize("keyed", [False, True])
def test_compact_wire_roundtrips_all_facts_without_changing_joint_scope_strings(keyed):
    original = snapshot()
    original.catalog.synthetic = False
    original.requirements[0].requirement.atomic_claims[0].value = Decimal(0)
    original.requirements[0].normalized.claims[0].value = Decimal(0)
    scope = AtomicClaim(
        id="scope_atom", parameter_id="specimen_material", label="Specimen material",
        kind="scope", operator="text", text_value="steel  panel\nwith coating",
        source_context="Painted  substrate", raw_text="Use a steel  panel\nwith coating.",
    )
    original.requirements[0].requirement.atomic_claims.append(scope.model_copy(deep=True))
    original.requirements[0].normalized.claims.append(scope.model_copy(deep=True))
    before = original.model_dump(mode="json")
    projected = project_matching_input(original)
    request = prepare_matching_request(original, keyed=keyed)
    payload = json.loads(request.user_prompt)
    native_ids = payload.pop("native_source_claim_ids")
    payload.pop("project_obligation_ids")
    assert set(native_ids["req_one"]) == {
        claim.id for claim in original.requirements[0].requirement.atomic_claims
    }
    assert "joint_scope_texts" not in payload
    assert MatchingInput.model_validate(payload) == projected
    assert payload["catalog"]["synthetic"] is False
    atom = payload["requirements"][0]["normalized"]["claims"][0]
    assert atom["value"] == "0"
    assert atom["values"] == []
    assert "minimum" not in atom
    assert original.model_dump(mode="json") == before
    ordinary = json.dumps(projected.model_dump(mode="json"), ensure_ascii=False)
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert len(compact) < len(ordinary)


def test_projection_preflight_failure_is_retained_without_calling_a_provider():
    class NoCallProvider(StaticModelProvider):
        def generate_structured(self, request):
            raise AssertionError("An inconsistent source must fail before inference.")

    original = snapshot()
    original.requirements[0].normalized.claims = []
    with pytest.raises(SemanticMatchingError) as caught:
        SemanticMatchingRunner(NoCallProvider({})).run(original)
    attempt = caught.value.run
    assert attempt.status == "failed"
    assert attempt.error_code == "input_projection_error"
    assert attempt.usage.total_tokens is None
    assert attempt.input_snapshot == original
    assert attempt.request.user_prompt


def test_runner_transmits_projection_and_saves_exact_full_snapshot_and_fingerprint():
    original = snapshot()
    untouched = original.model_dump(mode="json")
    provider = StaticModelProvider(
        {
            "schema_version": "0.1",
            "review_issues": [],
            "items": [
                {
                    "requirement_id": "req_one",
                    "identity_status": "aligned",
                    "canonical_test_id": "test_one",
                    "capability_id": "cap_one",
                    "reason": "Same test",
                    "claim_alignments": [
                        {
                            "requirement_claim_id": "normalized_atom",
                            "capability_claim_ids": ["lab_atom"],
                            "relation": "same_parameter",
                            "reason": "Same temperature",
                        }
                    ],
                }
            ],
        }
    )
    attempt = SemanticMatchingRunner(provider).run(original)
    wire_payload = json.loads(attempt.request.user_prompt)
    wire_payload.pop("native_source_claim_ids")
    wire_payload.pop("project_obligation_ids")
    wire = MatchingInput.model_validate(wire_payload)
    assert not wire.requirements[0].requirement.atomic_claims
    assert not wire.capabilities[0].capability.atomic_claims
    assert attempt.input_snapshot.model_dump(mode="json") == untouched
    assert original.model_dump(mode="json") == untouched
    assert attempt.input_fingerprint == input_fingerprint(original)
    assert attempt.output.items[0].claim_alignments[0].capability_claim_ids == ["lab_atom"]


def test_projection_retains_normalization_metadata_review_and_original_source_context():
    source = snapshot()
    row = source.requirements[0]
    row.requirement.atomic_claims[0].source_context = "Unconfirmed aluminium configuration"
    row.normalized.claims[0].source_context = row.requirement.atomic_claims[0].source_context
    row.normalized.review_issues = ["Unique numeric source occurrence; metadata is unconfirmed."]
    projected = project_matching_input(source)
    assert projected.requirements[0].normalized.review_issues == row.normalized.review_issues
    assert projected.requirements[0].normalized.claims[0].source_context == (
        row.requirement.atomic_claims[0].source_context
    )


@pytest.mark.parametrize("valid", [True, False])
def test_projection_accepts_only_source_proved_exposure_to_cycle_stage(valid):
    original = snapshot()
    row = original.requirements[0]
    raw = "Execute five cycles between -35 °C for 3 h and 80 °C for 2 h."
    source = row.requirement.atomic_claims[0].model_copy(update={
        "raw_text": raw, "stage": "exposure", "value": Decimal(-35), "sequence": 1,
    })
    row.requirement.atomic_claims = [source]
    row.requirement.evidence = [
        RequirementEvidence(page=1, quote=raw if valid else "Another cycle.")
    ]
    row.normalized.claims = [
        source.model_copy(update={"id": "normalized", "stage": "thermal_cycle"})
    ]
    if valid:
        assert project_matching_input(original).requirements[0].normalized.claims[0].value == -35
    else:
        with pytest.raises(MatchingProjectionError):
            project_matching_input(original)

"""Grounded semantic proposals, coverage, context guards and retained attempts."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from scione.catalogs.models import (
    CanonicalTest,
    LabCapability,
    LabProfile,
    ReferenceCatalog,
    TestMethod,
)
from scione.matching import (
    CapabilityCandidate,
    MatchingInput,
    RequirementCandidates,
    SemanticMatchingError,
    SemanticMatchingRunner,
    build_matching_input,
    compile_matching_run,
    input_fingerprint,
)
from scione.matching.service import matching_output_schema
from scione.providers.base import ProviderError, StructuredGenerationResponse, TokenUsage
from scione.providers.static import StaticModelProvider
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured.models import AtomicClaim, NormalizedRequirement


class RecordingProvider(StaticModelProvider):
    def __init__(self, payload, *, raw_text=None, failure=None):
        super().__init__(payload)
        self.requests = []
        self.raw_text = raw_text
        self.failure = failure

    def generate_structured(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        response = super().generate_structured(request)
        return StructuredGenerationResponse(
            **response.model_dump(exclude={"raw_text", "provider_request_id", "usage"}),
            raw_text=self.raw_text if self.raw_text is not None else response.raw_text,
            provider_request_id="provider_call_1",
            usage=TokenUsage(input_tokens=100, output_tokens=30, total_tokens=130),
        )


def claim(identifier, **changes):
    values = {
        "id": identifier,
        "parameter_id": "temperature",
        "label": "Temperature",
        "stage": "measurement",
        "operator": "=",
        "value": Decimal("60"),
        "unit": "°C",
        "raw_text": "Water temperature 60 °C",
    }
    values.update(changes)
    return AtomicClaim(**values)


def source_requirement():
    return CustomerRequirement(
        id="req_001",
        original_test_name="Pressure water jet adhesion",
        conditions=[RequirementValue(name="Condition", raw_value="Water temperature 60 °C")],
        evidence=[RequirementEvidence(page=1, quote="Water temperature 60 °C")],
    )


def test_semantic_stage_context_uses_same_synonyms_as_numeric_comparator():
    requested = claim("req_claim_1", stage="curing", source_context="curing")
    declared = claim("lab_claim_1", stage="curing", source_context="Cure")
    run = SemanticMatchingRunner(RecordingProvider(proposal())).run(
        snapshot(requested=[requested], declared=[declared])
    )
    assert run.output.items[0].claim_alignments[0].relation == "same_parameter"


def snapshot(*, requested=None, declared=None):
    requested = [claim("req_claim_1")] if requested is None else requested
    declared = [claim("lab_claim_1")] if declared is None else declared
    catalog = ReferenceCatalog(
        id="test_catalog",
        name="Test catalog",
        version=1,
        source_note="Synthetic isolated test data",
        tests=[
            CanonicalTest(
                id="water_jet",
                name="Water jet",
                description="Water jet adhesion",
                method_ids=["iso_16925"],
            ),
            CanonicalTest(id="other_test", name="Other", description="Another test"),
        ],
        methods=[TestMethod(id="iso_16925", identifier="ISO 16925", name="Water jet")],
    )
    capability = LabCapability(
        id="cap_water_jet",
        canonical_test_id="water_jet",
        method_ids=["iso_16925"],
        declared_constraints="Water temperature 60 °C",
    )
    return MatchingInput(
        lab_id="lab_one",
        extraction_run_id="extraction_one",
        catalog_revision="catalog_revision_1",
        capability_revision="capability_revision_1",
        catalog=catalog,
        capabilities=[CapabilityCandidate(capability=capability, claims=declared)],
        requirements=[
            RequirementCandidates(
                requirement=source_requirement(),
                normalized=NormalizedRequirement(
                    requirement_id="req_001",
                    test_name="Water jet",
                    claims=requested,
                ),
                catalog_test_ids=["water_jet"],
                capability_ids=["cap_water_jet"],
            )
        ],
    )


def proposal(*, requested_ids=None, declared_ids=None):
    requested_ids = ["req_claim_1"] if requested_ids is None else requested_ids
    declared_ids = ["lab_claim_1"] if declared_ids is None else declared_ids
    return {
        "schema_version": "0.1",
        "items": [
            {
                "requirement_id": "req_001",
                "identity_status": "aligned",
                "canonical_test_id": "water_jet",
                "capability_id": "cap_water_jet",
                "reason": "The source name identifies the catalog test.",
                "claim_alignments": [
                    {
                        "requirement_claim_id": identifier,
                        "capability_claim_ids": declared_ids,
                        "relation": "same_parameter" if declared_ids else "unknown",
                        "reason": "Both declarations refer to water temperature."
                        if declared_ids
                        else "No corresponding lab declaration was supplied.",
                    }
                    for identifier in requested_ids
                ],
            }
        ],
        "review_issues": [],
    }


def test_success_is_one_explicit_call_with_snapshot_prompt_usage_and_raw_response():
    source = snapshot()
    untouched = source.model_dump(mode="json")
    raw = "  \n" + json.dumps(proposal()) + "\n "
    provider = RecordingProvider(proposal(), raw_text=raw)
    result = SemanticMatchingRunner(provider).run(source)
    assert len(provider.requests) == 1
    assert result.status == "completed"
    assert result.raw_response == raw
    assert result.provider_request_id == "provider_call_1"
    assert result.usage.total_tokens == 130
    assert result.input_snapshot.model_dump(mode="json") == untouched
    assert result.proposal == result.output
    assert not result.validation_issues
    assert result.input_fingerprint == input_fingerprint(source)
    assert result.request.schema_name == "semantic_matching_v0_1"
    assert "untrusted source" in result.request.system_prompt
    assert "supported" not in result.output.model_dump_json()
    assert "value" not in result.output.model_dump_json()
    source.requirements[0].normalized.claims[0].value = Decimal(999)
    assert result.input_snapshot.requirements[0].normalized.claims[0].value == 60


def test_semantic_alias_alignment_does_not_compare_or_overwrite_numbers():
    source = snapshot(
        requested=[claim("req_claim_1", parameter_id="water_temperatur", value=Decimal(60))],
        declared=[claim("lab_claim_1", parameter_id="temperature", value=Decimal(30))],
    )
    result = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    assert result.output.items[0].claim_alignments[0].relation == "same_parameter"
    assert result.input_snapshot.requirements[0].normalized.claims[0].value == 60
    assert result.input_snapshot.capabilities[0].claims[0].value == 30
    assert (
        result.input_snapshot.requirements[0].normalized.claims[0].parameter_id
        == "water_temperatur"
    )


def test_criterion_can_align_to_reportable_output_without_predicting_specimen_pass():
    source = snapshot(
        requested=[
            claim(
                "req_claim_1",
                parameter_id="color_difference",
                kind="criterion",
                stage="initial",
                operator="<=",
                value=Decimal(1),
                unit="ΔE*ab",
            )
        ],
        declared=[
            claim(
                "lab_claim_1",
                parameter_id="color_difference",
                kind="reporting",
                stage="general",
                operator="range",
                value=None,
                minimum=Decimal(0),
                maximum=Decimal(20),
                unit="ΔE*ab",
            )
        ],
    )
    result = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    assert result.output.items[0].claim_alignments[0].relation == "same_parameter"
    assert result.validation_issues == []


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "reporting"},
        {"stage": "curing"},
        {"sequence": 2},
        {"unit": "cm"},
        {"parameter_id": "duration"},
        {"source_context": "On aluminium only"},
    ],
)
def test_explicit_context_conflicts_become_unknown_without_losing_machine_proposal(updates):
    source = snapshot(declared=[claim("lab_claim_1", **updates)])
    result = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    assert result.proposal.items[0].claim_alignments[0].relation == "same_parameter"
    accepted = result.output.items[0].claim_alignments[0]
    assert accepted.relation == "unknown"
    assert accepted.capability_claim_ids == []
    assert result.validation_issues[0].code == "incompatible_claim_context"


def test_requested_specific_context_is_not_supplied_by_a_generic_lab_value():
    source = snapshot(requested=[claim("req_claim_1", source_context="On aluminium only")])
    result = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    assert result.output.items[0].claim_alignments[0].relation == "unknown"


def test_matching_source_context_is_preserved_and_can_align():
    source = snapshot(
        requested=[claim("req_claim_1", source_context=" On aluminium only ")],
        declared=[claim("lab_claim_1", source_context="On aluminium only")],
    )
    result = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    assert result.output.items[0].claim_alignments[0].relation == "same_parameter"


def test_different_configurations_cannot_be_combined_into_a_supported_setup():
    source = snapshot(
        requested=[
            claim("req_claim_1"),
            claim("req_claim_2", parameter_id="duration", value=Decimal(60), unit="s"),
        ],
        declared=[
            claim("lab_claim_1", configuration="setup_a"),
            claim(
                "lab_claim_2",
                configuration="setup_b",
                parameter_id="duration",
                value=Decimal(60),
                unit="s",
            ),
        ],
    )
    payload = proposal(requested_ids=["req_claim_1", "req_claim_2"])
    payload["items"][0]["claim_alignments"][1]["capability_claim_ids"] = ["lab_claim_2"]
    result = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    assert all(row.relation == "unknown" for row in result.output.items[0].claim_alignments)
    assert len(result.validation_issues) == 2


def test_explicit_unavailable_lab_claim_can_align_but_is_never_changed_to_available():
    source = snapshot(declared=[claim("lab_claim_1", availability="unsupported")])
    result = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    assert result.output.items[0].claim_alignments[0].relation == "same_parameter"
    assert result.input_snapshot.capabilities[0].claims[0].availability == "unsupported"


def test_known_test_with_no_lab_declaration_has_only_unknown_claim_alignment():
    source = snapshot()
    source.capabilities = []
    source.requirements[0].capability_ids = []
    payload = proposal(declared_ids=[])
    payload["items"][0]["capability_id"] = None
    result = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    assert result.output.items[0].canonical_test_id == "water_jet"
    assert result.output.items[0].capability_id is None
    assert result.output.items[0].claim_alignments[0].relation == "unknown"


@pytest.mark.parametrize(
    "change",
    [
        "missing_item",
        "duplicate_item",
        "missing_claim",
        "duplicate_claim",
        "unknown_claim",
        "unknown_lab_claim",
        "unknown_test",
        "out_of_candidates",
        "unknown_capability",
    ],
)
def test_missing_or_forged_references_reject_entire_output_and_retain_attempt(change):
    payload = proposal()
    item = payload["items"][0]
    alignment = item["claim_alignments"][0]
    if change == "missing_item":
        payload["items"] = []
    elif change == "duplicate_item":
        payload["items"].append(dict(item))
    elif change == "missing_claim":
        item["claim_alignments"] = []
    elif change == "duplicate_claim":
        item["claim_alignments"].append(dict(alignment))
    elif change == "unknown_claim":
        alignment["requirement_claim_id"] = "req_from_some_other_run"
    elif change == "unknown_lab_claim":
        alignment["capability_claim_ids"] = ["lab_from_some_other_lab"]
    elif change == "unknown_test":
        item["canonical_test_id"] = "forged_test"
    elif change == "out_of_candidates":
        item["canonical_test_id"] = "other_test"
    elif change == "unknown_capability":
        item["capability_id"] = "cap_from_other_lab"
    provider = RecordingProvider(payload)
    with pytest.raises(SemanticMatchingError) as error:
        SemanticMatchingRunner(provider).run(snapshot())
    attempt = error.value.run
    assert len(provider.requests) == 1
    assert attempt.error_code == "invalid_output_references"
    assert attempt.status == "failed"
    assert attempt.output is None
    assert attempt.raw_response == json.dumps(payload, ensure_ascii=False)
    assert attempt.proposal is not None
    assert attempt.usage.total_tokens == 130


@pytest.mark.parametrize("field,value", [("supported", True), ("value", 30), ("confidence", 0.99)])
def test_model_cannot_add_numeric_facts_support_verdict_or_confidence(field, value):
    payload = proposal()
    payload["items"][0]["claim_alignments"][0][field] = value
    with pytest.raises(SemanticMatchingError) as error:
        SemanticMatchingRunner(RecordingProvider(payload)).run(snapshot())
    assert error.value.run.error_code == "invalid_output_schema"
    assert error.value.run.output is None
    assert error.value.run.parsed_response == payload


def test_input_limit_rejects_before_any_model_call_and_preserves_full_input():
    provider = RecordingProvider(proposal())
    with pytest.raises(SemanticMatchingError) as error:
        SemanticMatchingRunner(provider, max_input_characters=1).run(snapshot())
    assert provider.requests == []
    assert error.value.run.error_code == "input_limit_exceeded"
    assert error.value.run.input_snapshot == snapshot()
    assert "No model call" in str(error.value)


def test_provider_error_is_not_retried_or_copied_into_user_error_or_saved_fields():
    provider = RecordingProvider(proposal(), failure=ProviderError("Authorization: Bearer SECRET"))
    with pytest.raises(SemanticMatchingError) as error:
        SemanticMatchingRunner(provider).run(snapshot())
    assert len(provider.requests) == 1
    assert error.value.run.error_code == "provider_error"
    assert error.value.run.raw_response is None
    assert "SECRET" not in str(error.value)
    assert "SECRET" not in error.value.run.model_dump_json()


def test_fingerprint_includes_claims_and_exact_input_revisions():
    source = snapshot()
    before = input_fingerprint(source)
    source.capability_revision = "capability_revision_2"
    assert input_fingerprint(source) != before
    source = snapshot()
    source.requirements[0].normalized.claims[0].value = Decimal(61)
    assert input_fingerprint(source) != before
    source = snapshot()
    source.review_revision = "human_review_2"
    assert input_fingerprint(source) != before


def extraction_run():
    return CustomerExtractionRun(
        run_id="extraction_one",
        started_at=datetime.now(UTC),
        document_id="source_one",
        prompt_version="test",
        schema_version="0.1",
        provider="static",
        model="test",
        latency_ms=1,
        usage=TokenUsage(),
        raw_response="{}",
        extraction=CustomerExtraction(
            schema_version="0.1",
            document=CustomerDocumentMetadata(),
            requirements=[source_requirement()],
        ),
    )


def test_factory_uses_supplied_candidates_without_silent_truncation_and_detaches_input():
    source = snapshot()
    lab = LabProfile(id="lab_one", name="Lab one", capabilities=[source.capabilities[0].capability])
    run = extraction_run()
    built = build_matching_input(
        run,
        source.catalog,
        lab,
        capability_revision="sql_revision_1",
        candidate_test_ids={"req_001": ["water_jet"]},
    )
    assert built.extraction_run_id == run.run_id
    assert built.capability_revision == "sql_revision_1"
    assert [test.id for test in built.catalog.tests] == ["water_jet"]
    assert [method.id for method in built.catalog.methods] == ["iso_16925"]
    assert built.requirements[0].catalog_test_ids == ["water_jet"]
    assert built.capabilities[0].claims
    run.extraction.requirements[0].original_test_name = "Changed after snapshot"
    assert built.requirements[0].requirement.original_test_name == "Pressure water jet adhesion"


def test_factory_requires_complete_candidate_and_normalized_input_maps():
    source = snapshot()
    lab = LabProfile(id="lab_one", name="Lab one")
    for options in ({"candidate_test_ids": {}}, {"normalized_requirements": []}):
        with pytest.raises(ValueError):
            build_matching_input(
                extraction_run(),
                source.catalog,
                lab,
                capability_revision="sql_1",
                **options,
            )


def test_invalid_candidate_relationship_rejected_before_provider_call():
    data = snapshot().model_dump(mode="json")
    data["requirements"][0]["catalog_test_ids"] = ["other_test"]
    with pytest.raises(ValidationError, match="Capability candidates"):
        MatchingInput.model_validate(data)


def test_all_output_fields_required_in_provider_schema():
    provider = RecordingProvider(proposal())
    SemanticMatchingRunner(provider).run(snapshot())
    schema = provider.requests[0].json_schema
    for model in [schema, *schema["$defs"].values()]:
        if "properties" in model:
            assert set(model["required"]) == set(model["properties"])
            assert all("default" not in value for value in model["properties"].values())


def test_wire_schema_encodes_the_same_cross_field_rules_as_runtime_validation():
    schema = matching_output_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid(proposal())

    known_without_lab = proposal(declared_ids=[])
    known_without_lab["items"][0]["capability_id"] = None
    assert validator.is_valid(known_without_lab)

    for status in ("unknown", "ambiguous"):
        unresolved = deepcopy(known_without_lab)
        unresolved["items"][0].update(identity_status=status, canonical_test_id=None)
        unresolved["items"][0]["claim_alignments"][0]["relation"] = status
        assert validator.is_valid(unresolved)


@pytest.mark.parametrize(
    "change",
    [
        "empty_aligned_claim",
        "unknown_claim_with_selected_ids",
        "ambiguous_claim_with_selected_ids",
        "aligned_identity_without_test",
        "unknown_identity_with_test",
        "ambiguous_identity_with_capability",
        "selected_claim_without_capability",
    ],
)
def test_wire_schema_rejects_cross_field_contradictions_before_generation(change):
    payload = proposal()
    item = payload["items"][0]
    alignment = item["claim_alignments"][0]
    if change == "empty_aligned_claim":
        alignment["capability_claim_ids"] = []
    elif change == "unknown_claim_with_selected_ids":
        alignment["relation"] = "unknown"
    elif change == "ambiguous_claim_with_selected_ids":
        alignment["relation"] = "ambiguous"
    elif change == "aligned_identity_without_test":
        item["canonical_test_id"] = None
    elif change == "unknown_identity_with_test":
        item.update(identity_status="unknown", capability_id=None)
        alignment.update(relation="unknown", capability_claim_ids=[])
    elif change == "ambiguous_identity_with_capability":
        item.update(identity_status="ambiguous", canonical_test_id=None)
        alignment.update(relation="ambiguous", capability_claim_ids=[])
    elif change == "selected_claim_without_capability":
        item["capability_id"] = None
    assert not Draft202012Validator(matching_output_schema()).is_valid(payload)


@pytest.mark.parametrize("relation", ["unknown", "ambiguous"])
def test_provider_ignoring_state_schema_is_rejected_once_and_preserves_original(relation):
    payload = proposal()
    payload["items"][0]["claim_alignments"][0]["relation"] = relation
    untouched = deepcopy(payload)
    raw = " \n" + json.dumps(payload, ensure_ascii=False) + "\n "
    provider = RecordingProvider(payload, raw_text=raw)
    with pytest.raises(SemanticMatchingError) as error:
        SemanticMatchingRunner(provider).run(snapshot())
    attempt = error.value.run
    assert len(provider.requests) == 1
    assert attempt.status == "failed"
    assert attempt.error_code == "invalid_output_schema"
    assert attempt.output is None
    assert attempt.proposal is None
    assert attempt.parsed_response == untouched
    assert attempt.raw_response == raw
    assert attempt.usage.total_tokens == 130
    assert payload == untouched
    assert provider.requests[0].json_schema == matching_output_schema()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_input_guard_requires_positive_integer(limit):
    with pytest.raises(ValueError):
        SemanticMatchingRunner(RecordingProvider(proposal()), max_input_characters=limit)


def compile_source(source=None, payload=None, status="supported"):
    run = SemanticMatchingRunner(RecordingProvider(payload or proposal())).run(source or snapshot())
    return run, compile_matching_run(run, {"req_001": status})[0]


def test_compiler_uses_semantic_alias_but_numeric_range_is_still_checked():
    source = snapshot(
        requested=[claim("req_claim_1", parameter_id="water_temperatur", value=Decimal(60))],
        declared=[claim("lab_claim_1", value=Decimal(30))],
    )
    run, result = compile_source(source)
    assert result.checks[-1].status == "gap"
    assert result.checks[-1].declared == "30 °C"
    assert result.status == "partially_matched"
    assert run.input_snapshot.requirements[0].normalized.claims[0].value == 60
    assert run.input_snapshot.capabilities[0].claims[0].value == 30


def test_compiler_alias_range_coverage_preserves_source_values_ids_and_snapshot():
    source = snapshot(
        requested=[claim("req_claim_1", parameter_id="water_temperatur")],
        declared=[
            claim(
                "lab_claim_1",
                operator="range",
                value=None,
                minimum=Decimal(20),
                maximum=Decimal(80),
            )
        ],
    )
    run = SemanticMatchingRunner(RecordingProvider(proposal())).run(source)
    before = run.model_dump(mode="json")
    result = compile_matching_run(run, {"req_001": "supported"})[0]
    assert result.status == "fully_matched"
    assert result.checks[-1].capability_claim_ids == ["lab_claim_1"]
    assert result.checks[-1].declared == "20–80 °C"
    assert run.model_dump(mode="json") == before
    result.checks[-1].requirement_claim.value = Decimal(999)
    assert run.model_dump(mode="json") == before


def test_compiler_unknown_alignment_never_falls_back_to_a_matching_lab_parameter():
    payload = proposal(declared_ids=[])
    run, result = compile_source(payload=payload)
    assert run.input_snapshot.capabilities[0].claims[0].value == 60
    assert result.checks[-1].status == "unknown"
    assert result.checks[-1].capability_claim_ids == []
    assert result.status == "needs_information"


def test_compiler_uses_validated_output_instead_of_the_raw_aligned_proposal():
    source = snapshot(declared=[claim("lab_claim_1", source_context="Steel only")])
    run, result = compile_source(source)
    assert run.proposal.items[0].claim_alignments[0].relation == "same_parameter"
    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert result.checks[-1].status == "unknown"
    assert result.checks[-1].declared is None


def test_compiler_unresolved_source_is_not_repaired_by_a_plausible_ai_link():
    source = snapshot(
        requested=[
            claim(
                "req_claim_1",
                parameter_id=None,
                normalization_state="unresolved",
            )
        ]
    )
    _, result = compile_source(source)
    assert result.checks[-1].status == "unknown"
    assert "does not repair" in result.checks[-1].reason
    assert result.checks[-1].requirement_claim.parameter_id is None


def test_compiler_explicit_unavailable_claim_remains_a_gap():
    _, result = compile_source(
        snapshot(
            declared=[
                claim(
                    "lab_claim_1",
                    availability="unsupported",
                )
            ]
        )
    )
    assert result.checks[-1].status == "gap"
    assert result.status == "partially_matched"


def test_compiler_deterministic_method_gap_is_not_overridden_by_ai_parameter_links():
    _, result = compile_source(status="gap")
    assert result.checks[0].status == "gap"
    assert result.checks[-1].status == "supported"
    assert result.status == "not_matched"


def test_compiler_unknown_method_is_not_overridden_by_ai_parameter_links():
    _, result = compile_source(status="unknown")
    assert result.checks[0].status == "unknown"
    assert result.status == "needs_information"


def test_compiler_unknown_test_identity_cannot_be_certified_by_a_familiar_method():
    payload = proposal(declared_ids=[])
    payload["items"][0].update(
        identity_status="unknown",
        canonical_test_id=None,
        capability_id=None,
    )
    _, result = compile_source(payload=payload)
    assert result.checks[0].status == "unknown"
    assert result.status == "needs_information"


def test_compiler_missing_selected_capability_cannot_receive_supported_method_check():
    payload = proposal(declared_ids=[])
    payload["items"][0]["capability_id"] = None
    _, result = compile_source(payload=payload)
    assert result.checks[0].status == "unknown"
    assert result.status == "needs_information"


@pytest.mark.parametrize(
    "statuses",
    [
        {},
        {"req_001": "supported", "req_foreign": "gap"},
        {"req_001": "maybe"},
    ],
)
def test_compiler_requires_complete_explicit_method_decisions(statuses):
    run = SemanticMatchingRunner(RecordingProvider(proposal())).run(snapshot())
    with pytest.raises(ValueError):
        compile_matching_run(run, statuses)


def test_compiler_rejects_a_failed_attempt():
    with pytest.raises(SemanticMatchingError) as error:
        SemanticMatchingRunner(RecordingProvider(proposal()), max_input_characters=1).run(
            snapshot()
        )
    with pytest.raises(ValueError, match="completed"):
        compile_matching_run(error.value.run, {"req_001": "supported"})


def test_compiler_raw_source_requirements_without_atoms_do_not_look_fully_covered():
    _, result = compile_source(snapshot(requested=[]), proposal(requested_ids=[]))
    assert result.status == "needs_information"
    assert result.checks[-1].id == "req_001_missing_normalized_claims"
    assert result.checks[-1].source_pages == [1]


def test_compiler_method_only_request_is_allowed_without_inventing_conditions():
    source = snapshot(requested=[])
    source.requirements[0].requirement.conditions = []
    _, result = compile_source(source, proposal(requested_ids=[]))
    assert result.status == "fully_matched"
    assert len(result.checks) == 1


def test_compiler_checks_entire_tolerance_after_semantic_alias_alignment():
    source = snapshot(
        requested=[claim("req_claim_1", parameter_id="water_temperatur", tolerance=Decimal(2))],
        declared=[
            claim(
                "lab_claim_1",
                operator="range",
                value=None,
                minimum=Decimal(59),
                maximum=Decimal(62),
            )
        ],
    )
    _, result = compile_source(source)
    assert result.checks[-1].status == "gap"
    assert result.checks[-1].requested == "60 ± 2 °C"


def test_compiler_contradictory_selected_lab_declarations_are_unknown():
    source = snapshot(
        declared=[
            claim("lab_claim_1"),
            claim("lab_claim_2", availability="unsupported"),
        ]
    )
    _, result = compile_source(source, proposal(declared_ids=["lab_claim_1", "lab_claim_2"]))
    assert result.checks[-1].status == "unknown"
    assert "conflict" in result.checks[-1].reason


def test_compiler_revalidates_references_after_loading_or_mutating_a_run():
    run = SemanticMatchingRunner(RecordingProvider(proposal())).run(snapshot())
    run.output.items[0].claim_alignments[0].capability_claim_ids = ["foreign_claim"]
    with pytest.raises(ValueError):
        compile_matching_run(run, {"req_001": "supported"})

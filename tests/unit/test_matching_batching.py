"""Whole-source batching, preflight bounds and honest execution artifacts."""

import json

import pytest
from pydantic import ValidationError

from scione.evaluation.generated_matching import build_batch
from scione.library.store import LibraryStore
from scione.matching.batching import (
    MATCHING_BATCH_POLICY_VERSION,
    MatchingBatchPlanningError,
    plan_matching_batches,
    provider_claim_budget,
    request_character_count,
    run_bounded_matching,
)
from scione.matching.models import MatchingInput, SemanticMatchingRun
from scione.matching.service import SemanticMatchingError, prepare_matching_request
from scione.matching.store import SQLMatchingStore
from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationResponse,
    TokenUsage,
)


class RecordingProvider(ModelProvider):
    def __init__(self, *, fail_at=None, unknown_usage_at=None):
        self.requests = []
        self.fail_at = fail_at
        self.unknown_usage_at = unknown_usage_at

    @property
    def provider_name(self):
        return "recording"

    @property
    def model_name(self):
        return "offline_unknown"

    def generate_structured(self, request):
        self.requests.append(request)
        index = len(self.requests)
        if index == self.fail_at:
            raise ProviderError(
                "safe failure",
                raw_text="incomplete actual response",
                usage=TokenUsage(total_tokens=7),
            )
        source = json.loads(request.user_prompt)
        parsed = {
            "schema_version": "0.1",
            "items": [
                {
                    "requirement_id": row["requirement"]["id"],
                    "identity_status": "unknown",
                    "canonical_test_id": None,
                    "capability_id": None,
                    "reason": "Controlled offline response abstains.",
                    "claim_alignments": [
                        {
                            "requirement_claim_id": claim["id"],
                            "capability_claim_ids": [],
                            "relation": "unknown",
                            "reason": "No semantic link proposed by this offline test.",
                        }
                        for claim in row["normalized"]["claims"]
                    ],
                }
                for row in source["requirements"]
            ],
            "review_issues": [],
        }
        return StructuredGenerationResponse(
            provider=self.provider_name,
            model=self.model_name,
            raw_text="  " + json.dumps(parsed) + "\n",
            parsed=parsed,
            usage=(
                TokenUsage()
                if index == self.unknown_usage_at
                else TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120)
            ),
            latency_ms=2,
            provider_request_id=f"actual_call_{index}",
        )


@pytest.mark.parametrize("tokens,expected", [
    (None, 40), (True, 40), (12288, 60), (8192, 48), (4096, 16), (1024, 1), (50000, 60),
])
def test_output_aware_claim_budget_is_bounded_and_keeps_reasoning_reserve(tokens, expected):
    class BudgetProvider(RecordingProvider):
        @property
        def generation_settings(self):
            return {"max_output_tokens": tokens}

    assert provider_claim_budget(BudgetProvider()) == expected


@pytest.fixture
def source():
    return build_batch("batch_01")[0]


def test_small_request_preserves_single_call_response_and_preflight_contract(source):
    provider = RecordingProvider()
    expected = prepare_matching_request(source)
    run = run_bounded_matching(provider, source)
    assert len(provider.requests) == 1
    assert provider.requests[0] == expected == run.request
    assert run.execution_mode == "single_call"
    assert run.planned_count == 1
    assert not run.child_attempts
    assert run.raw_response.startswith("  ")
    assert run.provider_request_id == "actual_call_1"


def test_whole_rows_and_all_atoms_survive_and_candidates_are_isolated(source):
    original = source.model_dump(mode="json")
    plan = plan_matching_batches(source, max_claims_per_batch=1)
    assert not plan.single_call
    assert len(plan.snapshots) == 6
    for index, child in enumerate(plan.snapshots):
        assert child.requirements[0] == source.requirements[index]
        assert child.catalog.methods == source.catalog.methods
        assert {cap.capability.id for cap in child.capabilities} == set(
            child.requirements[0].capability_ids
        )
        assert {test.id for test in child.catalog.tests} == set(
            child.requirements[0].catalog_test_ids
        )
        assert plan.requests[index] == prepare_matching_request(child)
    assert source.model_dump(mode="json") == original


def test_multiple_calls_combine_ids_but_never_fabricate_a_parent_provider_response(source):
    provider = RecordingProvider()
    plan = plan_matching_batches(source, max_claims_per_batch=1)
    run = run_bounded_matching(provider, source, max_claims_per_batch=1)
    assert run.status == "completed"
    assert run.execution_mode == "planned_batches"
    assert run.planned_count == len(run.child_attempts) == len(provider.requests) == 6
    assert provider.requests == list(plan.requests)
    assert run.input_snapshot == source
    assert run.raw_response is None and run.parsed_response is None
    assert "not sent to a provider" in run.request.system_prompt
    assert run.request.schema_name == "local_matching_batch_plan_v1"
    assert run.policy_snapshot["batching"]["policy_version"] == MATCHING_BATCH_POLICY_VERSION
    assert run.usage.total_tokens == 720
    assert run.latency_ms == 12
    assert [row.requirement_id for row in run.output.items] == [
        row.requirement.id for row in source.requirements
    ]
    assert all(child.raw_response.startswith("  ") for child in run.child_attempts)
    assert SemanticMatchingRun.model_validate_json(run.model_dump_json()) == run


def test_unknown_usage_is_not_reported_as_zero(source):
    run = run_bounded_matching(
        RecordingProvider(unknown_usage_at=2), source, max_claims_per_batch=1
    )
    assert run.usage.input_tokens is None
    assert run.usage.output_tokens is None
    assert run.usage.total_tokens is None


def test_greedy_plan_packs_small_parents_in_original_order(source):
    plan = plan_matching_batches(source, max_claims_per_batch=2)
    assert [len(child.requirements) for child in plan.snapshots] == [2, 2, 2]
    assert [row for child in plan.snapshots for row in child.requirements] == source.requirements


def test_failed_new_batch_parent_does_not_replace_previous_saved_success(source, tmp_path):
    library = LibraryStore(tmp_path)
    try:
        store = SQLMatchingStore(library, "isolated_test_workspace")
        completed = run_bounded_matching(RecordingProvider(), source, max_claims_per_batch=1)
        store.save("test_document", completed)
        with pytest.raises(SemanticMatchingError) as caught:
            run_bounded_matching(RecordingProvider(fail_at=2), source, max_claims_per_batch=1)
        store.save("test_document", caught.value.run)
        assert store.load("test_document", completed.run_id) == completed
        assert (
            store.load("test_document", caught.value.run.run_id).child_attempts[1].status
            == "failed"
        )
        assert (
            store.latest_completed("test_document", completed.input_fingerprint).run_id
            == completed.run_id
        )
        assert len(store.listing("test_document")) == 2
    finally:
        library.close()


def test_first_failure_stops_without_retry_or_partial_accepted_output(source):
    provider = RecordingProvider(fail_at=2)
    with pytest.raises(SemanticMatchingError) as caught:
        run_bounded_matching(provider, source, max_claims_per_batch=1)
    run = caught.value.run
    assert len(provider.requests) == len(run.child_attempts) == 2
    assert run.planned_count == 6
    assert run.status == "failed" and run.error_code == "batch_child_failed"
    assert run.proposal is None and run.output is None
    assert run.child_attempts[0].status == "completed"
    assert run.child_attempts[1].raw_response == "incomplete actual response"
    assert run.child_attempts[1].error_code == "provider_error"
    assert run.raw_response is None
    assert run.usage.total_tokens == 127
    assert run.usage.input_tokens is None


def test_later_oversized_row_prevents_every_call(source):
    data = source.model_dump(mode="json")
    # Raw evidence stays part of the immutable parent and cannot be trimmed.
    data["requirements"][-1]["requirement"]["conditions"] = [
        {"name": "Preserved source clause", "raw_value": "x" * 1500} for _ in range(20)
    ]
    large = MatchingInput.model_validate(data)
    singles = plan_matching_batches(source, max_claims_per_batch=1)
    limit = max(request_character_count(row) for row in singles.requests) + 100
    provider = RecordingProvider()
    with pytest.raises(SemanticMatchingError) as caught:
        run_bounded_matching(provider, large, max_input_characters=limit, max_claims_per_batch=1)
    assert not provider.requests
    assert caught.value.run.error_code == "batch_input_limit_exceeded"
    assert caught.value.run.child_attempts == []
    assert caught.value.run.input_snapshot == large
    assert caught.value.run.usage.total_tokens == 0
    assert not caught.value.run.policy_snapshot["batching"]["preflight_complete"]


def test_more_than_eight_calls_is_rejected_before_first_call(source):
    data = source.model_dump(mode="json")
    originals = data["requirements"]
    data["requirements"] = []
    for index in range(9):
        row = json.loads(json.dumps(originals[index % len(originals)]))
        row["requirement"]["id"] = f"req_extra_{index}"
        row["normalized"]["requirement_id"] = f"req_extra_{index}"
        data["requirements"].append(row)
    too_many = MatchingInput.model_validate(data)
    provider = RecordingProvider()
    with pytest.raises(SemanticMatchingError) as caught:
        run_bounded_matching(provider, too_many, max_claims_per_batch=1)
    assert not provider.requests
    assert caught.value.run.planned_count == 9
    assert caught.value.run.error_code == "batch_call_limit_exceeded"
    assert len(caught.value.run.policy_snapshot["batching"]["batches"]) == 9


def test_a_large_indivisible_requirement_keeps_every_atom(source):
    data = source.model_dump(mode="json")
    data["requirements"] = data["requirements"][:1]
    claims = data["requirements"][0]["normalized"]["claims"]
    atom = claims[0]
    data["requirements"][0]["normalized"]["claims"] = [
        {**atom, "id": f"atom_{index}"} for index in range(45)
    ]
    large = MatchingInput.model_validate(data)
    plan = plan_matching_batches(large)
    assert len(plan.snapshots) == 1 and not plan.single_call
    assert len(plan.snapshots[0].requirements[0].normalized.claims) == 45
    run = run_bounded_matching(RecordingProvider(), large)
    assert len(run.child_attempts) == 1
    assert len(run.output.items[0].claim_alignments) == 45


def test_character_budget_can_split_below_soft_claim_limit(source):
    singles = plan_matching_batches(source, max_claims_per_batch=1)
    maximum = max(request_character_count(request) for request in singles.requests)
    plan = plan_matching_batches(source, max_input_characters=maximum + 100)
    assert not plan.single_call
    assert len(plan.snapshots) > 1
    assert all(request_character_count(request) <= maximum + 100 for request in plan.requests)
    assert [row for child in plan.snapshots for row in child.requirements] == source.requirements


def test_child_source_fact_or_revision_tampering_is_rejected(source):
    run = run_bounded_matching(RecordingProvider(), source, max_claims_per_batch=1)
    data = run.model_dump(mode="json")
    data["child_attempts"][0]["input_snapshot"]["requirements"][0]["requirement"][
        "original_test_name"
    ] = "changed source"
    with pytest.raises(ValidationError, match="source facts"):
        SemanticMatchingRun.model_validate(data)
    data = run.model_dump(mode="json")
    data["child_attempts"][0]["input_snapshot"]["capability_revision"] = "another_revision"
    with pytest.raises(ValidationError, match="revision"):
        SemanticMatchingRun.model_validate(data)


def test_parent_cannot_claim_a_raw_provider_response(source):
    run = run_bounded_matching(RecordingProvider(), source, max_claims_per_batch=1)
    with pytest.raises(ValidationError, match="no provider raw response"):
        SemanticMatchingRun.model_validate(
            {**run.model_dump(mode="json"), "raw_response": "invented"}
        )


def test_projection_failure_retains_single_call_preflight_artifact_without_network(source):
    source.requirements[0].normalized.claims = []
    provider = RecordingProvider()
    with pytest.raises(SemanticMatchingError) as caught:
        run_bounded_matching(provider, source)
    assert caught.value.run.error_code == "input_projection_error"
    assert caught.value.run.execution_mode == "single_call"
    assert not provider.requests


@pytest.mark.parametrize("value", [False, 0, -1, 2.5])
def test_limits_are_validated_before_calls(source, value):
    with pytest.raises(ValueError):
        plan_matching_batches(source, max_input_characters=value)


def test_planning_error_exposes_honest_zero_call_manifest(source):
    with pytest.raises(MatchingBatchPlanningError) as caught:
        plan_matching_batches(source, max_input_characters=1)
    assert caught.value.code == "batch_input_limit_exceeded"
    assert caught.value.manifest["kind"] == "local_execution_plan_not_a_provider_request"

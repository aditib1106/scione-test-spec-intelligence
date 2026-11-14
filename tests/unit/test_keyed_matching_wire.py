"""Exact keyed coverage is grammar-enforced and canonically adapted without repair."""

import json
import threading
import time
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_matching_batching import RecordingProvider, source  # noqa: F401

from scione.matching.batching import run_bounded_matching
from scione.matching.keyed_wire import decode_keyed_output
from scione.matching.models import MatchingInput
from scione.matching.service import (
    SemanticMatchingError,
    SemanticMatchingRunner,
    prepare_matching_request,
)
from scione.providers.base import ProviderError, StructuredGenerationResponse

__all__ = ["source"]


class PaidProvider(RecordingProvider):
    def __init__(self, *, omit=False, fail=False, delayed=False):
        super().__init__()
        self.omit, self.fail, self.delayed = omit, fail, delayed
        self.lock = threading.Lock()
        self.finished = []

    @property
    def provider_name(self):
        return "sail"

    @property
    def generation_settings(self):
        return {"max_output_tokens": 12288}

    def generate_structured(self, request):
        with self.lock:
            self.requests.append(request)
            index = len(self.requests)
        if self.delayed:
            time.sleep(0.05 if index == 1 else 0.005)
        if self.fail and index == 1:
            raise ProviderError("Controlled failure", raw_text="Actual failed provider output.")
        snapshot = json.loads(request.user_prompt)
        items = {}
        for row in snapshot["requirements"]:
            alignments = {
                claim["id"]: {
                    "capability_claim_ids": [],
                    "relation": "unknown",
                    "reason": "The controlled provider defers this correspondence.",
                    "context_role": None,
                    "scope_relation": None,
                    "value_relation": None,
                }
                for claim in row["normalized"]["claims"]
            }
            if self.omit:
                alignments.pop(next(iter(alignments)))
            items[row["requirement"]["id"]] = {
                "identity_status": "unknown",
                "canonical_test_id": None,
                "capability_id": None,
                "reason": "The controlled provider defers identity.",
                "claim_alignments": alignments,
            }
        payload = {"schema_version": "0.1", "items": items, "review_issues": []}
        if not self.omit:
            Draft202012Validator(request.json_schema).validate(payload)
        self.finished.append(index)
        return StructuredGenerationResponse(
            provider="sail",
            model="offline_paid_fixture",
            parsed=payload,
            raw_text=json.dumps(payload),
            latency_ms=5,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


def test_schema_requires_every_fixed_source_key_and_lossless_adapter_preserves_raw(source):
    request = prepare_matching_request(source, keyed=True)
    Draft202012Validator.check_schema(request.json_schema)
    provider = PaidProvider()
    run = SemanticMatchingRunner(provider).run(source)
    wire = json.loads(run.raw_response)
    untouched = deepcopy(wire)
    canonical = decode_keyed_output(wire, source)
    assert canonical == run.proposal.model_dump(mode="json")
    assert wire == untouched == run.parsed_response
    assert run.wire_format == "matching_keyed_v5"
    assert [item.requirement_id for item in run.proposal.items] == [
        row.requirement.id for row in source.requirements
    ]
    first_id = next(iter(wire["items"]))
    wire["items"].pop(first_id)
    assert list(Draft202012Validator(request.json_schema).iter_errors(wire))


def test_missing_key_is_rejected_instead_of_filled_with_unknown(source):
    provider = PaidProvider(omit=True)
    with pytest.raises(SemanticMatchingError) as caught:
        SemanticMatchingRunner(provider).run(source)
    run = caught.value.run
    assert run.error_code == "invalid_output_references"
    assert run.output is None and run.proposal is None
    assert run.raw_response and run.parsed_response
    assert len(provider.requests) == 1


def test_paid_parallel_waves_keep_source_order_after_out_of_order_completion(source):
    provider = PaidProvider(delayed=True)
    run = run_bounded_matching(provider, source, max_claims_per_batch=1)
    assert provider.finished[0] == 2
    completed_ids = [
        child.input_snapshot.requirements[0].requirement.id for child in run.child_attempts
    ]
    assert completed_ids == [row.requirement.id for row in source.requirements]
    assert run.policy_snapshot["batching"]["max_concurrent_batches"] == 2
    assert run.policy_snapshot["batching"]["latency_semantics"] == "wall_time_ms"
    assert run.usage.total_tokens == 90
    assert not any(thread.name.startswith("matching_") for thread in threading.enumerate())


def test_failed_parallel_wave_retains_both_attempts_and_starts_no_next_wave(source):
    provider = PaidProvider(fail=True, delayed=True)
    with pytest.raises(SemanticMatchingError) as caught:
        run_bounded_matching(provider, source, max_claims_per_batch=1)
    run = caught.value.run
    assert len(provider.requests) == len(run.child_attempts) == 2
    # Concurrent transport entry order is not batch order. Both in-flight
    # outcomes must be retained and the parent must still preserve source order.
    assert sorted(child.status for child in run.child_attempts) == ["completed", "failed"]
    failed = next(child for child in run.child_attempts if child.status == "failed")
    completed = next(child for child in run.child_attempts if child.status == "completed")
    assert failed.raw_response == "Actual failed provider output."
    assert completed.raw_response
    assert [
        child.input_snapshot.requirements[0].requirement.id for child in run.child_attempts
    ] == [row.requirement.id for row in source.requirements[:2]]
    assert run.status == "failed" and run.output is None
    assert not any(thread.name.startswith("matching_") for thread in threading.enumerate())


@pytest.mark.parametrize("field", ["catalog", "capability", "lab_claim"])
def test_keyed_schema_rejects_invented_candidate_id_before_output_acceptance(source, field):
    request = prepare_matching_request(source, keyed=True)
    payload = PaidProvider().generate_structured(request).parsed
    row = source.requirements[0]
    item = payload["items"][row.requirement.id]
    if field == "catalog":
        item["canonical_test_id"] = "invented_test"
    elif field == "capability":
        item["capability_id"] = "invented_capability"
    else:
        link = item["claim_alignments"][row.normalized.claims[0].id]
        link.update(relation="same_parameter", capability_claim_ids=["invented_lab_claim"])
    assert list(Draft202012Validator(request.json_schema).iter_errors(payload))


def test_no_candidate_row_uses_null_choices_and_only_empty_claim_links(source):
    data = source.model_dump(mode="json")
    data["requirements"][0].update(catalog_test_ids=[], capability_ids=[])
    empty = MatchingInput.model_validate(data)
    request = prepare_matching_request(empty, keyed=True)
    Draft202012Validator.check_schema(request.json_schema)
    payload = PaidProvider().generate_structured(request).parsed
    first = empty.requirements[0]
    item = payload["items"][first.requirement.id]
    assert item["canonical_test_id"] is item["capability_id"] is None
    link = item["claim_alignments"][first.normalized.claims[0].id]
    link.update(relation="same_parameter", capability_claim_ids=["invented_lab_claim"])
    assert list(Draft202012Validator(request.json_schema).iter_errors(payload))


@pytest.mark.parametrize("fail", [False, True])
def test_four_matching_workers_keep_order_and_collect_every_admitted_response(source, fail):
    class FourProvider(PaidProvider):
        def __init__(self):
            super().__init__(fail=fail)
            self.admission_lock = threading.Lock()
            self.admissions = 0
            self.first_wave = threading.Barrier(4)

        def generate_structured(self, request):
            with self.admission_lock:
                self.admissions += 1
                position = self.admissions
            if position <= 4:
                self.first_wave.wait(timeout=3)
            return super().generate_structured(request)

    provider = FourProvider()
    if fail:
        with pytest.raises(SemanticMatchingError) as caught:
            run_bounded_matching(
                provider, source, max_claims_per_batch=1, max_concurrent_batches=4,
            )
        run = caught.value.run
        assert len(provider.requests) == len(run.child_attempts) == 4
        assert sum(child.status == "failed" for child in run.child_attempts) == 1
        assert sum(child.status == "completed" for child in run.child_attempts) == 3
    else:
        run = run_bounded_matching(
            provider, source, max_claims_per_batch=1, max_concurrent_batches=4,
        )
        assert len(run.child_attempts) == len(provider.requests) == 6
        assert run.status == "completed"
    assert run.policy_snapshot["batching"]["max_concurrent_batches"] == 4
    assert [c.input_snapshot.requirements[0].requirement.id for c in run.child_attempts] == [
        r.requirement.id for r in source.requirements[:len(run.child_attempts)]
    ]
    assert not any(thread.name.startswith("matching_") for thread in threading.enumerate())

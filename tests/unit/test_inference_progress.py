"""Real returned-call progress is bounded, source-free and observational only."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from test_inventory_guided_extraction import SequenceProvider, document, extraction, inventory
from test_matching_batching import RecordingProvider

from scione.api.inference_gate import InferenceGate
from scione.evaluation.generated_matching import build_batch
from scione.inference_progress import ProgressReporter
from scione.matching.batching import run_bounded_matching
from scione.matching.service import SemanticMatchingError
from scione.providers.base import ProviderError, StructuredGenerationResponse, TokenUsage
from scione.specifications.extraction import CustomerExtractionError, CustomerRequirementRunner


def test_gate_progress_is_tenant_scoped_sanitized_detached_and_rejects_stale_callbacks():
    gate = InferenceGate()
    with gate.claim("lab-a:doc", "analysis", workspace_id="lab-a") as old_callback:
        old_callback({
            "stage": "extracting", "completed_steps": 1, "total_steps": 3,
            "percent": 999, "message": "PRIVATE_FILENAME_AND_SOURCE", "api_key": "SECRET",
        })
        active = gate.snapshot(workspace_id="lab-a")[0]
        assert active["progress"]["percent"] == 33.3
        assert "PRIVATE" not in json.dumps(active) and "SECRET" not in json.dumps(active)
        assert gate.snapshot(workspace_id="lab-b") == gate.snapshot() == []
        active["progress"]["message"] = "Changed observer copy"
        assert gate.snapshot(workspace_id="lab-a")[0]["progress"]["message"] != (
            "Changed observer copy"
        )
    with gate.claim("lab-a:doc", "matching", workspace_id="lab-a") as new_callback:
        old_callback({"stage": "saving", "completed_steps": 3, "total_steps": 3})
        assert gate.snapshot(workspace_id="lab-a")[0]["progress"]["total_steps"] is None
        new_callback({"stage": "matching", "completed_steps": 0, "total_steps": 1})
        new_callback({"stage": "matching", "completed_steps": 1, "total_steps": 1})
        new_callback({"stage": "matching", "completed_steps": 0, "total_steps": 1})
        assert gate.snapshot(workspace_id="lab-a")[0]["progress"]["completed_steps"] == 1
    assert gate.snapshot(workspace_id="lab-a") == []


def test_reporter_worker_completions_are_serialized_and_callback_failure_is_harmless():
    events = []
    reporter = ProgressReporter(events.append)
    reporter.plan(4, stage="matching")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: reporter.step(stage="matching"), range(4)))
    assert [event["completed_steps"] for event in events] == [0, 1, 2, 3, 4]
    assert [event["percent"] for event in events] == [0, 25, 50, 75, 100]


@pytest.mark.parametrize("failure", [False, True])
def test_extraction_progress_counts_real_calls_without_changing_short_document_requests(failure):
    events = []
    outputs = [inventory(), ProviderError("planned failure") if failure else extraction()]
    provider = SequenceProvider(outputs)
    if failure:
        with pytest.raises(CustomerExtractionError):
            CustomerRequirementRunner(provider).run(
                document(), strategy="inventory_compact_document", on_progress=events.append,
            )
    else:
        CustomerRequirementRunner(provider).run(
            document(), strategy="inventory_compact_document", on_progress=events.append,
        )
        baseline = SequenceProvider([inventory(), extraction()])
        CustomerRequirementRunner(baseline).run(document(), strategy="inventory_compact_document")
        assert provider.requests == baseline.requests
    assert len(provider.requests) == 2
    completed = [event["completed_steps"] for event in events]
    assert completed == sorted(completed) and max(completed) == 2
    assert all(event["total_steps"] == 2 for event in events if event["stage"] != "planning")
    assert document().pages[0].text not in json.dumps(events)


def test_throwing_progress_observer_cannot_fail_a_valid_inference_or_add_calls():
    def broken(_event):
        raise RuntimeError("PRIVATE_OBSERVER_FAILURE")
    provider = SequenceProvider([inventory(), extraction()])
    result = CustomerRequirementRunner(provider).run(
        document(), strategy="inventory_compact_document", on_progress=broken,
    )
    assert result.extraction.requirements and len(provider.requests) == 2
    matching = RecordingProvider()
    result = run_bounded_matching(matching, build_batch("batch_01")[0], on_progress=broken)
    assert result.status == "completed" and len(matching.requests) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_four_parallel_matching_returns_publish_real_monotonic_counts_and_stop_failed_wave(failure):
    class FourProvider(RecordingProvider):
        def __init__(self):
            super().__init__()
            self.lock = Lock()
            self.barrier = Barrier(4)
            self.active = self.maximum = 0

        @property
        def provider_name(self):
            return "sail"

        def generate_structured(self, request):
            with self.lock:
                self.requests.append(request)
                index = len(self.requests)
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            try:
                if index <= 4:
                    self.barrier.wait(timeout=3)
                if failure and index == 2:
                    raise ProviderError("planned failure")
                items = {}
                for identifier, shape in request.json_schema["properties"]["items"][
                    "properties"
                ].items():
                    claims = shape["properties"]["claim_alignments"]["properties"]
                    items[identifier] = {
                        "identity_status": "unknown", "canonical_test_id": None,
                        "capability_id": None, "reason": "Offline abstention.",
                        "claim_alignments": {key: {
                            "relation": "unknown", "capability_claim_ids": [],
                            "reason": "Offline abstention.",
                        } for key in claims},
                    }
                parsed = {"schema_version": "0.1", "items": items, "review_issues": []}
                return StructuredGenerationResponse(
                    provider="sail", model="offline", raw_text=json.dumps(parsed),
                    parsed=parsed, usage=TokenUsage(), latency_ms=1,
                )
            finally:
                with self.lock:
                    self.active -= 1

    events = []
    provider = FourProvider()
    kwargs = {"max_claims_per_batch": 1, "max_concurrent_batches": 4,
              "on_progress": events.append}
    if failure:
        with pytest.raises(SemanticMatchingError) as captured:
            run_bounded_matching(provider, build_batch("batch_01")[0], **kwargs)
        assert len(captured.value.run.child_attempts) == 4
    else:
        result = run_bounded_matching(provider, build_batch("batch_01")[0], **kwargs)
        assert result.status == "completed"
    assert provider.maximum == 4
    assert len(provider.requests) == (4 if failure else 6)
    counts = [event["completed_steps"] for event in events]
    assert counts == sorted(counts) and max(counts) == len(provider.requests)
    assert all(event["total_steps"] == 6 for event in events if event["stage"] != "planning")
    assert max(event["percent"] or 0 for event in events) == (66.7 if failure else 100)

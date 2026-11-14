"""Chunk waves preserve atomic extraction, source order and the short-file path."""

import json
import re
from collections import Counter
from threading import Barrier, Event, Lock

import pytest
from test_customer_input_chunking import (
    SHARED_CONDITION,
    client_source,
    compact_payload,
    pencil_requirement,
    physical_pages,
    primary_pages,
)

from scione.providers import ProviderError, StaticModelProvider, TokenUsage
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.extraction import CustomerExtractionError, CustomerRequirementRunner


class ParallelFixtureProvider(StaticModelProvider):
    def __init__(self, name="sail", fault=None):
        super().__init__({}, model_name="offline-parallel-chunks")
        self.name = name
        self.fault = fault
        self.lock = Lock()
        self.first_wave_started = Barrier(2)
        self.second_finished = Event()
        self.first_failed = Event()
        self.started = []
        self.completed = []
        self.active = self.peak = 0
        self.responses = {}
        self.safe_failure = ProviderError(
            "Controlled first-wave provider failure", raw_text="untouched partial source reply",
            usage=TokenUsage(input_tokens=17, output_tokens=5), latency_ms=12,
            provider=name, model=self.model_name, provider_request_id="failed-original-call",
        )

    @property
    def provider_name(self):
        return self.name

    def generate_structured(self, request):
        inventory = request.schema_name.startswith("customer_test_inventory")
        marker = re.search(r"<SOURCE_CHUNK index='(\d+)'", request.user_prompt)
        index = int(marker[1]) if marker else 0
        key = "inventory" if inventory else f"extraction-{index}"
        with self.lock:
            self.started.append(key)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if marker and index in {1, 2}:
                self.first_wave_started.wait(timeout=3)
                if self.fault:
                    if index == 1:
                        self.first_failed.set()
                        if self.fault == "provider":
                            raise self.safe_failure
                    else:
                        assert self.first_failed.wait(timeout=3)
                elif index == 1:
                    assert self.second_finished.wait(timeout=3)
            if inventory:
                value = {"tests": [], "attachments": [], "uncertainties": []}
            elif marker:
                rows = [pencil_requirement()] if 6 in primary_pages(request) else []
                shared = []
                if rows and 3 in physical_pages(request):
                    shared = [{
                        "applies_to": [1], "conditions": [SHARED_CONDITION],
                        "criteria": [], "scope": [], "instructions": [], "atomic_claims": [],
                        "evidence": [{"page": 3, "quote": SHARED_CONDITION}],
                    }]
                value = compact_payload(rows, shared)
                if index == 1 and self.fault == "schema":
                    value["document"]["title"] = ["invalid", "metadata"]
            else:
                row = pencil_requirement()
                row["evidence"] = [{"page": 1, "quote": SHORT_SOURCE}]
                value = compact_payload([row])
            result = StaticModelProvider(value).generate_structured(request).model_copy(update={
                "provider": self.provider_name, "model": self.model_name,
                "raw_text": "  " + json.dumps(value, ensure_ascii=False) + "\n",
                "usage": TokenUsage(input_tokens=17, output_tokens=5, total_tokens=22),
                "provider_request_id": key,
            })
            with self.lock:
                self.completed.append(key)
                self.responses[key] = result
            if marker and index == 2:
                self.second_finished.set()
            return result
        finally:
            with self.lock:
                self.active -= 1


@pytest.mark.parametrize("name", ["sail", "fireworks"])
def test_parallel_source_chunks_keep_call_artifacts_in_source_order(name):
    provider = ParallelFixtureProvider(name)
    run = CustomerRequirementRunner(provider).run(
        client_source(), strategy="inventory_compact_document",
    )
    assert provider.peak == 2 and provider.active == 0
    assert provider.completed.index("extraction-2") < provider.completed.index("extraction-1")
    assert [c.call_index for c in run.calls] == list(range(1, len(run.calls) + 1))
    extraction = [c for c in run.calls if c.purpose == "extraction"]
    assert [c.focus_pages for c in extraction] == sorted(c.focus_pages for c in extraction)
    for index, call in enumerate(extraction, 1):
        original = provider.responses[f"extraction-{index}"]
        assert call.raw_response == original.raw_text
        assert call.provider_request_id == original.provider_request_id
    assert run.usage.total_tokens == 22 * len(run.calls)
    assert all(count == 1 for count in Counter(provider.started).values())
    assert [row.original_test_name for row in run.extraction.requirements] == ["Pencil hardness"]
    partition = json.loads(run.raw_response)["input_partitioning"]
    assert partition["max_concurrent_chunks"] == 2
    assert partition["latency_semantics"] == "wall_time_ms"


@pytest.mark.parametrize("fault,code", [
    ("provider", "provider_error"), ("schema", "invalid_output_schema"),
])
def test_first_wave_failure_keeps_both_admitted_attempts_and_starts_no_later_wave(fault, code):
    provider = ParallelFixtureProvider(fault=fault)
    with pytest.raises(CustomerExtractionError) as caught:
        CustomerRequirementRunner(provider).run(
            client_source(), strategy="inventory_compact_document",
        )
    artifact = caught.value.artifact
    assert artifact is not None and artifact.error_code == code
    assert provider.active == 0 and provider.peak == 2
    assert Counter(provider.started) == {"inventory": 1, "extraction-1": 1, "extraction-2": 1}
    assert [call.call_index for call in artifact.calls] == [1, 2, 3]
    first, sibling = artifact.calls[1:]
    assert first.focus_pages == [1, 2, 3]
    assert sibling.focus_pages == [4, 5, 6]
    assert sibling.response.raw_text == provider.responses["extraction-2"].raw_text
    assert sibling.response.provider_request_id == "extraction-2"
    if fault == "provider":
        assert first.failure.raw_text == "untouched partial source reply"
        assert first.failure.usage.output_tokens == 5
        assert first.response is None
    else:
        assert first.response.raw_text == provider.responses["extraction-1"].raw_text
        assert first.failure is None


SHORT_SOURCE = "Pencil hardness ISO 15184, 750 g, acceptance >= F."


@pytest.mark.parametrize("name", ["sail", "fireworks"])
def test_short_source_keeps_the_original_serial_inventory_and_extraction_calls(name):
    document = IngestedDocument(
        document_id="short-parallel-probe", file_name="short.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=SHORT_SOURCE, character_count=len(SHORT_SOURCE),
            non_whitespace_character_count=len("".join(SHORT_SOURCE.split())),
        )],
    )
    provider = ParallelFixtureProvider(name)
    run = CustomerRequirementRunner(provider).run(document, strategy="inventory_compact_document")
    assert provider.started == provider.completed == ["inventory", "extraction-0"]
    assert provider.peak == 1 and provider.active == 0
    assert len(run.calls) == 2
    assert [call.purpose for call in run.calls] == ["inventory", "extraction"]
    assert all("<SOURCE_CHUNK " not in call.request.user_prompt for call in run.calls)
    assert "input_partitioning" not in json.loads(run.raw_response)


@pytest.mark.parametrize("fault", [False, True])
def test_four_admitted_chunks_are_retained_before_failure_or_next_wave(fault):
    class FourProvider(StaticModelProvider):
        def __init__(self):
            super().__init__({})
            self.barrier = Barrier(4)
            self.started = []
            self.lock = Lock()

        @property
        def provider_name(self):
            return "sail"

        def generate_structured(self, request):
            match = re.search(r"<SOURCE_CHUNK index='(\d+)'", request.user_prompt)
            index = int(match[1]) if match else 0
            with self.lock:
                self.started.append(index)
            if 1 <= index <= 4:
                self.barrier.wait(timeout=3)
            if index == 1 and fault:
                raise ProviderError("Controlled first-wave error", raw_text="partial original")
            if not match:
                value = {"tests": [], "attachments": [], "uncertainties": []}
            else:
                value = compact_payload(
                    [pencil_requirement()] if 6 in primary_pages(request) else []
                )
            return StaticModelProvider(value).generate_structured(request)

    provider = FourProvider()
    runner = CustomerRequirementRunner(provider, max_concurrent_chunks=4)
    if fault:
        with pytest.raises(CustomerExtractionError) as caught:
            runner.run(client_source(), strategy="inventory_compact_document")
        artifact = caught.value.artifact
        assert artifact is not None
        assert len(artifact.calls) == 5
        assert artifact.calls[1].failure.raw_text == "partial original"
        assert all(call.response is not None for call in artifact.calls[2:])
        assert set(provider.started) == {0, 1, 2, 3, 4}
    else:
        run = runner.run(client_source(), strategy="inventory_compact_document")
        partition = json.loads(run.raw_response)["input_partitioning"]
        assert partition["max_concurrent_chunks"] == 4
        assert len(provider.started) == len(set(provider.started)) == len(run.calls)
        assert [call.call_index for call in run.calls] == list(range(1, len(run.calls) + 1))

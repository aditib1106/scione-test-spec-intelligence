"""Bounded parallel extraction preserves response order and safe failure metadata."""

from collections import Counter
from threading import Barrier, Event, Lock

import pytest

from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TokenUsage,
)
from scione.specifications.chunk_execution import generate_chunk_wave


def request(number: int):
    return StructuredGenerationRequest(
        system_prompt="Offline wave test", user_prompt=str(number),
        schema_name="offline", json_schema={"type": "object"},
    )


def response(number: int):
    return StructuredGenerationResponse(
        provider="offline", model="wave-probe", raw_text=f"  raw response {number}\n",
        parsed={"number": number}, latency_ms=1,
        usage=TokenUsage(input_tokens=number, output_tokens=number),
        provider_request_id=f"request-{number}",
    )


class ProbeProvider(ModelProvider):
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.lock = Lock()

    @property
    def provider_name(self):
        return "offline"

    @property
    def model_name(self):
        return "wave-probe"

    def generate_structured(self, value):
        with self.lock:
            self.calls.append(value.user_prompt)
        return self.handler(int(value.user_prompt))


def test_two_calls_run_concurrently_but_return_in_original_source_order():
    barrier = Barrier(2)
    second_finished = Event()
    lock = Lock()
    active = peak = 0
    completion_order = []
    outputs = {number: response(number) for number in (1, 2)}

    def generate(number):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=3)
            if number == 1:
                assert second_finished.wait(timeout=3)
            with lock:
                completion_order.append(number)
            if number == 2:
                second_finished.set()
            return outputs[number]
        finally:
            with lock:
                active -= 1

    provider = ProbeProvider(generate)
    actual = generate_chunk_wave(provider, [request(1), request(2)])
    assert peak == 2
    assert active == 0
    assert completion_order == [2, 1]
    assert actual[0] is outputs[1]
    assert actual[1] is outputs[2]
    assert actual[0].raw_text == "  raw response 1\n"
    assert Counter(provider.calls) == {"1": 1, "2": 1}


def test_provider_failure_is_retained_and_the_other_admitted_result_is_collected():
    failure = ProviderError(
        "safe provider failure", raw_text="original incomplete response",
        usage=TokenUsage(input_tokens=27, output_tokens=13), latency_ms=55,
        provider="offline", model="wave-probe", provider_request_id="failed-call",
    )
    good = response(2)
    completed = Event()

    def generate(number):
        if number == 1:
            raise failure
        completed.set()
        return good

    provider = ProbeProvider(generate)
    actual = generate_chunk_wave(provider, [request(1), request(2)])
    assert actual[0] is failure
    assert actual[0].raw_text == "original incomplete response"
    assert actual[0].usage.output_tokens == 13
    assert actual[1] is good
    assert completed.is_set()
    assert Counter(provider.calls) == {"1": 1, "2": 1}


def test_programming_exception_propagates_without_becoming_a_provider_failure():
    programming_error = ValueError("internal implementation defect")
    other_completed = Event()

    def generate(number):
        if number == 1:
            raise programming_error
        other_completed.set()
        return response(number)

    provider = ProbeProvider(generate)
    with pytest.raises(ValueError) as caught:
        generate_chunk_wave(provider, [request(1), request(2)])
    assert caught.value is programming_error
    assert other_completed.is_set()
    assert Counter(provider.calls) == {"1": 1, "2": 1}


def test_oversized_wave_is_rejected_before_any_provider_call():
    provider = ProbeProvider(response)
    with pytest.raises(ValueError, match="at most 2"):
        generate_chunk_wave(provider, [request(1), request(2), request(3)])
    assert provider.calls == []


def test_single_or_empty_wave_does_not_schedule_other_requests():
    provider = ProbeProvider(response)
    assert generate_chunk_wave(provider, []) == []
    actual = generate_chunk_wave(provider, [request(1)])
    assert len(actual) == 1 and actual[0].parsed == {"number": 1}
    assert provider.calls == ["1"]


def test_explicit_four_request_wave_preserves_all_attempts_and_order():
    barrier = Barrier(4)
    active = peak = 0
    lock = Lock()
    failure = ProviderError("Controlled wave failure", raw_text="actual partial output")

    def generate(number):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=3)
            if number == 2:
                raise failure
            return response(number)
        finally:
            with lock:
                active -= 1

    provider = ProbeProvider(generate)
    results = generate_chunk_wave(
        provider, [request(n) for n in range(1, 5)], max_concurrent_calls=4,
    )
    assert peak == 4 and active == 0
    assert [r.parsed["number"] if not isinstance(r, ProviderError) else "failed"
            for r in results] == [1, "failed", 3, 4]
    assert results[1] is failure
    assert Counter(provider.calls) == Counter(map(str, range(1, 5)))


@pytest.mark.parametrize("width", [0, -1, 5, True, 2.5, "4"])
def test_invalid_parallelism_is_rejected_without_a_request(width):
    provider = ProbeProvider(response)
    with pytest.raises(ValueError, match="between 1 and 4"):
        generate_chunk_wave(provider, [request(1)], max_concurrent_calls=width)
    assert provider.calls == []

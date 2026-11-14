"""Execute one bounded extraction wave without retries or background follow-ups.

The caller owns the complete source plan and atomic result commit. All admitted
requests finish before this helper returns, including when one provider fails.
Responses stay in request order so provenance and later parsing remain stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
)

MAX_CHUNK_WAVE_CALLS = 4


def generate_chunk_wave(
    provider: ModelProvider,
    requests: Sequence[StructuredGenerationRequest],
    *, max_concurrent_calls: int = 2,
) -> list[StructuredGenerationResponse | ProviderError]:
    """Call each supplied request once; retain safe provider failures as values.

    Only ProviderError is converted into a result so its raw response and usage
    can be kept in the caller's failure artifact. Programming exceptions still
    propagate, after the executor waits for the other admitted call to finish.
    No later wave is submitted here and no accepted call is cancelled or retried.
    """
    if type(max_concurrent_calls) is not int or not 1 <= max_concurrent_calls <= 4:
        raise ValueError("max_concurrent_calls must be an integer between 1 and 4.")
    if len(requests) > max_concurrent_calls:
        raise ValueError(f"A chunk wave accepts at most {max_concurrent_calls} requests.")
    if not requests:
        return []

    def generate(request: StructuredGenerationRequest):
        try:
            return provider.generate_structured(request)
        except ProviderError as exc:
            return exc

    with ThreadPoolExecutor(
        max_workers=len(requests), thread_name_prefix="source-chunk",
    ) as executor:
        attempts = [executor.submit(generate, request) for request in requests]
        return [attempt.result() for attempt in attempts]

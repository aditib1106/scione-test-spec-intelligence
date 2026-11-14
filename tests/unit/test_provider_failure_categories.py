"""Failure diagnostics retain safe categories without SDK prose or new calls."""

import httpx
import pytest
from test_semantic_matching import RecordingProvider, proposal, snapshot

from scione.evaluation.generated_matching import build_batch
from scione.matching.batching import run_bounded_matching
from scione.matching.service import SemanticMatchingError, SemanticMatchingRunner
from scione.providers.base import ProviderError
from scione.providers.openai_compatible import OpenAICompatibleModelProvider
from scione.specifications.attempts import GenerationFailure


@pytest.mark.parametrize("kind, status", [("timeout", None), ("network", None), ("http", 503)])
def test_matching_retains_safe_cause_without_copying_secret_exception_prose(kind, status):
    failure = ProviderError(
        "Authorization Bearer PRIVATE_SECRET", failure_kind=kind, http_status=status,
    )
    provider = RecordingProvider(proposal(), failure=failure)
    with pytest.raises(SemanticMatchingError) as caught:
        SemanticMatchingRunner(provider).run(snapshot())
    run = caught.value.run
    assert len(provider.requests) == 1
    assert run.provider_failure_kind == kind
    assert run.provider_http_status == status
    assert "PRIVATE_SECRET" not in run.model_dump_json()
    assert "PRIVATE_SECRET" not in str(caught.value)


def test_source_failure_retains_same_structured_cause_and_legacy_none_defaults():
    cause = GenerationFailure.from_provider_error(ProviderError(
        "AI service request failed.", failure_kind="http", http_status=429,
    ))
    assert cause.failure_kind == "http" and cause.http_status == 429
    legacy = GenerationFailure.model_validate({"message": "Existing failure artifact."})
    assert legacy.failure_kind is None and legacy.http_status is None
    invalid = ProviderError(
        "Ignored arbitrary diagnostics", failure_kind="SECRET", http_status=True,
    )
    assert invalid.failure_kind is None and invalid.http_status is None


def test_adapter_categorizes_http_error_without_persisting_remote_error_text():
    def handler(request):
        return httpx.Response(503, json={"error": {"message": "PRIVATE_REMOTE_SECRET"}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            provider_name="sail", api_key="PRIVATE_API_KEY", model_name="offline-model",
            client=client,
        )
        from scione.providers.base import StructuredGenerationRequest
        request = StructuredGenerationRequest(
            system_prompt="Offline", user_prompt="Offline", schema_name="offline",
            json_schema={"type": "object"},
        )
        with pytest.raises(ProviderError) as caught:
            provider.generate_structured(request)
    assert caught.value.failure_kind == "http" and caught.value.http_status == 503
    assert "PRIVATE" not in str(caught.value)


def test_batch_timeout_remains_visible_and_admitted_siblings_finish_without_retry():
    class TimeoutProvider(RecordingProvider):
        @property
        def provider_name(self):
            return "sail"
    provider = TimeoutProvider(proposal(), failure=ProviderError(
        "PRIVATE_SDK_EXCEPTION", failure_kind="timeout", latency_ms=1,
    ))
    with pytest.raises(SemanticMatchingError) as caught:
        run_bounded_matching(provider, build_batch("batch_01")[0], max_claims_per_batch=1)
    assert len(provider.requests) == 2
    assert len(caught.value.run.child_attempts) == 2
    assert all(
        child.provider_failure_kind == "timeout" for child in caught.value.run.child_attempts
    )
    assert "timed out" in str(caught.value)
    assert "PRIVATE" not in caught.value.run.model_dump_json()

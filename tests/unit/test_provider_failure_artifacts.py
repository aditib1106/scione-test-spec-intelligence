"""Failed SDK outputs remain private, reproducible matching artifacts."""

from types import SimpleNamespace

import httpx
import pytest
from groq import APIConnectionError, APITimeoutError, BadRequestError

from scione.catalogs.models import ReferenceCatalog
from scione.library.store import LibraryStore
from scione.matching.models import MatchingInput, RequirementCandidates
from scione.matching.service import SemanticMatchingError, SemanticMatchingRunner
from scione.matching.store import SQLMatchingStore
from scione.providers.base import ProviderError, StructuredGenerationRequest
from scione.providers.gemini import GeminiModelProvider, _safe_api_error_message
from scione.providers.groq import GroqModelProvider
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.models import NormalizedRequirement


def matching_input():
    return MatchingInput(
        lab_id="lab_one",
        extraction_run_id="extraction_one",
        catalog_revision="catalog_one",
        capability_revision="capability_one",
        catalog=ReferenceCatalog(
            id="test_catalog",
            name="Test catalog",
            version=1,
            source_note="Isolated synthetic test",
            tests=[],
            methods=[],
        ),
        requirements=[
            RequirementCandidates(
                requirement=CustomerRequirement(
                    id="req_one",
                    original_test_name="Example test",
                    evidence=[RequirementEvidence(page=1, quote="Example test")],
                ),
                normalized=NormalizedRequirement(
                    requirement_id="req_one", test_name="Example test"
                ),
            )
        ],
    )


def sdk_provider(kind, raw, finish):
    calls = []
    if kind == "gemini":
        response = SimpleNamespace(
            text=raw,
            model_version="resolved-model-2026",
            response_id="response-one",
            candidates=[SimpleNamespace(finish_reason=finish)],
            usage_metadata=SimpleNamespace(
                prompt_token_count=11,
                candidates_token_count=5,
                thoughts_token_count=2,
                total_token_count=18,
            ),
        )

        def generate(**kwargs):
            calls.append(kwargs)
            return response

        provider = GeminiModelProvider(
            api_key="private-key-unused",
            model_name="requested-alias",
            client=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
        )
    else:
        response = SimpleNamespace(
            model="resolved-model-2026",
            id="response-one",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=raw),
                    finish_reason=finish,
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )

        def generate(**kwargs):
            calls.append(kwargs)
            return response

        provider = GroqModelProvider(
            api_key="private-key-unused",
            model_name="requested-alias",
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=generate),
                )
            ),
        )
    return provider, calls


@pytest.mark.parametrize("kind", ["gemini", "groq"])
@pytest.mark.parametrize("failure", ["invalid_json", "truncated", "empty", "wrong_shape"])
def test_failed_provider_response_and_usage_survive_matching_sql_roundtrip(tmp_path, kind, failure):
    raw = {
        "invalid_json": ' \n{"partial":"private customer source",',
        "truncated": '{"partial":"private customer source"',
        "empty": "",
        "wrong_shape": '["private customer source"]',
    }[failure]
    finish = None
    if failure == "truncated":
        finish = "MAX_TOKENS" if kind == "gemini" else "length"
    provider, calls = sdk_provider(kind, raw, finish)
    with pytest.raises(SemanticMatchingError) as captured:
        SemanticMatchingRunner(provider).run(matching_input())
    attempt = captured.value.run
    assert len(calls) == 1
    assert attempt.status == "failed" and attempt.output is None
    assert attempt.raw_response == raw
    assert attempt.model == "resolved-model-2026"
    assert attempt.requested_model == "requested-alias"
    assert attempt.provider == attempt.requested_provider == kind
    assert attempt.provider_request_id == "response-one"
    assert attempt.latency_ms is not None and attempt.latency_ms >= 0
    assert attempt.usage.input_tokens == 11
    assert attempt.usage.output_tokens == 7
    assert attempt.usage.total_tokens == 18
    assert "private customer source" not in str(captured.value)
    assert "private-key-unused" not in attempt.model_dump_json()
    archive = LibraryStore(tmp_path)
    try:
        store = SQLMatchingStore(archive, "workspace_one")
        store.save("document_one", attempt)
        saved = store.load("document_one", attempt.run_id)
        assert saved.model_dump(mode="json") == attempt.model_dump(mode="json")
    finally:
        archive.close()


@pytest.mark.parametrize("kind", ["gemini", "groq"])
def test_provider_error_string_does_not_include_actual_raw_answer(kind):
    raw = ' \n{"private":"customer source",'
    provider, _ = sdk_provider(kind, raw, None)
    with pytest.raises(ProviderError) as captured:
        provider.generate_structured(
            StructuredGenerationRequest(
                system_prompt="Example",
                user_prompt="Example",
                schema_name="example",
                json_schema={},
            )
        )
    assert captured.value.raw_text == raw
    assert "customer source" not in str(captured.value)
    assert "customer source" not in repr(captured.value)
    assert captured.value.usage.total_tokens == 18


def test_groq_api_json_validation_failure_retains_only_generated_output():
    raw = '{"partial":"private customer source"'
    error = BadRequestError(
        "private request detail",
        response=httpx.Response(
            400,
            request=httpx.Request(
                "POST",
                "https://api.groq.invalid",
                headers={"Authorization": "Bearer PRIVATE_KEY"},
            ),
        ),
        body={
            "error": {
                "code": "json_validate_failed",
                "message": "private request detail",
                "failed_generation": raw,
            }
        },
    )

    def create(**kwargs):
        raise error

    provider = GroqModelProvider(
        api_key="not-used",
        model_name="requested-alias",
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    with pytest.raises(SemanticMatchingError) as captured:
        SemanticMatchingRunner(provider).run(matching_input())
    assert captured.value.run.raw_response == raw
    assert captured.value.run.requested_model == "requested-alias"
    assert "PRIVATE_KEY" not in captured.value.run.model_dump_json()
    assert "private request detail" not in captured.value.run.model_dump_json()


def test_gemini_api_status_message_does_not_echo_arbitrary_provider_message():
    message = _safe_api_error_message(
        SimpleNamespace(
            code=400,
            status="INVALID_ARGUMENT",
            message="Private prompt or API key",
        )
    )
    assert "HTTP 400" in message and "INVALID_ARGUMENT" in message
    assert "Private" not in message


def test_gemini_rejected_text_accessor_retains_answer_parts_without_thinking_text():
    class PartialResponse:
        model_version = "resolved-model-2026"
        response_id = "response-one"
        usage_metadata = SimpleNamespace(prompt_token_count=11, total_token_count=18)
        candidates = [
            SimpleNamespace(
                finish_reason="MAX_TOKENS",
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text='{"partial":"answer"', thought=False),
                        SimpleNamespace(text="private thinking text", thought=True),
                    ]
                ),
            )
        ]

        @property
        def text(self):
            raise ValueError("Private SDK detail")

    provider = GeminiModelProvider(
        api_key="not-used",
        model_name="requested-alias",
        client=SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kwargs: PartialResponse())
        ),
    )
    with pytest.raises(SemanticMatchingError) as captured:
        SemanticMatchingRunner(provider).run(matching_input())
    assert captured.value.run.raw_response == '{"partial":"answer"'
    assert captured.value.run.model == "resolved-model-2026"
    assert captured.value.run.usage.total_tokens == 18
    assert "private thinking text" not in captured.value.run.model_dump_json()
    assert "Private SDK detail" not in captured.value.run.model_dump_json()


def test_message_only_provider_errors_remain_compatible():
    error = ProviderError("A safe failure message.")
    assert str(error) == "A safe failure message."
    assert error.args == ("A safe failure message.",)
    assert error.raw_text is None and error.latency_ms is None
    assert error.usage.total_tokens is None


@pytest.mark.parametrize("kind", ["gemini", "groq"])
@pytest.mark.parametrize("timeout", [False, True])
def test_provider_transport_failure_is_a_safe_persistable_matching_attempt(tmp_path, kind, timeout):
    request = httpx.Request(
        "POST",
        "https://provider.invalid?key=PRIVATE_KEY",
        headers={"Authorization": "Bearer PRIVATE_HEADER"},
    )
    if kind == "gemini":
        exception = (httpx.ReadTimeout if timeout else httpx.ConnectError)(
            "PRIVATE_EXCEPTION_DETAIL",
            request=request,
        )
    else:
        exception = (
            APITimeoutError(request=request)
            if timeout
            else APIConnectionError(
                message="PRIVATE_EXCEPTION_DETAIL",
                request=request,
            )
        )
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        raise exception

    if kind == "gemini":
        provider = GeminiModelProvider(
            api_key="PRIVATE_KEY",
            model_name="requested-model",
            client=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
        )
    else:
        provider = GroqModelProvider(
            api_key="PRIVATE_KEY",
            model_name="requested-model",
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=generate),
                )
            ),
        )
    with pytest.raises(SemanticMatchingError) as captured:
        SemanticMatchingRunner(provider).run(matching_input())
    attempt = captured.value.run
    assert len(calls) == 1
    assert attempt.status == "failed" and attempt.error_code == "provider_error"
    assert attempt.raw_response is None and attempt.output is None
    assert attempt.usage.total_tokens is None
    assert attempt.latency_ms is not None and attempt.latency_ms >= 0
    assert attempt.provider == kind and attempt.model == "requested-model"
    public_error = captured.value.__cause__
    assert isinstance(public_error, ProviderError)
    for private in [
        "PRIVATE_KEY",
        "PRIVATE_HEADER",
        "PRIVATE_EXCEPTION_DETAIL",
        "provider.invalid",
    ]:
        assert private not in str(public_error)
        assert private not in attempt.model_dump_json()
    if kind == "gemini":
        assert ("timed out" if timeout else "network connection") in str(public_error)
        assert public_error.__suppress_context__
    archive = LibraryStore(tmp_path)
    try:
        store = SQLMatchingStore(archive, "workspace_one")
        store.save("document_one", attempt)
        assert store.load("document_one", attempt.run_id).model_dump(
            mode="json"
        ) == attempt.model_dump(mode="json")
    finally:
        archive.close()

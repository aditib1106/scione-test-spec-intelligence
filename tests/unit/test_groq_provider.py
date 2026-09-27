"""Contract tests for Groq structured output without network access."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scione.providers import (
    GroqModelProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from scione.providers.groq import _safe_api_error_message
from scione.schemas import TestMethodExtraction as MethodExtractionSchema

REPOSITORY_ROOT = Path(__file__).parents[2]
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.arguments: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.arguments = kwargs
        return SimpleNamespace(
            id="request-123",
            model="openai/gpt-oss-20b",
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25, total_tokens=125),
        )


class FakeGroqClient:
    def __init__(self, content: str) -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        system_prompt="Extract structured test methods.",
        user_prompt="Synthetic document text.",
        schema_name="test_method_extraction_v0_1",
        json_schema=MethodExtractionSchema.model_json_schema(),
    )


def test_groq_provider_requests_strict_schema_and_records_usage() -> None:
    payload = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    client = FakeGroqClient(json.dumps(payload))
    provider = GroqModelProvider(
        api_key="not-used-by-fake",
        model_name="openai/gpt-oss-20b",
        strict_structured_output=True,
        client=client,
    )

    response = provider.generate_structured(_request())

    arguments = client.completions.arguments
    assert arguments is not None
    response_format = arguments["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert arguments["temperature"] == 0.0
    value_variants = response_format["json_schema"]["schema"]["$defs"]["AcceptanceCriterion"][
        "properties"
    ]["value"]["anyOf"]
    assert {variant["type"] for variant in value_variants} == {"string", "number"}
    assert response.parsed["document"]["designation"] == "FTM-MG-017"
    assert response.usage.total_tokens == 125
    assert response.provider_request_id == "request-123"


def test_groq_provider_uses_json_object_mode_with_schema_in_prompt() -> None:
    payload = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    client = FakeGroqClient(json.dumps(payload))
    provider = GroqModelProvider(
        api_key="not-used-by-fake",
        model_name="openai/gpt-oss-20b",
        strict_structured_output=False,
        client=client,
    )

    provider.generate_structured(_request())

    arguments = client.completions.arguments
    assert arguments is not None
    assert arguments["response_format"] == {"type": "json_object"}
    system_message = arguments["messages"][0]
    assert "<JSON_SCHEMA>" in system_message["content"]
    assert '"schema_version"' in system_message["content"]


def test_groq_provider_rejects_non_json_content() -> None:
    provider = GroqModelProvider(
        api_key="not-used-by-fake",
        model_name="openai/gpt-oss-20b",
        client=FakeGroqClient("not json"),
    )

    with pytest.raises(ProviderError, match="invalid JSON"):
        provider.generate_structured(_request())


def test_groq_error_message_omits_failed_generation() -> None:
    error = SimpleNamespace(
        status_code=400,
        body={
            "error": {
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "message": "Failed to validate JSON.",
                "failed_generation": "sensitive extracted document content",
            }
        },
    )

    message = _safe_api_error_message(error)

    assert "HTTP 400" in message
    assert "json_validate_failed" in message
    assert "sensitive extracted document content" not in message

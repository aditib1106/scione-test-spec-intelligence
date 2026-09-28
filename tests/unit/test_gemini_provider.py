"""Contract tests for Gemini structured output without network access."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scione.providers import GeminiModelProvider, ProviderError, StructuredGenerationRequest
from scione.schemas import TestMethodExtraction as MethodExtractionSchema

REPOSITORY_ROOT = Path(__file__).parents[2]
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


class FakeModels:
    def __init__(self, content: str) -> None:
        self.content = content
        self.arguments: dict[str, object] | None = None

    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        self.arguments = kwargs
        return SimpleNamespace(
            text=self.content,
            model_version="gemini-3.8-flash-001",
            response_id="response-123",
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=25,
                thoughts_token_count=5,
                total_token_count=130,
            ),
        )


class FakeGeminiClient:
    def __init__(self, content: str) -> None:
        self.models = FakeModels(content)


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        system_prompt="Extract structured test methods.",
        user_prompt="Synthetic document text.",
        schema_name="test_method_extraction_v0_1",
        json_schema=MethodExtractionSchema.model_json_schema(),
    )


def test_gemini_provider_requests_json_schema_and_records_usage() -> None:
    payload = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    client = FakeGeminiClient(json.dumps(payload))
    provider = GeminiModelProvider(
        api_key="not-used-by-fake",
        model_name="gemini-3.8-flash",
        max_retries=0,
        thinking_level="low",
        client=client,
    )

    response = provider.generate_structured(_request())

    arguments = client.models.arguments
    assert arguments is not None
    assert arguments["model"] == "gemini-3.8-flash"
    config = arguments["config"]
    assert config["response_mime_type"] == "application/json"
    assert config["thinking_config"] == {"thinking_level": "low"}
    assert config["http_options"]["retry_options"] == {"attempts": 1}
    assert "default" not in json.dumps(config["response_json_schema"])
    assert response.parsed["document"]["designation"] == "FTM-MG-017"
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 30
    assert response.usage.total_tokens == 130
    assert response.provider_request_id == "response-123"


def test_gemini_provider_rejects_non_json_content() -> None:
    provider = GeminiModelProvider(
        api_key="not-used-by-fake",
        model_name="gemini-3.8-flash",
        client=FakeGeminiClient("not json"),
    )

    with pytest.raises(ProviderError, match="invalid JSON"):
        provider.generate_structured(_request())

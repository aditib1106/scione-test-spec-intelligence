"""Contract tests for Gemini structured output without network access."""

import json
from types import SimpleNamespace

import pytest
from google.genai import types

from scione.providers import GeminiModelProvider, ProviderError, StructuredGenerationRequest
from scione.specifications.models import CustomerExtraction


class FakeModels:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.arguments: dict[str, object] | None = None

    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        self.arguments = kwargs
        return SimpleNamespace(
            text=self.content,
            model_version="gemini-3.8-flash-001",
            response_id="response-123",
            candidates=[SimpleNamespace(finish_reason=self.finish_reason)],
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=25,
                thoughts_token_count=5,
                total_token_count=130,
            ),
        )


class FakeGeminiClient:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.models = FakeModels(content, finish_reason)


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        system_prompt="Extract customer-stated testing requirements without filling gaps.",
        user_prompt="Fictional request: surface shine, 60 degrees, at least 70 units.",
        schema_name="customer_requirements_v0_1",
        json_schema=CustomerExtraction.model_json_schema(),
    )


def _customer_payload() -> dict[str, object]:
    """An independent fictional customer request, not a benchmark or real spec."""

    return CustomerExtraction.model_validate(
        {
            "schema_version": "0.1",
            "document": {"identifier": "DEMO-CUSTOMER-001"},
            "requirements": [
                {
                    "id": "req_shine",
                    "original_test_name": "Surface shine",
                    "conditions": [
                        {"name": "Measurement angle", "raw_value": "60", "unit": "degrees"}
                    ],
                    "acceptance_criteria": [
                        {"name": "Shine", "raw_value": "at least 70", "unit": "units"}
                    ],
                    "evidence": [
                        {
                            "page": 1,
                            "quote": "surface shine, 60 degrees, at least 70 units",
                        }
                    ],
                }
            ],
        }
    ).model_dump(mode="json")


def test_gemini_provider_requests_json_schema_and_records_usage() -> None:
    payload = _customer_payload()
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
    schema = config["response_json_schema"]
    assert '"default"' not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["enum"] == ["0.1"]
    assert "const" not in schema["properties"]["schema_version"]
    requirement_schema = schema["$defs"]["CustomerRequirement"]
    assert requirement_schema["properties"]["conditions"]["items"] == {
        "$ref": "#/$defs/RequirementValue"
    }
    value_schema = schema["$defs"]["RequirementValue"]
    assert value_schema["additionalProperties"] is False
    assert value_schema["properties"]["raw_value"]["type"] == "string"
    for name in ("unit", "context"):
        variants = value_schema["properties"][name]["anyOf"]
        assert {variant["type"] for variant in variants} == {"string", "null"}
    assert response.parsed == payload
    assert response.parsed["document"]["identifier"] == "DEMO-CUSTOMER-001"
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


@pytest.mark.parametrize(
    "finish_reason",
    [types.FinishReason.MAX_TOKENS, "MAX_TOKENS", types.FinishReason.SAFETY, "RECITATION"],
)
def test_gemini_rejects_unsuccessful_finish_even_when_json_is_valid(finish_reason: str) -> None:
    private_text = "private document content must not appear in errors"
    provider = GeminiModelProvider(
        api_key="private-key-not-used",
        model_name="offline-model",
        client=FakeGeminiClient(json.dumps({"text": private_text}), finish_reason),
    )

    reason_value = getattr(finish_reason, "value", finish_reason)
    with pytest.raises(ProviderError, match=f"finish_reason={reason_value}") as captured:
        provider.generate_structured(_request())

    assert private_text not in str(captured.value)
    assert "private-key-not-used" not in str(captured.value)


@pytest.mark.parametrize("finish_reason", [types.FinishReason.STOP, "STOP", None])
def test_gemini_accepts_success_or_legacy_missing_finish_metadata(
    finish_reason: str | None,
) -> None:
    provider = GeminiModelProvider(
        api_key="not-used",
        model_name="offline-model",
        client=FakeGeminiClient('{"ok": true}', finish_reason),
    )

    assert provider.generate_structured(_request()).parsed == {"ok": True}


def test_gemini_legacy_mock_without_candidates_attribute_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeGeminiClient('{"ok": true}')
    generate = client.models.generate_content

    def without_candidates(**kwargs: object) -> SimpleNamespace:
        response = generate(**kwargs)
        del response.candidates
        return response

    monkeypatch.setattr(client.models, "generate_content", without_candidates)
    provider = GeminiModelProvider(api_key="unused", model_name="offline", client=client)
    assert provider.generate_structured(_request()).parsed == {"ok": True}


def test_gemini_unknown_finish_metadata_does_not_leak_arbitrary_text() -> None:
    private = "private document or key in unexpected metadata"
    provider = GeminiModelProvider(
        api_key="unused", model_name="offline", client=FakeGeminiClient('{"ok":true}', private)
    )
    with pytest.raises(ProviderError, match="finish_reason=UNKNOWN") as captured:
        provider.generate_structured(_request())
    assert private not in str(captured.value)

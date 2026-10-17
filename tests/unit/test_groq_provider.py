"""Contract tests for Groq structured output without network access."""

import json
from types import SimpleNamespace

import pytest

from scione.providers import (
    GroqModelProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from scione.providers.groq import _safe_api_error_message
from scione.specifications.models import CustomerExtraction


class FakeCompletions:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.arguments: dict[str, object] | None = None

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.arguments = kwargs
        return SimpleNamespace(
            id="request-123",
            model="openai/gpt-oss-20b",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content), finish_reason=self.finish_reason
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25, total_tokens=125),
        )


class FakeGroqClient:
    def __init__(self, content: str, finish_reason: str | None = None) -> None:
        self.completions = FakeCompletions(content, finish_reason)
        self.chat = SimpleNamespace(completions=self.completions)


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


def test_groq_provider_requests_strict_schema_and_records_usage() -> None:
    payload = _customer_payload()
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
    schema = response_format["json_schema"]["schema"]
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
    assert '"default"' not in json.dumps(schema)
    assert response.parsed == payload
    assert response.parsed["document"]["identifier"] == "DEMO-CUSTOMER-001"
    assert response.usage.total_tokens == 125
    assert response.provider_request_id == "request-123"


def test_groq_provider_uses_json_object_mode_with_schema_in_prompt() -> None:
    payload = _customer_payload()
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


@pytest.mark.parametrize(
    "finish_reason", ["length", "tool_calls", "function_call", "content_filter"]
)
def test_groq_rejects_unsuccessful_finish_even_when_json_is_valid(finish_reason: str) -> None:
    private_text = "private document content must not appear in errors"
    provider = GroqModelProvider(
        api_key="private-key-not-used",
        model_name="offline-model",
        client=FakeGroqClient(json.dumps({"text": private_text}), finish_reason),
    )

    with pytest.raises(ProviderError, match=f"finish_reason={finish_reason}") as captured:
        provider.generate_structured(_request())

    assert private_text not in str(captured.value)
    assert "private-key-not-used" not in str(captured.value)


@pytest.mark.parametrize("finish_reason", ["stop", None])
def test_groq_accepts_success_or_legacy_missing_finish_metadata(finish_reason: str | None) -> None:
    provider = GroqModelProvider(
        api_key="not-used",
        model_name="offline-model",
        client=FakeGroqClient('{"ok": true}', finish_reason),
    )

    assert provider.generate_structured(_request()).parsed == {"ok": True}


def test_groq_legacy_mock_without_finish_attribute_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeGroqClient('{"ok": true}')
    create = client.completions.create

    def without_finish(**kwargs: object) -> SimpleNamespace:
        response = create(**kwargs)
        del response.choices[0].finish_reason
        return response

    monkeypatch.setattr(client.completions, "create", without_finish)
    provider = GroqModelProvider(api_key="unused", model_name="offline", client=client)
    assert provider.generate_structured(_request()).parsed == {"ok": True}


def test_groq_unknown_finish_metadata_does_not_leak_arbitrary_text() -> None:
    private = "private document or key in unexpected metadata"
    provider = GroqModelProvider(
        api_key="unused", model_name="offline", client=FakeGroqClient('{"ok":true}', private)
    )
    with pytest.raises(ProviderError, match="finish_reason=unknown") as captured:
        provider.generate_structured(_request())
    assert private not in str(captured.value)


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


def test_groq_rate_limit_error_is_short_and_actionable() -> None:
    error = SimpleNamespace(
        status_code=429,
        body={
            "error": {
                "type": "tokens",
                "code": "rate_limit_exceeded",
                "message": (
                    "Request too large in organization org_private on output tokens per "
                    "minute (OTPM): Limit 1000, Requested 2750. Upgrade billing."
                ),
            }
        },
    )

    message = _safe_api_error_message(error)

    assert "HTTP 429" in message
    assert "OTPM limit 1000, this request needs 2750" in message
    assert "per-minute output allowance, not the context-window size" in message
    assert "can truncate extraction" in message
    assert "Waiting alone will not fix" in message
    assert "org_private" not in message
    assert "billing" not in message


@pytest.mark.parametrize("kind", ["TPM", "ITPM"])
def test_groq_input_rate_limit_does_not_claim_output_is_too_large(kind: str) -> None:
    error = SimpleNamespace(
        status_code=429,
        body={
            "error": {
                "code": "rate_limit_exceeded",
                "message": (f"Private organization: ({kind}): Limit 5,000, Requested 6,252"),
            }
        },
    )
    message = _safe_api_error_message(error)
    assert f"{kind} limit 5,000, this request needs 6,252" in message
    assert "do not reduce input tokens" in message
    assert "full-document output is too large" not in message
    assert "Private organization" not in message

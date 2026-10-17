"""Google Gemini implementation of provider-neutral structured generation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Literal

from google import genai
from google.genai import errors

from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TokenUsage,
)


def _prepare_gemini_schema(value: Any) -> Any:
    """Remove unsupported defaults and translate Pydantic literals to enums."""

    if isinstance(value, list):
        return [_prepare_gemini_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    prepared = {
        key: _prepare_gemini_schema(item) for key, item in value.items() if key != "default"
    }
    if "const" in prepared:
        prepared["enum"] = [prepared.pop("const")]
    return prepared


def _safe_api_error_message(exc: errors.APIError) -> str:
    """Keep actionable status data without including document or generated content."""

    code = getattr(exc, "code", None)
    if code == 429:
        return (
            "Gemini rate limit exceeded (HTTP 429). The free-tier quota may be "
            "exhausted; wait for it to reset before trying again."
        )

    details = [f"HTTP {code}"] if code is not None else []
    status = getattr(exc, "status", None)
    if status:
        details.append(str(status))
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        details.append(message[:500])
    return ", ".join(details) or type(exc).__name__


class GeminiModelProvider(ModelProvider):
    """Call Gemini structured output without using tools or background requests."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 0,
        max_output_tokens: int = 6000,
        temperature: float = 0.0,
        thinking_level: Literal["low", "medium", "high"] = "low",
        client: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._thinking_level = thinking_level
        self._client = client or genai.Client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResponse:
        config = {
            "system_instruction": request.system_prompt,
            "response_mime_type": "application/json",
            "response_json_schema": _prepare_gemini_schema(request.json_schema),
            "max_output_tokens": self._max_output_tokens,
            "temperature": self._temperature,
            "thinking_config": {"thinking_level": self._thinking_level},
            "http_options": {
                "timeout": int(self._timeout_seconds * 1000),
                "retry_options": {"attempts": self._max_retries + 1},
            },
        }

        started = perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=request.user_prompt,
                config=config,
            )
            content = response.text
        except errors.APIError as exc:
            raise ProviderError(
                f"Gemini API request failed ({_safe_api_error_message(exc)})."
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ProviderError(f"Gemini returned an unusable response: {exc}") from exc

        latency_ms = (perf_counter() - started) * 1000
        if not content:
            raise ProviderError("Gemini returned an empty structured response.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Gemini returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Gemini structured response must be a JSON object.")

        usage = getattr(response, "usage_metadata", None)
        candidate_tokens = getattr(usage, "candidates_token_count", None)
        thought_tokens = getattr(usage, "thoughts_token_count", None)
        output_tokens = None
        if candidate_tokens is not None or thought_tokens is not None:
            output_tokens = (candidate_tokens or 0) + (thought_tokens or 0)

        return StructuredGenerationResponse(
            provider=self.provider_name,
            model=getattr(response, "model_version", None) or self.model_name,
            raw_text=content,
            parsed=parsed,
            latency_ms=latency_ms,
            usage=TokenUsage(
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=output_tokens,
                total_tokens=getattr(usage, "total_token_count", None),
            ),
            provider_request_id=getattr(response, "response_id", None),
        )

"""Groq implementation of provider-neutral structured generation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Literal

import groq
from groq import Groq

from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TokenUsage,
)


def _prepare_strict_schema(value: Any) -> Any:
    """Translate small Pydantic schema details to Groq's strict JSON subset."""

    if isinstance(value, list):
        return [_prepare_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    prepared = {
        key: _prepare_strict_schema(item) for key, item in value.items() if key != "default"
    }
    if "const" in prepared:
        prepared["enum"] = [prepared.pop("const")]

    any_of = prepared.get("anyOf")
    if isinstance(any_of, list):
        variant_types = {variant.get("type") for variant in any_of if isinstance(variant, dict)}
        if {"integer", "number"} <= variant_types:
            prepared["anyOf"] = [
                variant
                for variant in any_of
                if not (isinstance(variant, dict) and variant.get("type") == "integer")
            ]
    return prepared


def _safe_api_error_message(exc: groq.APIError) -> str:
    """Retain actionable provider metadata without logging generated document text."""

    details: list[str] = []
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        details.append(f"HTTP {status_code}")

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("type", "code", "param"):
                value = error.get(key)
                if value:
                    details.append(f"{key}={value}")
            message = error.get("message")
            if isinstance(message, str) and message:
                details.append(f"message={message[:500]}")

    return ", ".join(details) or type(exc).__name__


class GroqModelProvider(ModelProvider):
    """Call GroqCloud while preserving raw output, usage, and request metadata."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        max_completion_tokens: int = 6000,
        reasoning_effort: Literal["none", "default", "low", "medium", "high"] = "low",
        temperature: float = 0.0,
        strict_structured_output: bool = True,
        client: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._max_completion_tokens = max_completion_tokens
        self._reasoning_effort = reasoning_effort
        self._temperature = temperature
        self._strict_structured_output = strict_structured_output
        self._client = client or Groq(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResponse:
        system_prompt = request.system_prompt
        if self._strict_structured_output:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": _prepare_strict_schema(request.json_schema),
                },
            }
        else:
            response_format = {"type": "json_object"}
            compact_schema = json.dumps(request.json_schema, separators=(",", ":"))
            system_prompt = (
                f"{system_prompt}\n\n"
                "Return one JSON object matching this exact JSON Schema. "
                "Every array element must match its declared item type; never insert "
                f"empty-string placeholders.\n<JSON_SCHEMA>{compact_schema}</JSON_SCHEMA>"
            )
        started = perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                response_format=response_format,
                max_completion_tokens=self._max_completion_tokens,
                reasoning_effort=self._reasoning_effort,
                temperature=self._temperature,
            )
        except groq.APIError as exc:
            raise ProviderError(
                f"Groq API request failed ({_safe_api_error_message(exc)})."
            ) from exc

        latency_ms = (perf_counter() - started) * 1000
        content = completion.choices[0].message.content
        if not content:
            raise ProviderError("Groq returned an empty structured response.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Groq returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Groq structured response must be a JSON object.")

        usage = getattr(completion, "usage", None)
        return StructuredGenerationResponse(
            provider=self.provider_name,
            model=getattr(completion, "model", None) or self.model_name,
            raw_text=content,
            parsed=parsed,
            latency_ms=latency_ms,
            usage=TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            ),
            provider_request_id=getattr(completion, "id", None),
        )

"""Google Gemini implementation of provider-neutral structured generation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Literal

import httpx
from google import genai
from google.genai import errors, types

from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TokenUsage,
)
from scione.providers.json_output import loads_exact


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
    if isinstance(status, str) and status in {
        "CANCELLED",
        "UNKNOWN",
        "INVALID_ARGUMENT",
        "DEADLINE_EXCEEDED",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "RESOURCE_EXHAUSTED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "INTERNAL",
        "UNAVAILABLE",
        "DATA_LOSS",
    }:
        details.append(status)
    # API error messages can quote prompts, URLs or supplied credentials.
    # Keep only recognized status metadata in the user-visible message.
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
        except errors.APIError as exc:
            raise ProviderError(
                f"Gemini API request failed ({_safe_api_error_message(exc)}).",
                latency_ms=(perf_counter() - started) * 1000,
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        except httpx.TransportError as exc:
            # SDK transport failures may contain request URLs or headers. They
            # still represent a failed attempt, not an exception that bypasses
            # persistence. Do not expose or serialize the underlying request.
            message = (
                "Gemini request timed out without a usable response."
                if isinstance(exc, httpx.TimeoutException)
                else "Gemini request failed because of a network connection error."
            )
            raise ProviderError(
                message,
                latency_ms=(perf_counter() - started) * 1000,
                provider=self.provider_name,
                model=self.model_name,
            ) from None
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                "Gemini returned an unusable response.",
                latency_ms=(perf_counter() - started) * 1000,
                provider=self.provider_name,
                model=self.model_name,
            ) from exc

        latency_ms = (perf_counter() - started) * 1000
        usage = getattr(response, "usage_metadata", None)
        candidate_tokens = getattr(usage, "candidates_token_count", None)
        thought_tokens = getattr(usage, "thoughts_token_count", None)
        output_tokens = None
        if candidate_tokens is not None or thought_tokens is not None:
            output_tokens = (candidate_tokens or 0) + (thought_tokens or 0)
        metadata = {
            "provider": self.provider_name,
            "model": getattr(response, "model_version", None) or self.model_name,
            "latency_ms": latency_ms,
            "usage": TokenUsage(
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=output_tokens,
                total_tokens=getattr(usage, "total_token_count", None),
            ),
            "provider_request_id": getattr(response, "response_id", None),
        }
        candidates = getattr(response, "candidates", None)
        try:
            content = response.text
        except (AttributeError, TypeError, ValueError) as exc:
            # Some SDK response.text accessors reject blocked/partial output.
            # Retain only answer text parts, never thinking parts or the SDK object.
            parts = (
                getattr(getattr(candidates[0], "content", None), "parts", []) if candidates else []
            )
            text = "".join(
                part.text
                for part in (parts or [])
                if isinstance(getattr(part, "text", None), str)
                and not getattr(part, "thought", False)
            )
            raise ProviderError(
                "Gemini returned an unusable structured response.",
                raw_text=text or None,
                **metadata,
            ) from exc
        if content is not None and not isinstance(content, str):
            raise ProviderError("Gemini returned an unusable structured response.", **metadata)
        finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        reason_value = getattr(finish_reason, "value", finish_reason)
        if reason_value is not None and reason_value != "STOP":
            # Restrict error text to SDK-defined metadata; never echo source output.
            safe_reason = (
                reason_value
                if isinstance(reason_value, str)
                and reason_value in {reason.value for reason in types.FinishReason}
                else "UNKNOWN"
            )
            raise ProviderError(
                "Gemini did not complete structured generation "
                f"(finish_reason={safe_reason}). No structured result was accepted.",
                raw_text=content,
                **metadata,
            )
        if not content:
            raise ProviderError(
                "Gemini returned an empty structured response.", raw_text=content, **metadata
            )

        try:
            parsed = loads_exact(content)
        except ValueError as exc:
            raise ProviderError(
                (
                    f"Gemini returned invalid JSON (line {exc.lineno}, column {exc.colno})."
                    if isinstance(exc, json.JSONDecodeError)
                    else "Gemini returned invalid or ambiguous JSON."
                ),
                raw_text=content,
                **metadata,
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderError(
                "Gemini structured response must be a JSON object.", raw_text=content, **metadata
            )

        return StructuredGenerationResponse(
            raw_text=content,
            parsed=parsed,
            **metadata,
        )

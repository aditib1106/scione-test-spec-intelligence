"""Construct configured model adapters for the customer-spec workflow."""

from __future__ import annotations

from scione.config import ConfigurationError, Settings
from scione.providers.base import ModelProvider
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider


def create_configured_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    """Build the selected adapter without coupling the extraction runner to a vendor."""

    selected_provider = (provider_name or settings.model_provider).strip().casefold()
    max_retries = 0 if settings.scione_single_attempt_mode else None
    if selected_provider == "groq":
        return GroqModelProvider(
            api_key=settings.require_groq_api_key(),
            model_name=model_name or settings.groq_model,
            timeout_seconds=settings.groq_timeout_seconds,
            max_retries=(settings.groq_max_retries if max_retries is None else max_retries),
            max_completion_tokens=settings.groq_max_completion_tokens,
            reasoning_effort=settings.groq_reasoning_effort,
            temperature=settings.groq_temperature,
            strict_structured_output=settings.groq_strict_structured_output,
        )
    if selected_provider == "gemini":
        return GeminiModelProvider(
            api_key=settings.require_gemini_api_key(),
            model_name=model_name or settings.gemini_model,
            timeout_seconds=settings.gemini_timeout_seconds,
            max_retries=(settings.gemini_max_retries if max_retries is None else max_retries),
            max_output_tokens=settings.gemini_max_output_tokens,
            temperature=settings.gemini_temperature,
            thinking_level=settings.gemini_thinking_level,
        )
    raise ConfigurationError(
        f"Unsupported provider={selected_provider!r}. "
        "Install an adapter and register it in create_configured_provider()."
    )

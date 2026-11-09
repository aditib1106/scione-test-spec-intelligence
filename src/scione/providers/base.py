"""Provider-neutral contracts for structured model generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderError(RuntimeError):
    """Safe failure message plus separately retained generation artifacts.

    Adapters pass only actual generated text and response metadata here, never
    request headers, credentials or whole SDK exceptions. ``raw_text`` may
    contain customer data and belongs in the authorized run artifact, not in
    user-facing errors or ordinary logs. Existing message-only callers remain
    compatible.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_text: str | None = None,
        usage: TokenUsage | None = None,
        latency_ms: float | None = None,
        provider: str | None = None,
        model: str | None = None,
        provider_request_id: str | None = None,
        generation_settings: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.usage = usage.model_copy(deep=True) if usage is not None else TokenUsage()
        self.latency_ms = latency_ms
        self.provider = provider
        self.model = model
        self.provider_request_id = provider_request_id
        self.generation_settings = dict(generation_settings or {})


class StructuredGenerationRequest(BaseModel):
    """All information a provider needs for one structured inference call."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    schema_name: str = Field(min_length=1)
    json_schema: dict[str, Any]


class TokenUsage(BaseModel):
    """Optional provider-reported token counts."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class StructuredGenerationResponse(BaseModel):
    """Raw and parsed output returned by a provider adapter."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    raw_text: str
    parsed: dict[str, Any]
    latency_ms: float = Field(ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider_request_id: str | None = None
    generation_settings: dict[str, Any] = Field(default_factory=dict)


class ModelProvider(ABC):
    """Generate JSON without exposing provider-specific APIs to the pipeline."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier used in run metadata."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Provider model identifier used for this adapter instance."""

    @property
    def generation_settings(self) -> dict[str, Any]:
        """Safe reproducibility metadata, without credentials or request headers."""
        return {}

    @abstractmethod
    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResponse:
        """Return one structured response for a versioned request."""

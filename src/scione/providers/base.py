"""Provider-neutral contracts for structured model generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderError(RuntimeError):
    """Raised when a model provider cannot produce a usable response."""


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

    @abstractmethod
    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResponse:
        """Return one structured response for a versioned request."""

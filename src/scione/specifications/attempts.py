"""Private, typed extraction failure artifacts without transport credentials.

Only explicit generation contracts and safe provider metadata belong here. Never
serialize provider instances, SDK errors, request headers or HTTP client objects.
Source prompts and generated text may contain customer data and are developer
artifacts rather than user-facing error messages.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scione.providers.base import (
    ProviderError,
    ProviderFailureKind,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TokenUsage,
)


class AttemptModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationFailure(AttemptModel):
    message: str
    raw_text: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float | None = None
    provider: str | None = None
    model: str | None = None
    provider_request_id: str | None = None
    generation_settings: dict = Field(default_factory=dict)
    failure_kind: ProviderFailureKind | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)

    @classmethod
    def from_provider_error(cls, error: ProviderError) -> "GenerationFailure":
        return cls(
            message=str(error),
            raw_text=error.raw_text,
            usage=error.usage,
            latency_ms=error.latency_ms,
            provider=error.provider,
            model=error.model,
            provider_request_id=error.provider_request_id,
            generation_settings=error.generation_settings,
            failure_kind=error.failure_kind,
            http_status=error.http_status,
        )


class ExtractionAttemptCall(AttemptModel):
    call_index: int = Field(ge=1)
    focus_pages: list[int]
    request: StructuredGenerationRequest
    response: StructuredGenerationResponse | None = None
    failure: GenerationFailure | None = None
    purpose: Literal["extraction", "inventory"] = "extraction"
    prompt_version: str | None = None


class ExtractionFailureArtifact(AttemptModel):
    schema_version: Literal["0.1"] = "0.1"
    attempt_id: str
    document_id: str
    started_at: datetime
    finished_at: datetime
    requested_provider: str
    requested_model: str
    strategy: str
    prompt_version: str
    generation_settings: dict = Field(default_factory=dict)
    status: Literal["failed"] = "failed"
    error_code: str
    error_message: str
    calls: list[ExtractionAttemptCall]

"""Orchestrate one whole-document test-method extraction run."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scione.prompts import (
    METHOD_EXTRACTION_PROMPT_VERSION,
    build_method_extraction_user_prompt,
    load_method_extraction_system_prompt,
)
from scione.providers import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
    TokenUsage,
)
from scione.schemas import IngestedDocument, TestMethodExtraction, TextExtractionStatus


class ExtractionError(RuntimeError):
    """Raised when an extraction run cannot produce validated semantic output."""


class MethodExtractionRun(BaseModel):
    """Reproducible record of one provider call and its validated result."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime
    document_id: str
    strategy: str
    prompt_version: str
    schema_version: str
    provider: str
    model: str
    latency_ms: float = Field(ge=0)
    usage: TokenUsage
    raw_response: str
    extraction: TestMethodExtraction


class MethodExtractionRunner:
    """Execute the current whole-document baseline through any provider adapter."""

    strategy_name = "whole_document"

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def run(self, document: IngestedDocument) -> MethodExtractionRun:
        if document.extraction_status != TextExtractionStatus.READY:
            raise ExtractionError(
                "Method extraction requires ready text; received "
                f"{document.extraction_status.value}."
            )

        request = StructuredGenerationRequest(
            system_prompt=load_method_extraction_system_prompt(),
            user_prompt=build_method_extraction_user_prompt(document),
            schema_name="test_method_extraction_v0_1",
            json_schema=TestMethodExtraction.model_json_schema(),
        )

        try:
            response = self.provider.generate_structured(request)
            extraction = TestMethodExtraction.model_validate(response.parsed)
        except ProviderError as exc:
            raise ExtractionError(f"Provider failed: {exc}") from exc
        except ValidationError as exc:
            raise ExtractionError(
                f"Provider output did not conform to TestMethodExtraction v0.1: {exc}"
            ) from exc

        return MethodExtractionRun(
            run_id=str(uuid4()),
            started_at=datetime.now(UTC),
            document_id=document.document_id,
            strategy=self.strategy_name,
            prompt_version=METHOD_EXTRACTION_PROMPT_VERSION,
            schema_version=extraction.schema_version,
            provider=response.provider,
            model=response.model,
            latency_ms=response.latency_ms,
            usage=response.usage,
            raw_response=response.raw_text,
            extraction=extraction,
        )

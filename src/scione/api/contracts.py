"""HTTP write contracts with explicit consent, edit versions and input fingerprints."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LabExample(WriteModel):
    version: int = Field(ge=1)


class SourceReview(WriteModel):
    run_id: str
    version: int = Field(ge=0)
    requirement_ids: list[str] = Field(min_length=1)
    decision: Literal["confirmed", "excluded", "needs_review"]
    note: str = Field(default="", max_length=4000)
    acknowledged: bool = False


class AnalysisRequest(WriteModel):
    provider: Literal["groq", "gemini"]
    model: str = Field(min_length=1, max_length=500)
    strategy: Literal["compact_document", "whole_document", "focused_passes"] = "compact_document"
    output_limit: int = Field(ge=512, le=65536)
    permission: bool = False
    free_tier_confirmed: bool = False


class ReportInput(WriteModel):
    run_id: str
    input_fingerprint: str


class ReportEdit(ReportInput):
    version: int = Field(ge=0)


class ScopeReview(ReportEdit):
    requirement_ids: list[str] = Field(min_length=1)
    supported: bool
    note: str = Field(default="", max_length=4000)
    acknowledged: bool = False


class MappingInput(WriteModel):
    canonical_test_id: str | None = None
    selected_method_codes: list[str] | None = None
    capability_id: str | None = None
    capability_verdict: Literal["unreviewed", "supported", "unsupported"] = "unreviewed"
    note: str = Field(default="", max_length=4000)

    @field_validator("selected_method_codes")
    @classmethod
    def distinct_methods(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Select each source method only once.")
        return value


class MappingReview(ReportEdit):
    requirement_id: str
    mapping: MappingInput


class MappingReset(ReportEdit):
    requirement_id: str


class QuantityReview(ReportEdit):
    requirement_id: str
    quantity: str | None
    scope_and_basis_confirmed: bool = False
    note: str = Field(default="", max_length=4000)


class ResetRequest(WriteModel):
    acknowledged: Literal[True]

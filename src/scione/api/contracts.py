"""HTTP write contracts with explicit consent, edit versions and input fingerprints."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scione.specifications.models import CustomerRequirement

ProviderName = Literal["groq", "gemini", "fireworks", "sail"]


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LabExample(WriteModel):
    version: int = Field(ge=1)
    example: Literal["legacy", "csp301", "csp301_typed", "csp301_detailed", "csp301_demo"] = (
        "legacy"
    )
    replace_existing: bool = False
    acknowledged: bool = False


class LibraryMetadata(WriteModel):
    company_name: str | None = Field(default=None, max_length=300)
    specification_id: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=500)
    revision: str | None = Field(default=None, max_length=300)
    specification_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    family_id: str | None = Field(default=None, max_length=32)
    expected_version: int = Field(default=0, ge=0)

    @field_validator("specification_date")
    @classmethod
    def valid_specification_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class LibraryRemoval(WriteModel):
    version: int = Field(ge=0)


class SourceReview(WriteModel):
    run_id: str
    version: int = Field(ge=0)
    requirement_ids: list[str] = Field(min_length=1)
    decision: Literal["confirmed", "excluded", "needs_review"]
    note: str = Field(default="", max_length=4000)
    acknowledged: bool = False
    facts_version: int = Field(default=0, ge=0)


class SourceFactsEdit(WriteModel):
    run_id: str
    expected_version: int = Field(ge=0)
    expected_review_version: int = Field(ge=0)
    requirements: list[CustomerRequirement] = Field(min_length=1, max_length=1000)
    note: str = Field(min_length=1, max_length=4000)


class SourceRequirementAdd(WriteModel):
    run_id: str
    expected_version: int = Field(ge=0)
    expected_review_version: int = Field(ge=0)
    requirement: CustomerRequirement
    note: str = Field(min_length=1, max_length=4000)


class AnalysisRequest(WriteModel):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=500)
    strategy: Literal[
        "inventory_compact_document", "compact_document", "whole_document", "focused_passes"
    ] = "inventory_compact_document"
    output_limit: int = Field(ge=512, le=65536)
    permission: bool = False
    free_tier_confirmed: bool = False
    billing_acknowledged: bool = False


class ReportInput(WriteModel):
    run_id: str
    input_fingerprint: str


class AssessmentReview(WriteModel):
    comparison_id: str = Field(min_length=1, max_length=32)
    input_fingerprint: str = Field(min_length=1, max_length=64)
    requirement_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=0)
    review_status: Literal["accepted", "pending"] = "accepted"
    decision: Literal["keep", "clarify_customer", "outsource", "decline"] = "keep"
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def explain_followup(self):
        if self.decision != "keep" and not self.note:
            raise ValueError("Add a note explaining the selected follow-up decision.")
        return self


class MatchingRequest(ReportInput):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)
    output_limit: int = Field(default=8000, ge=512, le=65536)
    permission: bool = False
    rerun: bool = False


class ReportEdit(ReportInput):
    version: int = Field(ge=0)


class ExclusionReview(ReportEdit):
    requirement_ids: list[str] = Field(min_length=1)
    excluded: bool
    note: str = Field(default="", max_length=4000)


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

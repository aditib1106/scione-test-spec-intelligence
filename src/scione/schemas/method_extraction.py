"""Versioned semantic schema for extracting a test-method document."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    """Base settings shared by model-facing structured outputs."""

    model_config = ConfigDict(extra="forbid")


class DocumentType(StrEnum):
    TEST_METHOD_STANDARD = "test_method_standard"
    CUSTOMER_SPECIFICATION = "customer_specification"
    UNKNOWN = "unknown"


class SourceEvidence(StrictSchema):
    page: int = Field(ge=1)
    clause: str | None
    quote: str = Field(min_length=1)


class DocumentIdentity(StrictSchema):
    document_type: DocumentType
    designation: str | None
    title: str
    version: str | None
    organization: str | None
    is_fictional: bool
    evidence: list[SourceEvidence]


class ExtractedTestItem(StrictSchema):
    source_name: str
    normalized_name: str
    description: str | None
    evidence: list[SourceEvidence]


class ProcedureCondition(StrictSchema):
    name: str
    raw_value: str
    unit: str | None
    evidence: list[SourceEvidence]


class ExposureDefinition(StrictSchema):
    name: str
    cycle_count: int | None = Field(ge=0)
    conditions: list[ProcedureCondition]
    evidence: list[SourceEvidence]


class ExtractedParameter(StrictSchema):
    name: str
    unit: str | None
    description: str | None
    evidence: list[SourceEvidence]


class ClassificationLevel(StrictSchema):
    label: str
    description: str
    evidence: list[SourceEvidence]


class TestMethodDefinition(StrictSchema):
    method_id: str
    name: str
    applicability: str | None
    exposures: list[ExposureDefinition]
    parameters: list[ExtractedParameter]
    classifications: list[ClassificationLevel]
    evidence: list[SourceEvidence]


class ReferencedDocument(StrictSchema):
    code: str
    title: str
    evidence: list[SourceEvidence]


class AcceptanceCriterion(StrictSchema):
    """A pass/fail threshold, not merely a procedure or classification definition."""

    method_id: str | None
    parameter: str
    operator: str
    value: str | int | float
    unit: str | None
    evidence: list[SourceEvidence]


class TestMethodExtraction(StrictSchema):
    """Canonical v0.1 output for one test-method document."""

    schema_version: Literal["0.1"]
    document: DocumentIdentity
    test_items: list[ExtractedTestItem]
    methods: list[TestMethodDefinition]
    referenced_documents: list[ReferencedDocument]
    acceptance_criteria: list[AcceptanceCriterion]
    warnings: list[str]

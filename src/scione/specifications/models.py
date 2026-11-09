"""Customer-request extraction contracts, separate from standard interpretation.

These fields record what a customer actually wrote. They are not normalized
catalog identities, lab capabilities, or inferred requirements from standards.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scione.providers import TokenUsage
from scione.structured.models import AtomicClaim

CUSTOMER_REQUIREMENTS_SCHEMA_VERSION = "0.1"

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
RequirementText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]


class CustomerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CustomerDocumentMetadata(CustomerModel):
    """Identity printed in the source; absent details must remain unknown."""

    title: ShortText | None = None
    identifier: ShortText | None = None
    revision: ShortText | None = None


class RequestedMethod(CustomerModel):
    """A source-stated method reference, not a claim of equivalence."""

    code: ShortText
    revision: ShortText | None = None
    role: RequirementText | None = None


class RequirementValue(CustomerModel):
    """Keep raw qualitative, ordinal, range, and table-based values intact."""

    name: ShortText
    raw_value: RequirementText
    unit: ShortText | None = None
    context: RequirementText | None = None


class RequirementEvidence(CustomerModel):
    page: int = Field(ge=1)
    section: ShortText | None = None
    quote: RequirementText


class CustomerRequirement(CustomerModel):
    """One requested test in its stated scope, with source evidence."""

    id: str = Field(pattern=r"^req_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    original_test_name: ShortText
    requested_methods: list[RequestedMethod] = Field(default_factory=list)
    conditions: list[RequirementValue] = Field(default_factory=list)
    acceptance_criteria: list[RequirementValue] = Field(default_factory=list)
    applicability: list[RequirementText] = Field(default_factory=list)
    special_instructions: list[RequirementText] = Field(default_factory=list)
    evidence: list[RequirementEvidence] = Field(min_length=1)
    atomic_claims: list[AtomicClaim] = Field(default_factory=list)


class CustomerExtraction(CustomerModel):
    """Validated extraction; catalog mapping and human decisions live elsewhere."""

    schema_version: Literal["0.1"]
    document: CustomerDocumentMetadata
    requirements: list[CustomerRequirement]
    review_issues: list[RequirementText] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_requirement_ids(self) -> Self:
        identifiers = [requirement.id for requirement in self.requirements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Customer requirement IDs must be unique within an extraction.")
        return self


class CompactRequirementEvidence(CustomerModel):
    """Short source fragments for the token-conscious extraction wire format."""

    page: int = Field(ge=1)
    quote: RequirementText


class CompactAtomicClaim(CustomerModel):
    """Token-conscious normalization inside the original extraction call.

    Numeric values retain source units. '=' uses [nominal] or [nominal,
    tolerance], 'range' uses [low, high], and 'one_of' lists discrete values.
    No source string or original provider response is replaced by these atoms.
    """

    parameter_id: ShortText | None
    kind: Literal["condition", "criterion", "reporting", "scope", "instruction"]
    stage: ShortText
    operator: Literal["=", "range", "<=", ">=", "<", ">", "one_of", "text", "required"]
    values: list[float] = Field(default_factory=list)
    unit: ShortText | None = None
    text_value: RequirementText | None = None
    raw_text: RequirementText
    source_pages: list[int] = Field(min_length=1)
    sequence: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def numeric_shape(self) -> Self:
        count = len(self.values)
        expected = {"range": (2,), "=": (1, 2), "<=": (1,), ">=": (1,), "<": (1,), ">": (1,)}
        if self.operator in expected and count not in expected[self.operator]:
            raise ValueError("Atomic numeric values do not match the operator.")
        if self.operator == "one_of" and not count:
            raise ValueError("Discrete values must not be empty.")
        if any(page < 1 for page in self.source_pages):
            raise ValueError("Source pages must be positive.")
        return self

    def expand(self, identifier: str) -> AtomicClaim:
        from decimal import Decimal

        values = [Decimal(str(value)) for value in self.values]
        fields = {}
        if self.operator == "range":
            fields = {"minimum": values[0], "maximum": values[1]}
        elif self.operator == "one_of":
            fields = {"values": values}
        elif self.operator not in {"required", "text"}:
            fields = {"value": values[0]}
            if len(values) == 2:
                fields["tolerance"] = values[1]
        return AtomicClaim(
            id=identifier,
            parameter_id=self.parameter_id,
            label=(self.parameter_id or "Unstructured requirement").replace("_", " ").title(),
            kind=self.kind,
            stage=self.stage,
            operator=self.operator,
            unit=self.unit,
            text_value=self.text_value,
            raw_text=self.raw_text,
            source_pages=self.source_pages,
            sequence=self.sequence,
            normalization_state="parsed" if self.parameter_id else "unresolved",
            **fields,
        )


class CompactCustomerRequirement(CustomerModel):
    """Reduce repeated JSON scaffolding, not the customer-stated requirements.

    Source phrasing remains in raw strings, including qualifications and units.
    Method roles and regional alternatives belong in scope/instructions; no
    remembered standard details or numeric interpretation are added locally.
    """

    test: ShortText
    methods: list[ShortText] = Field(default_factory=list)
    conditions: list[RequirementText] = Field(default_factory=list)
    criteria: list[RequirementText] = Field(default_factory=list)
    scope: list[RequirementText] = Field(default_factory=list)
    instructions: list[RequirementText] = Field(default_factory=list)
    evidence: list[CompactRequirementEvidence] = Field(min_length=1)
    uncertainty: RequirementText | None = None
    atomic_claims: list[CompactAtomicClaim] = Field(default_factory=list)


class CompactCustomerExtraction(CustomerModel):
    """Model-facing first pass; mapped without guessing into the review schema."""

    document: CustomerDocumentMetadata
    requirements: list[CompactCustomerRequirement]
    review_issues: list[RequirementText] = Field(default_factory=list)


class CustomerExtractionCall(CustomerModel):
    """One actual provider response, before any local aggregation or renumbering."""

    call_index: int = Field(ge=1)
    focus_pages: list[int] = Field(min_length=1)
    provider: ShortText
    model: ShortText
    latency_ms: float = Field(ge=0)
    usage: TokenUsage
    raw_response: Annotated[str, StringConstraints(strip_whitespace=False)]
    provider_request_id: ShortText | None = None


class CustomerExtractionRun(CustomerModel):
    """A reviewed draft and separately identifiable, untouched provider responses."""

    run_id: ShortText
    started_at: datetime
    document_id: ShortText
    strategy: Literal["whole_document", "focused_passes", "compact_document"] = "whole_document"
    prompt_version: ShortText
    schema_version: Literal["0.1"]
    provider: ShortText
    model: ShortText
    latency_ms: float = Field(ge=0)
    usage: TokenUsage
    # Unlike normalized field values, preserve provider text byte-for-byte in UTF-8.
    raw_response: Annotated[str, StringConstraints(strip_whitespace=False)]
    raw_response_kind: Literal["provider_text", "focused_pass_manifest", "manual_reference"] = (
        "provider_text"
    )
    calls: list[CustomerExtractionCall] = Field(default_factory=list)
    provider_request_id: ShortText | None = None
    extraction: CustomerExtraction

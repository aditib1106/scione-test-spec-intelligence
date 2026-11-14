"""Customer-request extraction contracts, separate from standard interpretation.

These fields record what a customer actually wrote. They are not normalized
catalog identities, lab capabilities, or inferred requirements from standards.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scione.numeric_literals import validate_numeric_complexity
from scione.providers import StructuredGenerationRequest, TokenUsage
from scione.structured.models import AtomicClaim

CUSTOMER_REQUIREMENTS_SCHEMA_VERSION = "0.1"

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
RequirementText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
AtomicIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
AtomicUnit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
AtomicConfiguration = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
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


class CustomerDocumentNotice(CustomerModel):
    """A proposed literal provenance note, independently checked before display."""

    kind: Literal["synthetic_provenance"] = "synthetic_provenance"
    page: int = Field(ge=1)
    quote: RequirementText


class CustomerDocumentNoticeAudit(CustomerModel):
    """A proposal's metadata/source status, separate from scientific review."""

    policy_version: Literal["provenance_notice_v2"] = "provenance_notice_v2"
    call_index: int = Field(ge=1)
    notice_index: int = Field(ge=1)
    proposal: CustomerDocumentNotice
    status: Literal["source_verified", "unused_unverified_metadata", "needs_review"]
    issues: list[RequirementText] = Field(default_factory=list)


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
    document_notices: list[CustomerDocumentNotice] = Field(default_factory=list)

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

    Numeric values retain source units and exact Decimal values after the
    provider's lossless JSON parse. '=' uses [nominal] or [nominal, tolerance],
    'range' uses [low, high], and 'one_of' lists discrete values. Native model
    wire values are numbers; saved artifacts reload exact decimal strings.
    Lab form numeric fields likewise travel as strings, avoiding JavaScript
    Number rounding before the typed backend. No original response is replaced.
    """

    parameter_id: AtomicIdentifier | None
    kind: Literal["condition", "criterion", "reporting", "scope", "instruction"]
    stage: AtomicIdentifier
    operator: Literal["=", "range", "<=", ">=", "<", ">", "one_of", "text", "required"]
    values: list[Decimal] = Field(default_factory=list)
    unit: AtomicUnit | None = None
    text_value: RequirementText | None = None
    raw_text: RequirementText
    source_pages: list[int] = Field(min_length=1)
    sequence: int | None = Field(default=None, ge=1)
    source_context: RequirementText | None = None
    configuration: AtomicConfiguration | None = None

    @model_validator(mode="after")
    def numeric_shape(self) -> Self:
        count = len(self.values)
        for value in self.values:
            validate_numeric_complexity(value)
        expected = {
            "range": (2,),
            "=": (1, 2),
            "<=": (1,),
            ">=": (1,),
            "<": (1,),
            ">": (1,),
            "text": (0,),
            "required": (0,),
        }
        if self.operator in expected and count not in expected[self.operator]:
            raise ValueError("Atomic numeric values do not match the operator.")
        if self.operator == "one_of" and not count and not self.text_value:
            raise ValueError("Discrete values must not be empty.")
        if any(page < 1 for page in self.source_pages):
            raise ValueError("Source pages must be positive.")
        return self

    def expand(self, identifier: str) -> AtomicClaim:
        values = list(self.values)
        textual_alternatives = self.operator == "one_of" and not values
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
            parameter_id=None if textual_alternatives else self.parameter_id,
            label=(self.parameter_id or "Unstructured requirement").replace("_", " ").title(),
            kind=self.kind,
            stage=self.stage,
            operator="text" if textual_alternatives else self.operator,
            unit=self.unit,
            text_value=self.text_value,
            raw_text=self.raw_text,
            source_pages=self.source_pages,
            sequence=self.sequence,
            source_context=self.source_context,
            configuration=self.configuration,
            normalization_state="parsed"
            if self.parameter_id and not textual_alternatives
            else "unresolved",
            **fields,
        )


class CompactMethodRole(CustomerModel):
    """A proposed source role, accepted only with a verified source quotation."""

    code: ShortText
    role: Literal[
        "primary", "required", "optional_approval_required", "alternative", "exposure",
        "measurement", "assessment", "calculation", "preparation",
    ]
    page: int = Field(ge=1)
    quote: RequirementText


class CompactCustomerRequirement(CustomerModel):
    """Reduce repeated JSON scaffolding, not the customer-stated requirements.

    Source phrasing remains in raw strings, including qualifications and units.
    Method roles and regional alternatives belong in scope/instructions; no
    remembered standard details or numeric interpretation are added locally.
    """

    test: ShortText
    methods: list[ShortText] = Field(default_factory=list)
    method_roles: list[CompactMethodRole] = Field(default_factory=list)
    conditions: list[RequirementText] = Field(default_factory=list)
    criteria: list[RequirementText] = Field(default_factory=list)
    scope: list[RequirementText] = Field(default_factory=list)
    instructions: list[RequirementText] = Field(default_factory=list)
    evidence: list[CompactRequirementEvidence] = Field(min_length=1)
    uncertainty: RequirementText | None = None
    atomic_claims: list[CompactAtomicClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_method_roles(self) -> Self:
        codes = [entry.code for entry in self.method_roles]
        roles: dict[str, set[str]] = {}
        for entry in self.method_roles:
            roles.setdefault(entry.code, set()).add(entry.role)
        if any(len(values) > 1 for values in roles.values()):
            raise ValueError("Conflicting source roles for one method require review.")
        if set(codes) - set(self.methods):
            raise ValueError("Method roles must reference this requirement's original methods.")
        return self


class CompactSharedRequirement(CustomerModel):
    """Source-stated clauses applied to explicit one-based test row references."""

    applies_to: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)
    conditions: list[RequirementText] = Field(default_factory=list)
    criteria: list[RequirementText] = Field(default_factory=list)
    scope: list[RequirementText] = Field(default_factory=list)
    instructions: list[RequirementText] = Field(default_factory=list)
    evidence: list[CompactRequirementEvidence] = Field(min_length=1)
    atomic_claims: list[CompactAtomicClaim] = Field(default_factory=list)


class CompactCustomerExtraction(CustomerModel):
    """Model-facing first pass; mapped without guessing into the review schema."""

    document: CustomerDocumentMetadata
    shared_requirements: list[CompactSharedRequirement] = Field(default_factory=list)
    requirements: list[CompactCustomerRequirement]
    review_issues: list[RequirementText] = Field(default_factory=list)
    document_notices: list[CustomerDocumentNotice] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_shared_targets(self) -> Self:
        for shared in self.shared_requirements:
            if len(shared.applies_to) != len(set(shared.applies_to)):
                raise ValueError("Shared source clauses must not repeat a target row.")
            if any(index > len(self.requirements) for index in shared.applies_to):
                raise ValueError("Shared source clauses must reference existing test rows.")
        return self


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
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    purpose: Literal["extraction", "inventory"] = "extraction"
    prompt_version: ShortText | None = None
    request: StructuredGenerationRequest | None = None


class CustomerInventoryAudit(CustomerModel):
    """Whether the source-reading proposal was safe to include as guidance.

    A verified quote does not certify the proposed inventory's semantics. Its
    untouched raw result belongs to the separately identified inventory call.
    """

    prompt_version: ShortText
    call_index: int = Field(ge=1)
    status: Literal[
        "source_verified_guidance", "source_verified_partial_guidance",
        "unused_invalid_schema", "unused_invalid_source",
        "unused_empty_inventory", "unused_input_budget",
    ]
    issues: list[RequirementText] = Field(default_factory=list)
    guidance_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    grounding_policy_version: ShortText | None = None
    omitted_test_indexes: list[int] = Field(default_factory=list)
    omitted_attachment_indexes: list[int] = Field(default_factory=list)


class CustomerGroundingAction(CustomerModel):
    """Auditable source-only citation enrichment, never a rewritten model fact."""

    policy_version: Literal["source_stage_citation_v1"] = "source_stage_citation_v1"
    action: Literal["added_stage_evidence", "stage_conflict"] = "added_stage_evidence"
    requirement_id: ShortText
    claim_ids: list[ShortText] = Field(min_length=1)
    page: int = Field(ge=1)
    source_line: int = Field(ge=1)
    quote: RequirementText
    citation: RequirementText | None = None
    stage: ShortText
    source_stage: ShortText


class CustomerAtomGroundingAction(CustomerModel):
    """An exact source span, without endorsing a model's semantic interpretation."""

    policy_version: Literal["source_atom_citation_v1"] = "source_atom_citation_v1"
    action: Literal["added_atom_evidence"] = "added_atom_evidence"
    requirement_id: ShortText
    claim_ids: list[ShortText] = Field(min_length=1)
    page: int = Field(ge=1)
    citation: RequirementText
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=1)


class CustomerUnitCitationAction(CustomerModel):
    """A physical summary states a named unit omitted by a body quotation."""

    policy_version: Literal["source_named_scale_citation_v1"] = "source_named_scale_citation_v1"
    action: Literal["added_unit_evidence"] = "added_unit_evidence"
    requirement_id: ShortText
    claim_ids: list[ShortText] = Field(min_length=1)
    page: int = Field(ge=1)
    citation: RequirementText
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=1)
    source_unit: ShortText
    source_operator: ShortText
    source_value: ShortText
    body_quote: RequirementText


class CustomerWireFormatAction(CustomerModel):
    """Lossless bookkeeping cleanup; all original provider bytes remain saved."""

    policy_version: Literal["compact_optional_blanks_v1"] = "compact_optional_blanks_v1"
    action: Literal["removed_blank_optional_entry"] = "removed_blank_optional_entry"
    path: ShortText


class CustomerMethodLabelAction(CustomerModel):
    """A known bank label is separated from its literal method designator."""

    policy_version: Literal["source_method_label_v1"] = "source_method_label_v1"
    action: Literal["normalized_method_label"] = "normalized_method_label"
    requirement_id: ShortText
    raw_reference: ShortText
    code: ShortText
    family_id: ShortText
    form: Literal["family_heading", "family_id_suffix"]
    page: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=1)
    code_start: int = Field(ge=0)
    code_end: int = Field(ge=1)
    parent_evidence_index: int = Field(ge=0)


class CustomerExtractionRun(CustomerModel):
    """A reviewed draft and separately identifiable, untouched provider responses."""

    run_id: ShortText
    started_at: datetime
    document_id: ShortText
    strategy: Literal[
        "whole_document", "focused_passes", "compact_document", "inventory_compact_document"
    ] = "whole_document"
    prompt_version: ShortText
    schema_version: Literal["0.1"]
    provider: ShortText
    model: ShortText
    latency_ms: float = Field(ge=0)
    usage: TokenUsage
    # Unlike normalized field values, preserve provider text byte-for-byte in UTF-8.
    raw_response: Annotated[str, StringConstraints(strip_whitespace=False)]
    raw_response_kind: Literal[
        "provider_text", "focused_pass_manifest", "manual_reference", "inventory_guided_manifest"
    ] = "provider_text"
    calls: list[CustomerExtractionCall] = Field(default_factory=list)
    provider_request_id: ShortText | None = None
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    grounding_actions: list[
        CustomerGroundingAction | CustomerAtomGroundingAction | CustomerMethodLabelAction
        | CustomerUnitCitationAction
    ] = Field(default_factory=list)
    wire_format_actions: list[CustomerWireFormatAction] = Field(default_factory=list)
    inventory_audit: CustomerInventoryAudit | None = None
    notice_audits: list[CustomerDocumentNoticeAudit] = Field(default_factory=list)
    extraction: CustomerExtraction

"""Versioned, provider-neutral semantic matching inputs and machine artifacts.

These contracts propose identity/parameter correspondences only. Source facts
remain in AtomicClaim; final capability, unit and numeric checks belong to the
deterministic comparator. Human edits are a separate downstream revision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scione.catalogs.models import LabCapability, ReferenceCatalog
from scione.providers.base import StructuredGenerationRequest, TokenUsage
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim, NormalizedRequirement, StructuredComparison

MATCHING_SCHEMA_VERSION = "0.1"
MATCHING_PROMPT_VERSION = "semantic_matching_v0_2"
MATCHING_VALIDATION_VERSION = "semantic_alignment_guards_v0_3"
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class MatchingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CapabilityCandidate(MatchingModel):
    """One existing lab declaration and its existing source-preserving claims."""

    capability: LabCapability
    claims: list[AtomicClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_claims(self) -> Self:
        ids = [claim.id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("Capability claim IDs must be unique inside a candidate.")
        return self


class RequirementCandidates(MatchingModel):
    """Explicit candidate boundary for one extracted requirement."""

    requirement: CustomerRequirement
    normalized: NormalizedRequirement
    catalog_test_ids: list[Identifier] = Field(default_factory=list)
    capability_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_requirement(self) -> Self:
        if self.requirement.id != self.normalized.requirement_id:
            raise ValueError("Normalized claims must belong to the source requirement.")
        for values in (self.catalog_test_ids, self.capability_ids):
            if len(values) != len(set(values)):
                raise ValueError("Candidate IDs must not be repeated.")
        ids = [claim.id for claim in self.normalized.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement claim IDs must be unique inside a requirement.")
        return self


class MatchingInput(MatchingModel):
    """Authorized input snapshot; IDs/revisions are supplied by the caller.

    SQL access and authorization deliberately live outside this package. A
    caller must first load data for the authenticated lab, then pass only that
    lab's candidate snapshot. The runner can validate references, not ownership
    of arbitrary caller-provided data.
    """

    schema_version: Literal["0.1"] = "0.1"
    lab_id: Identifier
    extraction_run_id: Identifier
    catalog_revision: Identifier
    capability_revision: Identifier
    review_revision: Identifier = "unreviewed"
    catalog: ReferenceCatalog
    capabilities: list[CapabilityCandidate] = Field(default_factory=list)
    requirements: list[RequirementCandidates] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_candidates(self) -> Self:
        tests = {test.id: test for test in self.catalog.tests}
        capabilities = {item.capability.id: item for item in self.capabilities}
        if len(capabilities) != len(self.capabilities):
            raise ValueError("Capability candidate IDs must be unique.")
        ids = [row.requirement.id for row in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique.")
        for candidate in self.capabilities:
            capability = candidate.capability
            if capability.canonical_test_id not in tests:
                raise ValueError("A capability references a test outside the catalog snapshot.")
            if set(capability.method_ids) - set(tests[capability.canonical_test_id].method_ids):
                raise ValueError("A capability references a method outside its catalog test.")
        for row in self.requirements:
            if set(row.catalog_test_ids) - tests.keys():
                raise ValueError("A requirement references an unknown catalog candidate.")
            if set(row.capability_ids) - capabilities.keys():
                raise ValueError("A requirement references an unknown capability candidate.")
            if any(
                capabilities[identifier].capability.canonical_test_id not in row.catalog_test_ids
                for identifier in row.capability_ids
            ):
                raise ValueError("Capability candidates must belong to candidate catalog tests.")
        return self


class ClaimAlignment(MatchingModel):
    """Parameter correspondence, never a declaration that values are satisfied."""

    requirement_claim_id: Identifier
    capability_claim_ids: list[Identifier] = Field(default_factory=list)
    relation: Literal["same_parameter", "unknown", "ambiguous"]
    reason: Text

    @model_validator(mode="after")
    def consistent_relation(self) -> Self:
        if len(self.capability_claim_ids) != len(set(self.capability_claim_ids)):
            raise ValueError("Aligned claim IDs must not be repeated.")
        if self.relation == "same_parameter" and not self.capability_claim_ids:
            raise ValueError("An aligned parameter needs an existing lab claim.")
        if self.relation != "same_parameter" and self.capability_claim_ids:
            raise ValueError("Unresolved alignments cannot select lab claims.")
        return self


class ItemAlignment(MatchingModel):
    requirement_id: Identifier
    identity_status: Literal["aligned", "unknown", "ambiguous"]
    canonical_test_id: Identifier | None = None
    capability_id: Identifier | None = None
    reason: Text
    claim_alignments: list[ClaimAlignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_identity(self) -> Self:
        if self.identity_status == "aligned" and self.canonical_test_id is None:
            raise ValueError("An aligned identity needs a catalog test.")
        if self.identity_status != "aligned" and (
            self.canonical_test_id is not None or self.capability_id is not None
        ):
            raise ValueError("An unresolved identity cannot select a test or capability.")
        if self.capability_id is None and any(
            row.capability_claim_ids for row in self.claim_alignments
        ):
            raise ValueError("Claim alignments require a selected capability.")
        return self


class SemanticMatchingOutput(MatchingModel):
    schema_version: Literal["0.1"] = "0.1"
    items: list[ItemAlignment]
    review_issues: list[Text] = Field(default_factory=list)


class AlignmentValidationIssue(MatchingModel):
    requirement_id: Identifier
    requirement_claim_id: Identifier | None = None
    code: Identifier
    reason: Text


class SemanticMatchingRun(MatchingModel):
    """One attempted provider call and immutable reproducibility artifacts."""

    run_id: Identifier
    started_at: datetime
    finished_at: datetime
    status: Literal["completed", "failed"]
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_version: str = MATCHING_PROMPT_VERSION
    validation_version: str = MATCHING_VALIDATION_VERSION
    projection_version: str | None = None
    provider: Identifier
    model: Identifier
    requested_provider: Identifier | None = None
    requested_model: Identifier | None = None
    input_snapshot: MatchingInput
    request: StructuredGenerationRequest
    raw_response: Annotated[str | None, StringConstraints(strip_whitespace=False)] = None
    parsed_response: dict[str, Any] | None = None
    proposal: SemanticMatchingOutput | None = None
    output: SemanticMatchingOutput | None = None
    validation_issues: list[AlignmentValidationIssue] = Field(default_factory=list)
    review_snapshot: dict[str, Any] | None = None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    validated_comparison: list[StructuredComparison] = Field(default_factory=list)
    latency_ms: float | None = Field(default=None, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider_request_id: str | None = None
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    error_code: Identifier | None = None
    error_message: Text | None = None

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.status == "completed" and (
            self.proposal is None or self.output is None or self.error_code is not None
        ):
            raise ValueError("A completed matching run needs validated output and no error.")
        if self.status == "failed" and (self.output is not None or self.error_code is None):
            raise ValueError("A failed run cannot expose an accepted output.")
        return self

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
MATCHING_PROMPT_VERSION = "semantic_matching_v0_11"
MATCHING_VALIDATION_VERSION = "semantic_alignment_guards_v0_24"
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


class ProposedEquivalence(MatchingModel):
    """A model-proposed wording correspondence between two existing strings.

    This is not a new source fact or a capability verdict. The validator binds
    both strings to the selected input pair and retains the proposal for audit.
    """

    requested_text: Text
    declared_text: Text
    reason: Text


class ClaimAlignment(MatchingModel):
    """Parameter correspondence, never a declaration that values are satisfied."""

    requirement_claim_id: Identifier
    capability_claim_ids: list[Identifier] = Field(default_factory=list)
    relation: Literal["same_parameter", "unknown", "ambiguous"]
    reason: Text
    context_equivalence: ProposedEquivalence | None = None
    value_equivalence: ProposedEquivalence | None = None
    joint_scope_equivalence: ProposedEquivalence | None = None
    context_role: (
        Literal[
            "method_context",
            "administrative",
            "already_represented",
            "sample_plan",
            "project_workflow",
        ]
        | None
    ) = None
    represented_by_source_ids: list[Identifier] = Field(default_factory=list)
    scope_relation: Literal["equivalent", "covered"] | None = None
    value_relation: Literal["equivalent", "covered", "incompatible"] | None = None

    @model_validator(mode="after")
    def consistent_relation(self) -> Self:
        if self.context_role is not None and self.relation != "unknown":
            raise ValueError("Source context notes cannot assert parameter correspondence.")
        if self.context_role is None and self.represented_by_source_ids:
            raise ValueError("Source context reference IDs need an explicit context role.")
        if (
            self.context_role in {"method_context", "already_represented"}
            and not self.represented_by_source_ids
        ):
            raise ValueError("Represented source notes need existing native source IDs.")
        if len(self.capability_claim_ids) != len(set(self.capability_claim_ids)):
            raise ValueError("Aligned claim IDs must not be repeated.")
        if self.relation == "same_parameter" and not self.capability_claim_ids:
            raise ValueError("An aligned parameter needs an existing lab claim.")
        if self.relation != "same_parameter" and self.capability_claim_ids:
            raise ValueError("Unresolved alignments cannot select lab claims.")
        if (
            self.context_equivalence or self.value_equivalence or self.joint_scope_equivalence
        ) and (self.relation != "same_parameter" or len(self.capability_claim_ids) != 1):
            raise ValueError("Wording equivalence requires one explicitly selected lab claim.")
        if self.joint_scope_equivalence and (self.context_equivalence or self.value_equivalence):
            raise ValueError("Joint scope wording cannot be combined with field-wise bridges.")
        if self.scope_relation and self.relation != "same_parameter":
            raise ValueError("Semantic scope needs an existing aligned lab claim.")
        if self.value_relation and (
            self.relation != "same_parameter" or len(self.capability_claim_ids) != 1
        ):
            raise ValueError("Semantic coverage needs one existing, explicitly selected lab claim.")
        if (self.scope_relation or self.value_relation) and (
            self.context_equivalence or self.value_equivalence or self.joint_scope_equivalence
        ):
            raise ValueError("Use semantic coverage or legacy wording bridges, not both.")
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
    """An individual provider attempt or an explicit parent execution artifact."""

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
    wire_format: Literal[
        "matching_array_v1",
        "matching_keyed_v1",
        "matching_keyed_v2",
        "matching_keyed_v3",
        "matching_keyed_v4",
        "matching_keyed_v5",
        "matching_batch_manifest_v1",
    ] = "matching_array_v1"
    execution_mode: Literal["single_call", "planned_batches"] = "single_call"
    planned_count: int = Field(default=1, ge=1)
    child_attempts: list[SemanticMatchingRun] = Field(default_factory=list)
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
        if self.execution_mode == "single_call":
            if self.child_attempts or self.planned_count != 1:
                raise ValueError("A single call cannot contain child attempts or a batch plan.")
        else:
            if self.raw_response is not None or self.parsed_response is not None:
                raise ValueError(
                    "A batch parent has no provider raw response; inspect its children."
                )
            if len(self.child_attempts) > self.planned_count:
                raise ValueError("Attempted batches cannot exceed the declared plan.")
            expected = {row.requirement.id: row for row in self.input_snapshot.requirements}
            capabilities = {row.capability.id: row for row in self.input_snapshot.capabilities}
            tests = {row.id: row for row in self.input_snapshot.catalog.tests}
            methods = {row.id: row for row in self.input_snapshot.catalog.methods}
            seen: set[str] = set()
            for child in self.child_attempts:
                if child.execution_mode != "single_call":
                    raise ValueError(
                        "Matching batch children must be individual provider attempts."
                    )
                if any(
                    getattr(child.input_snapshot, name) != getattr(self.input_snapshot, name)
                    for name in (
                        "lab_id",
                        "extraction_run_id",
                        "catalog_revision",
                        "capability_revision",
                        "review_revision",
                    )
                ):
                    raise ValueError("A matching child belongs to another source revision or lab.")
                for rows, original, key in (
                    (
                        child.input_snapshot.capabilities,
                        capabilities,
                        lambda row: row.capability.id,
                    ),
                    (child.input_snapshot.catalog.tests, tests, lambda row: row.id),
                    (child.input_snapshot.catalog.methods, methods, lambda row: row.id),
                ):
                    if any(
                        key(row) not in original
                        or row.model_dump(mode="json") != original[key(row)].model_dump(mode="json")
                        for row in rows
                    ):
                        raise ValueError("Matching child declaration or catalog facts changed.")
                for row in child.input_snapshot.requirements:
                    identifier = row.requirement.id
                    if identifier in seen or identifier not in expected:
                        raise ValueError(
                            "Matching children must have disjoint original source rows."
                        )
                    if row.model_dump(mode="json") != expected[identifier].model_dump(mode="json"):
                        raise ValueError("Matching child source facts or candidate IDs changed.")
                    seen.add(identifier)
            if self.status == "completed" and (
                len(self.child_attempts) != self.planned_count
                or any(child.status != "completed" for child in self.child_attempts)
                or seen != expected.keys()
            ):
                raise ValueError(
                    "A completed batch parent needs every planned source row and child."
                )
        return self

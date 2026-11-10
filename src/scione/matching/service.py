"""Explicit one-call semantic matching with retained artifacts and strict references.

No SQL, browser event, provider retry or numeric capability decision occurs here.
Callers authorize/read a fixed snapshot, persist the returned attempt (including
errors), then apply accepted semantic links to deterministic comparison.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from scione.catalogs.models import LabProfile, ReferenceCatalog, validate_lab_references
from scione.matching.keyed_wire import (
    KEYED_WIRE_VERSION,
    KeyedWireError,
    decode_keyed_output,
    keyed_output_schema,
)
from scione.matching.literal_text import literal_comparison_claim
from scione.matching.metadata import canonical_alignment_pair
from scione.matching.models import (
    MATCHING_PROMPT_VERSION,
    AlignmentValidationIssue,
    CapabilityCandidate,
    MatchingInput,
    RequirementCandidates,
    SemanticMatchingOutput,
    SemanticMatchingRun,
)
from scione.matching.parameter_policy import ParameterPolicyConflict, comparison_pair
from scione.matching.projection import (
    MATCHING_PROJECTION_VERSION,
    MatchingProjectionError,
    project_matching_input,
)
from scione.matching.semantics import SemanticEquivalenceError, semantic_pair
from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from scione.specifications.models import CustomerExtractionRun
from scione.structured.models import AtomicClaim, NormalizedRequirement
from scione.structured.normalization import normalize_capability, normalize_requirement

DEFAULT_MATCHING_INPUT_CHARACTER_LIMIT = 120_000


class SemanticMatchingError(RuntimeError):
    """An unsuccessful attempt, with safe user text and separately stored details."""

    def __init__(self, message: str, *, run: SemanticMatchingRun) -> None:
        super().__init__(message)
        self.run = run


class MatchingReferenceError(ValueError):
    """A proposal omitted an input or referenced something outside its candidates."""


def input_fingerprint(snapshot: MatchingInput) -> str:
    payload = json.dumps(
        snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_matching_input(
    run: CustomerExtractionRun,
    catalog: ReferenceCatalog,
    lab: LabProfile,
    *,
    capability_revision: str,
    review_revision: str = "unreviewed",
    catalog_revision: str | None = None,
    normalized_requirements: list[NormalizedRequirement] | None = None,
    candidate_test_ids: dict[str, list[str]] | None = None,
    candidate_method_ids: list[str] | tuple[str, ...] | None = None,
) -> MatchingInput:
    """Build a snapshot without inference or silent candidate truncation.

    An upstream retriever can supply per-requirement candidate IDs. Otherwise
    the current small catalog is included explicitly. A size guard in the runner
    rejects oversized requests before calling a provider; it never discards data.
    An explicit method allowlist projects unrelated catalog metadata out while
    retaining every referenced lab method. Source method strings remain in each
    requirement, including unresolved references. This helper does not apply human
    corrections: the caller supplies the chosen extraction/review revision and any
    corresponding normalized requirements.
    """

    validate_lab_references(lab, catalog)
    if normalized_requirements is None:
        normalized_requirements = [
            normalize_requirement(row) for row in run.extraction.requirements
        ]
    normalized = {row.requirement_id: row for row in normalized_requirements}
    requirement_ids = {row.id for row in run.extraction.requirements}
    if len(normalized) != len(normalized_requirements) or normalized.keys() != requirement_ids:
        raise ValueError("Provide exactly one normalized record for each extracted requirement.")
    if candidate_test_ids is not None and candidate_test_ids.keys() != requirement_ids:
        raise ValueError("Provide an explicit candidate list for every extracted requirement.")
    all_test_ids = [row.id for row in catalog.tests]
    requirements = []
    selected_test_ids: set[str] = set()
    for requirement in run.extraction.requirements:
        tests = all_test_ids if candidate_test_ids is None else candidate_test_ids[requirement.id]
        selected_test_ids.update(tests)
        requirements.append(
            RequirementCandidates(
                requirement=requirement,
                normalized=normalized[requirement.id],
                catalog_test_ids=tests,
                capability_ids=[
                    row.id for row in lab.capabilities if row.canonical_test_id in tests
                ],
            )
        )
    # Keep only requested candidate tests/methods, preserving source IDs and text.
    tests = [row for row in catalog.tests if row.id in selected_test_ids]
    capabilities = [row for row in lab.capabilities if row.canonical_test_id in selected_test_ids]
    if candidate_method_ids is None:
        method_ids = {identifier for row in tests for identifier in row.method_ids}
    else:
        method_ids = set(candidate_method_ids)
        if method_ids - {row.id for row in catalog.methods}:
            raise ValueError("Method candidates must be existing catalog identities.")
        method_ids.update(identifier for row in capabilities for identifier in row.method_ids)
        # Source references may belong to a different family from a human pin.
        # Keep their metadata without associating them with the pinned test.
        tests = [
            row.model_copy(update={"method_ids": [i for i in row.method_ids if i in method_ids]})
            for row in tests
        ]
    filtered_catalog = catalog.model_copy(
        update={"tests": tests, "methods": [row for row in catalog.methods if row.id in method_ids]}
    )
    snapshot = MatchingInput(
        lab_id=lab.id,
        extraction_run_id=run.run_id,
        catalog_revision=catalog_revision or f"{catalog.id}:{catalog.version}",
        capability_revision=capability_revision,
        review_revision=review_revision,
        catalog=filtered_catalog,
        capabilities=[
            CapabilityCandidate(capability=row, claims=normalize_capability(row))
            for row in capabilities
        ],
        requirements=requirements,
    )
    # Deep-copy and revalidate so later editor changes cannot alter the snapshot.
    return MatchingInput.model_validate(snapshot.model_dump(mode="json"))


def _fold(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def _unit_dimension(unit: str | None) -> str | None:
    value = _fold(unit).replace(" ", "")
    groups = {
        "temperature": {"°c", "°f", "celsius", "fahrenheit", "kelvin", "k"},
        "duration": {
            "s",
            "sec",
            "second",
            "seconds",
            "min",
            "minute",
            "minutes",
            "h",
            "hr",
            "hour",
            "hours",
            "day",
            "days",
        },
        "length": {"μm", "µm", "um", "mm", "cm", "m", "in", "inch", "inches"},
        "angle": {"°", "deg", "degree", "degrees", "rad"},
        "pressure": {"pa", "kpa", "mpa", "bar", "psi"},
        "load": {"n", "kn", "g", "kg", "kgf"},
        "humidity": {"%rh"},
        "gloss": {"gu"},
        "cycle_count": {"cycle", "cycles"},
    }
    return next((name for name, units in groups.items() if value in units), None)


def _compatible_context(request: AtomicClaim, declaration: AtomicClaim) -> str | None:
    """Reject explicit contradictions; this does not prove semantic correctness."""

    from scione.parameters import contexts_match, normalize_claim, resolve_definition
    from scione.parameters.units import resolve_unit

    request, declaration = normalize_claim(request), normalize_claim(declaration)

    if request.kind != declaration.kind and not (
        request.kind == "criterion" and declaration.kind == "reporting"
    ):
        return "The requested and declared claim roles are incompatible."
    first_definition = resolve_definition(request.parameter_id)
    second_definition = resolve_definition(declaration.parameter_id)
    if first_definition and second_definition and first_definition.id != second_definition.id:
        return "The two claims identify different known parameters."
    if not contexts_match(request, declaration):
        return "The stage, subject, source context, sequence or configuration is not equivalent."
    first_unit, second_unit = resolve_unit(request.unit), resolve_unit(declaration.unit)
    first = first_unit.dimension if first_unit else _unit_dimension(request.unit)
    second = second_unit.dimension if second_unit else _unit_dimension(declaration.unit)
    if first is not None and second is not None and first != second:
        return "The source units describe incompatible dimensions."
    return None


def validate_matching_output(
    snapshot: MatchingInput, proposal: SemanticMatchingOutput
) -> tuple[SemanticMatchingOutput, list[AlignmentValidationIssue]]:
    """Check all references and downgrade explicitly incompatible alignments.

    Missing/forged references reject the entire proposal. Context conflicts are
    retained as machine proposals but become unknown in the accepted output.
    This output still contains correspondences only, never numeric verdicts.
    """

    expected = {row.requirement.id: row for row in snapshot.requirements}
    received = [row.requirement_id for row in proposal.items]
    if len(received) != len(set(received)) or set(received) != expected.keys():
        raise MatchingReferenceError("Every input requirement must appear exactly once.")
    capabilities = {row.capability.id: row for row in snapshot.capabilities}
    output = proposal.model_copy(deep=True)
    issues: list[AlignmentValidationIssue] = []
    for row in output.items:
        source = expected[row.requirement_id]
        if (
            row.canonical_test_id is not None
            and row.canonical_test_id not in source.catalog_test_ids
        ):
            raise MatchingReferenceError("A test selection is outside the requirement candidates.")
        capability = None
        if row.capability_id is not None:
            if row.capability_id not in source.capability_ids:
                raise MatchingReferenceError("A capability selection is outside the candidates.")
            capability = capabilities[row.capability_id]
            if capability.capability.canonical_test_id != row.canonical_test_id:
                raise MatchingReferenceError("The capability belongs to another catalog test.")
        requested = {claim.id: claim for claim in source.normalized.claims}
        aligned = [claim.requirement_claim_id for claim in row.claim_alignments]
        if len(aligned) != len(set(aligned)) or set(aligned) != requested.keys():
            raise MatchingReferenceError("Every input claim must appear exactly once.")
        declared = {claim.id: claim for claim in capability.claims} if capability else {}
        used_configurations: set[str] = set()
        for alignment in row.claim_alignments:
            if set(alignment.capability_claim_ids) - declared.keys():
                raise MatchingReferenceError(
                    "An aligned lab claim is outside the selected capability."
                )
            for identifier in alignment.capability_claim_ids:
                configuration = declared[identifier].configuration
                if configuration:
                    used_configurations.add(configuration)
        for alignment in row.claim_alignments:
            reason = None
            metadata_actions = []
            if alignment.relation == "same_parameter":
                # Equal recorded values need no semantic value bridge. Ignore
                # a redundant proposal on this accepted copy only: the raw
                # response and original proposal remain unchanged for audit.
                # All parameter/context/configuration checks still run below.
                if alignment.value_equivalence and len(alignment.capability_claim_ids) == 1:
                    request = requested[alignment.requirement_claim_id]
                    declaration = declared[alignment.capability_claim_ids[0]]
                    if (
                        request.text_value
                        and request.text_value.strip()
                        and request.text_value == declaration.text_value
                    ):
                        alignment.value_equivalence = None
                        issues.append(
                            AlignmentValidationIssue(
                                requirement_id=row.requirement_id,
                                requirement_claim_id=alignment.requirement_claim_id,
                                code="unneeded_value_proposal_ignored",
                                reason="The recorded source and lab text values are already "
                                "exactly equal. An unnecessary value-wording proposal was "
                                "ignored; the original proposal remains in the artifact.",
                            )
                        )
                if len(used_configurations) > 1:
                    reason = "The proposal combines declarations from different configurations."
                else:
                    reason = next(
                        (
                            problem
                            for identifier in alignment.capability_claim_ids
                            if (
                                problem := _alignment_problem(
                                    requested[alignment.requirement_claim_id],
                                    declared[identifier],
                                    alignment,
                                    source.requirement,
                                    list(declared.values()),
                                    metadata_actions,
                                )
                            )
                        ),
                        None,
                    )
            for action in metadata_actions:
                issues.append(
                    AlignmentValidationIssue(
                        requirement_id=row.requirement_id,
                        requirement_claim_id=alignment.requirement_claim_id,
                        code="source_proved_metadata_canonicalization",
                        reason=json.dumps(action, ensure_ascii=False, sort_keys=True),
                    )
                )
            if reason:
                issues.append(
                    AlignmentValidationIssue(
                        requirement_id=row.requirement_id,
                        requirement_claim_id=alignment.requirement_claim_id,
                        code="incompatible_claim_context",
                        reason=reason,
                    )
                )
                alignment.relation = "unknown"
                alignment.capability_claim_ids = []
                alignment.context_equivalence = None
                alignment.value_equivalence = None
                alignment.joint_scope_equivalence = None
                alignment.reason = reason
    return output, issues


def _alignment_problem(
    request, declaration, alignment, source, declarations, actions
) -> str | None:
    try:
        literal = literal_comparison_claim(request, source)
        if literal is not None:
            request = literal
            actions.append(
                {
                    "field": "comparison_type",
                    "canonical": "verbatim_source_clause",
                    "rule": "complete_located_nonnumeric_source_text",
                }
            )
        request, declaration = comparison_pair(request, declaration)
        request, declaration, proved = canonical_alignment_pair(
            request,
            declaration,
            source,
            declarations,
            wording_bridge=bool(alignment.context_equivalence or alignment.joint_scope_equivalence),
        )
        actions.extend(proved)
        if (
            alignment.value_equivalence
            and request.text_value == declaration.text_value
            and any(action.get("rule") == "literal_reporting_body_and_purpose" for action in proved)
        ):
            # The literal whole output body, not an AI paraphrase, already
            # proves this harmless imperative/purpose placement difference.
            actions.append(
                {
                    "field": "value_equivalence",
                    "source": alignment.value_equivalence.model_dump(),
                    "canonical": None,
                    "rule": "unneeded_proposal_after_literal_body_proof",
                }
            )
            alignment.value_equivalence = None
        pair = semantic_pair(request, declaration, alignment, source=source)
    except (ParameterPolicyConflict, SemanticEquivalenceError) as exc:
        return str(exc)
    return _compatible_context(request, pair.declaration)


def _strict_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item) for key, item in value.items() if key != "default"}
    if isinstance(result.get("properties"), dict):
        result["required"] = list(result["properties"])
    return result


def matching_output_schema() -> dict[str, Any]:
    """Expose cross-field invariants to constrained generation, not only Python.

    Pydantic after-validators are not represented by its generated JSON Schema.
    Full ``anyOf`` object branches encode the same accepted wire shapes using
    object, enum, null and array-length constraints supported by our adapters.
    Runtime validation remains authoritative: a provider may ignore a constraint,
    and candidate ownership/exact coverage still require the supplied snapshot.
    This builder never changes or repairs a received model response.
    """
    schema = _strict_schema(SemanticMatchingOutput.model_json_schema())
    definitions = schema["$defs"]
    claim = deepcopy(definitions["ClaimAlignment"])

    def branch(base: dict, **properties: dict) -> dict:
        result = deepcopy(base)
        result["properties"].update(deepcopy(properties))
        return result

    aligned_claim = branch(
        claim,
        relation={"type": "string", "enum": ["same_parameter"]},
        capability_claim_ids={
            **claim["properties"]["capability_claim_ids"],
            "minItems": 1,
        },
    )
    unresolved_claim = branch(
        claim,
        relation={"type": "string", "enum": ["unknown", "ambiguous"]},
        capability_claim_ids={
            **claim["properties"]["capability_claim_ids"],
            "maxItems": 0,
        },
        context_equivalence={"type": "null"},
        value_equivalence={"type": "null"},
        joint_scope_equivalence={"type": "null"},
    )
    no_wording = branch(
        aligned_claim,
        context_equivalence={"type": "null"},
        value_equivalence={"type": "null"},
        joint_scope_equivalence={"type": "null"},
    )
    one_id = {**claim["properties"]["capability_claim_ids"], "minItems": 1, "maxItems": 1}
    equivalence = {"$ref": "#/$defs/ProposedEquivalence"}
    definitions["ClaimAlignment"] = {
        "anyOf": [
            no_wording,
            branch(
                aligned_claim,
                capability_claim_ids=one_id,
                context_equivalence=equivalence,
                joint_scope_equivalence={"type": "null"},
            ),
            branch(
                aligned_claim,
                capability_claim_ids=one_id,
                value_equivalence=equivalence,
                joint_scope_equivalence={"type": "null"},
            ),
            branch(
                aligned_claim,
                capability_claim_ids=one_id,
                context_equivalence={"type": "null"},
                value_equivalence={"type": "null"},
                joint_scope_equivalence=equivalence,
            ),
            unresolved_claim,
        ]
    }
    definitions["UnresolvedClaimAlignment"] = unresolved_claim

    item = deepcopy(definitions["ItemAlignment"])

    def identifier(name: str) -> dict:
        return deepcopy(
            next(
                option
                for option in item["properties"][name]["anyOf"]
                if option.get("type") == "string"
            )
        )

    unresolved_claims = {
        **item["properties"]["claim_alignments"],
        "items": {"$ref": "#/$defs/UnresolvedClaimAlignment"},
    }
    definitions["ItemAlignment"] = {
        "anyOf": [
            branch(
                item,
                identity_status={"type": "string", "enum": ["aligned"]},
                canonical_test_id=identifier("canonical_test_id"),
                capability_id=identifier("capability_id"),
            ),
            branch(
                item,
                identity_status={"type": "string", "enum": ["aligned"]},
                canonical_test_id=identifier("canonical_test_id"),
                capability_id={"type": "null"},
                claim_alignments=unresolved_claims,
            ),
            branch(
                item,
                identity_status={"type": "string", "enum": ["unknown", "ambiguous"]},
                canonical_test_id={"type": "null"},
                capability_id={"type": "null"},
                claim_alignments=unresolved_claims,
            ),
        ]
    }
    return schema


def prepare_matching_request(
    snapshot: MatchingInput, *, keyed: bool = False
) -> StructuredGenerationRequest:
    """Pure preflight using the same wire contract as an individual runner.

    This creates no provider, sends no request, and never changes the immutable
    source snapshot. Individual ``run`` failure artifacts remain unchanged.
    """
    snapshot = MatchingInput.model_validate(snapshot.model_dump(mode="json"))
    prompt = files("scione.prompts")
    prompt = prompt.joinpath(f"{MATCHING_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    if keyed:
        prompt += (
            "\nWIRE FORMAT: items is an object keyed by every requirement ID. "
            "claim_alignments is an object keyed by every claim ID. "
            "Return every required key exactly once. Do not repeat IDs inside values."
        )
    schema = matching_output_schema()
    payload = project_matching_input(snapshot).model_dump(mode="json")
    if keyed:
        from scione.matching.semantics import joint_scope_text

        payload["joint_scope_texts"] = {
            "requirements": {
                row.requirement.id: {
                    claim.id: text
                    for claim in row.normalized.claims
                    if (text := joint_scope_text(claim)) is not None
                }
                for row in snapshot.requirements
            },
            "capabilities": {
                row.capability.id: {
                    claim.id: text
                    for claim in row.claims
                    if (text := joint_scope_text(claim)) is not None
                }
                for row in snapshot.capabilities
            },
        }
    return StructuredGenerationRequest(
        system_prompt=prompt,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        schema_name="semantic_matching_keyed_v0_1" if keyed else "semantic_matching_v0_1",
        json_schema=keyed_output_schema(snapshot, schema) if keyed else schema,
    )


class SemanticMatchingRunner:
    """Run exactly one explicitly requested, bounded matching attempt."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_input_characters: int = DEFAULT_MATCHING_INPUT_CHARACTER_LIMIT,
    ) -> None:
        if (
            isinstance(max_input_characters, bool)
            or not isinstance(max_input_characters, int)
            or max_input_characters < 1
        ):
            raise ValueError("max_input_characters must be a positive integer.")
        self.provider = provider
        self.max_input_characters = max_input_characters

    def run(self, snapshot: MatchingInput) -> SemanticMatchingRun:
        # Revalidate nested instances and detach all mutable caller data.
        snapshot = MatchingInput.model_validate(snapshot.model_dump(mode="json"))
        started = datetime.now(UTC)
        keyed = self.provider.provider_name in {"sail", "fireworks"}
        request = StructuredGenerationRequest(
            system_prompt=files("scione.prompts")
            .joinpath(f"{MATCHING_PROMPT_VERSION}.txt")
            .read_text(encoding="utf-8"),
            user_prompt=json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False),
            schema_name="semantic_matching_v0_1",
            json_schema=matching_output_schema(),
        )
        fields: dict[str, Any] = {
            "run_id": str(uuid4()),
            "started_at": started,
            "input_fingerprint": input_fingerprint(snapshot),
            "provider": self.provider.provider_name,
            "model": self.provider.model_name,
            "requested_provider": self.provider.provider_name,
            "requested_model": self.provider.model_name,
            "input_snapshot": snapshot,
            "request": request,
            "projection_version": MATCHING_PROJECTION_VERSION,
            "generation_settings": self.provider.generation_settings,
            "wire_format": KEYED_WIRE_VERSION if keyed else "matching_array_v1",
        }

        def fail(code: str, message: str) -> SemanticMatchingError:
            return SemanticMatchingError(
                message,
                run=SemanticMatchingRun(
                    **fields,
                    finished_at=datetime.now(UTC),
                    status="failed",
                    error_code=code,
                    error_message=message,
                ),
            )

        try:
            request = prepare_matching_request(snapshot, keyed=keyed)
            fields["request"] = request
        except MatchingProjectionError as exc:
            raise fail(
                "input_projection_error",
                "The authoritative normalized facts do not preserve a source fact. "
                "No provider call was made; inspect the retained preflight input.",
            ) from exc

        count = (
            len(request.system_prompt)
            + len(request.user_prompt)
            + len(json.dumps(request.json_schema, ensure_ascii=False))
        )
        if count > self.max_input_characters:
            raise fail(
                "input_limit_exceeded",
                f"Matching input has {count:,} characters including prompts and schema; "
                f"the configured guard is {self.max_input_characters:,}. No model call "
                "was made and no candidate was truncated. This is not a token or cost guarantee.",
            )
        try:
            response = self.provider.generate_structured(request)
        except ProviderError as exc:
            # Adapter exception text can include URLs/provider details. Retain
            # actual generation artifacts, not arbitrary exception text or an
            # SDK response object containing headers/credentials.
            fields.update(
                raw_response=exc.raw_text,
                usage=exc.usage,
                latency_ms=exc.latency_ms,
                provider_request_id=exc.provider_request_id,
                generation_settings=exc.generation_settings or self.provider.generation_settings,
            )
            if exc.provider:
                fields["provider"] = exc.provider
            if exc.model:
                fields["model"] = exc.model
            raise fail(
                "provider_error",
                "The matching provider did not return a usable response. "
                "The failed attempt is retained; retry must be requested explicitly.",
            ) from exc
        fields.update(
            provider=response.provider,
            model=response.model,
            raw_response=response.raw_text,
            parsed_response=response.parsed,
            latency_ms=response.latency_ms,
            usage=response.usage,
            provider_request_id=response.provider_request_id,
            generation_settings=response.generation_settings or self.provider.generation_settings,
        )
        try:
            parsed = decode_keyed_output(response.parsed, snapshot) if keyed else response.parsed
            proposal = SemanticMatchingOutput.model_validate(parsed)
        except KeyedWireError as exc:
            raise fail(
                "invalid_output_references",
                "The matching response did not contain every required keyed input. "
                "No partial result was accepted; the raw response is retained.",
            ) from exc
        except ValidationError as exc:
            raise fail(
                "invalid_output_schema",
                f"Matching output failed schema validation ({exc.error_count()} errors). "
                "No output was accepted; the original response is retained.",
            ) from exc
        fields["proposal"] = proposal
        try:
            output, issues = validate_matching_output(snapshot, proposal)
        except MatchingReferenceError as exc:
            raise fail(
                "invalid_output_references",
                "Matching output did not cover the supplied inputs exactly or selected "
                "records outside its candidates. No output was accepted; "
                "inspect the retained attempt.",
            ) from exc
        return SemanticMatchingRun(
            **fields,
            finished_at=datetime.now(UTC),
            status="completed",
            output=output,
            validation_issues=issues,
        )

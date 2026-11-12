"""Explicit one-call semantic matching with retained artifacts and strict references.

No SQL, browser event, provider retry or numeric capability decision occurs here.
Callers authorize/read a fixed snapshot, persist the returned attempt (including
errors), then apply accepted semantic links to deterministic comparison.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from scione.catalogs.models import LabProfile, ReferenceCatalog, validate_lab_references
from scione.matching.compound import compound_dimensions_check
from scione.matching.coverage import coverage_pair
from scione.matching.keyed_wire import (
    KEYED_WIRE_VERSION,
    KeyedWireError,
    decode_keyed_output,
    keyed_output_schema,
)
from scione.matching.literal_text import _NUMBER_WORDS, literal_comparison_claim
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
from scione.matching.parameter_policy import (
    ParameterPolicyConflict,
    comparison_pair,
    source_proved_humidity_dimensions,
)
from scione.matching.projection import (
    MATCHING_PROJECTION_VERSION,
    MatchingProjectionError,
    project_matching_input,
)
from scione.matching.semantics import SemanticEquivalenceError, semantic_pair
from scione.matching.text_pool import pool_matching_text
from scione.matching.workflow_roles import mark_project_obligations, planning_role_problem
from scione.numeric_literals import source_numbers
from scione.parameters.units import resolve_unit
from scione.providers.base import (
    ModelProvider,
    ProviderError,
    StructuredGenerationRequest,
)
from scione.source_text import citation_contains, citation_text
from scione.specifications.models import CustomerExtractionRun
from scione.structured.lexical_grounding import _word_value, source_quantities
from scione.structured.models import AtomicClaim, NormalizedRequirement
from scione.structured.normalization import (
    _native_comparison_coverage,
    normalize_capability,
    normalize_requirement,
)

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
    mark_project_obligations(snapshot.requirements)
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


def _compatible_context(
    request: AtomicClaim, declaration: AtomicClaim, *, humidity_dimensions: bool = False
) -> str | None:
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
    dimension = "humidity" if humidity_dimensions else None
    first_unit = resolve_unit(request.unit, dimension=dimension)
    second_unit = resolve_unit(declaration.unit, dimension=dimension)
    first = first_unit.dimension if first_unit else _unit_dimension(request.unit)
    second = second_unit.dimension if second_unit else _unit_dimension(declaration.unit)
    if first is not None and second is not None and first != second:
        return "The source units describe incompatible dimensions."
    return None


def _semantic_note_coverage_problem(claim, selected, source) -> str | None:
    """AI owns descriptive equivalence; code retains quantities and critical qualifiers."""
    text = citation_text(claim.raw_text)
    for method in source.requirement.requested_methods:
        text = re.sub(re.escape(method.code), "method", text, flags=re.I)
    wanted = set(source_numbers(text))
    for match in _NUMBER_WORDS.finditer(text):
        value = _word_value(match.group(), None)
        if value is None:
            return "An unfamiliar source number remains unresolved."
        wanted.add(value)
    owned = {
        value
        for row in selected
        for value in (row.value, row.minimum, row.maximum, row.tolerance, *row.values)
        if value is not None
    }
    if not wanted <= owned:
        return "Selected native facts do not retain every source quantity."
    for quantity in source_quantities(text, context=text):
        unit = resolve_unit(quantity.unit)
        candidates = [
            row
            for row in selected
            if quantity.value in (row.value, row.minimum, row.maximum, row.tolerance, *row.values)
            and (
                row.unit == quantity.unit
                or (
                    unit is not None
                    and (native_unit := resolve_unit(row.unit)) is not None
                    and unit == native_unit
                )
            )
        ]
        if quantity.ambiguity or not candidates:
            return "A source unit or quantity is not retained by the cited native facts."
        if quantity.operator and not any(
            row.operator == quantity.operator
            or (quantity.role == "range_endpoint" and row.operator == "range")
            for row in candidates
        ):
            return "A source comparison operator is not retained by the cited native facts."
    metadata = " ".join(
        (row.text_value or "") + " " + (row.source_context or "") for row in selected
    ).casefold()
    critical = re.findall(
        r"\b(?:steel|alumini?um|nitrogen|polymer|plastic|water|air|fuel|solvent|"
        r"primer|substrate|equipment|fixture|chamber|ruler|material|medium|"
        r"each|every|individual|mean|maximum|minimum|unless|except|without|"
        r"mode|field|attribute|property|parameter)\b|[A-Z]{2,}[-_]?\d+[A-Za-z0-9_-]*",
        text,
        flags=re.I,
    )
    for term in critical:
        if term.casefold() not in metadata:
            return "A material, resource, code or reporting qualifier remains uncovered."
    if re.search(r"\b(?:no|not|none|never)\b|禁止|不得|无", text, re.I) and not re.search(
        r"\b(?:no|not|none|never)\b|禁止|不得|无",
        metadata,
        re.I,
    ):
        return "A negative source restriction is absent from the cited native facts."
    return None


def source_note_problem(claim, alignment, source) -> str | None:
    """Guard an explicit AI proposal to retain parser residue as a source note.

    This never establishes lab support. Native facts, unknown fields, quantities,
    materials/resources and incomplete source quotations remain requirements.
    """
    if alignment.context_role is None:
        return "No source-context classification was proposed."
    if alignment.context_role == "sample_plan":
        return planning_role_problem(claim, source)
    if alignment.context_role == "project_workflow":
        return (
            None
            if any(
                action.get("rule") == "shared_project_delivery_instruction"
                and action.get("claim_id") == claim.id
                for action in source.normalized.normalization_actions
            )
            else "Only a source-proved shared project delivery instruction may use this role."
        )
    natives = {row.id: row for row in source.requirement.atomic_claims}
    if claim.id in natives or claim.parameter_id is not None:
        return "Native or typed customer requirements cannot become context notes."
    if claim.kind == "criterion" and alignment.context_role != "already_represented":
        return "A specimen criterion cannot become administrative or descriptive context."
    if (
        claim.unit is not None
        or claim.values
        or claim.availability == "unsupported"
        or any(
            value is not None
            for value in (claim.value, claim.minimum, claim.maximum, claim.tolerance)
        )
    ):
        return "A quantitative or explicit negative requirement cannot become a context note."
    located = {
        evidence.page
        for evidence in source.requirement.evidence
        if citation_contains(evidence.quote, claim.raw_text)
    }
    if not claim.source_pages or not set(claim.source_pages) <= located:
        return "Source-note classification needs the complete located source quotation."
    selected_ids = alignment.represented_by_source_ids
    if len(selected_ids) != len(set(selected_ids)):
        return "Source context references repeat a native fact; the advisory note was not accepted."
    if set(selected_ids) - natives.keys():
        return "A source note references an absent native customer fact."
    normalized_native = {
        row.id: row
        for row in source.normalized.claims
        if row.id in natives and row.normalization_state != "unresolved"
    }
    if set(selected_ids) - normalized_native.keys():
        return "A source note references an unverified native customer fact."
    selected = [normalized_native[identifier] for identifier in selected_ids]
    if alignment.context_role == "already_represented":
        if claim.kind == "criterion" and any(row.kind != "criterion" for row in selected):
            return "A condition cannot stand in for a specimen acceptance criterion."
        proof = _native_comparison_coverage(
            claim.model_copy(update={"text_value": claim.raw_text}),
            [claim.model_copy(update={"text_value": claim.raw_text})],
            selected,
            clause=claim.raw_text,
            pages=claim.source_pages,
            requirement=source.requirement,
        )
        return None if proof else _semantic_note_coverage_problem(claim, selected, source)
    text = citation_text(claim.raw_text)
    for method in source.requirement.requested_methods:
        text = re.sub(re.escape(method.code), "method", text, flags=re.I)
    if source_numbers(text) or _NUMBER_WORDS.search(text):
        return "Unrepresented source numbers cannot become administrative or method context."
    if re.search(
        r"[%°±≤≥=<>]|\b(?:material|substrate|medium|equipment|fixture|instrument|tool|"
        r"chamber|steel|alumini?um|plastic|polymer|nitrogen|water|air|fuel|solvent|"
        r"temperature|humidity|pressure|hardness|thickness|tolerance|minimum|maximum|"
        r"threshold|per[- ]panel|per[- ]site|per[- ]location|individual|each|every)\b|"
        r"材料|设备|夹具|温度|湿度|压力|厚度|每个|逐一",
        text,
        re.I,
    ):
        return (
            "A scientific field, resource, material or reporting qualifier must remain comparable."
        )
    nonworkflow = re.sub(
        r"\b(?:(?:customer|buyer)\s+)?(?:sign[- ]off|approval)\s+required\b",
        "",
        text,
        flags=re.I,
    )
    if re.search(
        r"\b(?:use|using|operate|heat|cool|immerse|expose|maintain|apply|coat|prepare|"
        r"equip|requires?|required|without|except|unless|not|never)\b|禁止|不得|必须使用",
        nonworkflow,
        re.I,
    ):
        return "An execution obligation, exclusion or conditional limitation remains a requirement."
    if alignment.context_role == "administrative":
        if not re.search(
            r"\b(?:approval|sign[- ]off|release review|retain|retention|submit|deviations?)\b|"
            r"审批|签字|提交|留档",
            text,
            re.I,
        ):
            return "Administrative classification needs a workflow or recordkeeping statement."
        return None
    if re.search(
        r"\b(?:mode|setting|attribute|property|field|requirement|condition|parameter|"
        r"limit|must|shall|needs?)\b",
        text,
        re.I,
    ):
        return "An unfamiliar property or execution requirement cannot become method context."
    if not selected or not re.search(
        r"\b(?:method|procedure|approach|measurement|reference|scale|quantity)\b|方法|测量",
        text,
        re.I,
    ):
        return (
            "Method context needs cited native facts and an explicit descriptive method statement."
        )
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
        requested = {claim.id: claim for claim in source.normalized.claims}
        aligned = [claim.requirement_claim_id for claim in row.claim_alignments]
        if len(aligned) != len(set(aligned)) or set(aligned) != requested.keys():
            raise MatchingReferenceError("Every input claim must appear exactly once.")
        declared = {claim.id: claim for claim in capability.claims} if capability else {}
        owner_test_labels = ()
        if row.identity_status == "aligned" and row.canonical_test_id:
            selected_test = snapshot.catalog.test_by_id(row.canonical_test_id)
            owner_test_labels = (selected_test.name, *(a.text for a in selected_test.aliases))
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
        if capability and capability.capability.canonical_test_id != row.canonical_test_id:
            # Both IDs can be legitimate candidates while their proposed pair
            # is incompatible. Check every reference before downgrading this
            # row; a forged or missing link must still reject the whole output.
            native_ids = {claim.id for claim in source.requirement.atomic_claims}
            if any(set(alignment.represented_by_source_ids) - native_ids
                   for alignment in row.claim_alignments):
                raise MatchingReferenceError("A source context reference is outside the input.")
            reason = (
                "The selected lab capability belongs to another test item. "
                "This proposed correspondence was not accepted as lab support; "
                "the test identity and capability need review."
            )
            issues.append(AlignmentValidationIssue(
                requirement_id=row.requirement_id,
                code="incompatible_test_capability_owner",
                reason=reason,
            ))
            row.identity_status = "unknown"
            row.canonical_test_id = None
            row.capability_id = None
            row.reason = reason
            for alignment in row.claim_alignments:
                alignment.relation = "unknown"
                alignment.capability_claim_ids = []
                alignment.context_equivalence = None
                alignment.value_equivalence = None
                alignment.joint_scope_equivalence = None
                alignment.scope_relation = None
                alignment.value_relation = None
                alignment.context_role = None
                alignment.represented_by_source_ids = []
                alignment.reason = reason
            continue
        for alignment in row.claim_alignments:
            reason = None
            metadata_actions = []
            if alignment.context_role is not None:
                problem = source_note_problem(
                    requested[alignment.requirement_claim_id], alignment, source
                )
                issues.append(
                    AlignmentValidationIssue(
                        requirement_id=row.requirement_id,
                        requirement_claim_id=alignment.requirement_claim_id,
                        code="source_context_note_rejected"
                        if problem
                        else "source_context_note_accepted",
                        reason=problem
                        or f"AI {alignment.context_role} retained as context, not lab support.",
                    )
                )
                if problem:
                    alignment.context_role = None
                    alignment.represented_by_source_ids = []
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
                elif (
                    compound_dimensions_check(
                        requested[alignment.requirement_claim_id],
                        [declared[i] for i in alignment.capability_claim_ids],
                        source=source.requirement,
                    )
                    is not None
                ):
                    reason = None
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
                                    owner_test_labels=owner_test_labels,
                                    declared_method_codes=tuple(
                                        snapshot.catalog.method_by_id(identifier).identifier
                                        for identifier in capability.capability.method_ids
                                    ),
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
                alignment.scope_relation = None
                alignment.value_relation = None
                alignment.reason = reason
    return output, issues


def _alignment_problem(
    request, declaration, alignment, source, declarations, actions, *, owner_test_labels=(),
    declared_method_codes=(),
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
            owner_test_labels=owner_test_labels,
            declared_method_codes=declared_method_codes,
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
        declaration, coverage_actions = coverage_pair(
            request, declaration, alignment, source=source
        )
        actions.extend(coverage_actions)
        pair = semantic_pair(request, declaration, alignment, source=source)
    except (ParameterPolicyConflict, SemanticEquivalenceError) as exc:
        return str(exc)
    return _compatible_context(
        request, pair.declaration,
        humidity_dimensions=source_proved_humidity_dimensions(request, pair.declaration, source),
    )


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
        context_role={"type": "null"},
        represented_by_source_ids={"type": "array", "items": {"type": "string"}, "maxItems": 0},
        relation={"type": "string", "enum": ["same_parameter"]},
        capability_claim_ids={
            **claim["properties"]["capability_claim_ids"],
            "minItems": 1,
        },
    )
    unresolved_base = branch(
        claim,
        relation={"type": "string", "enum": ["unknown", "ambiguous"]},
        capability_claim_ids={
            **claim["properties"]["capability_claim_ids"],
            "maxItems": 0,
        },
        context_equivalence={"type": "null"},
        value_equivalence={"type": "null"},
        joint_scope_equivalence={"type": "null"},
        scope_relation={"type": "null"},
        value_relation={"type": "null"},
    )
    unresolved_claim = {
        "anyOf": [
            branch(
                unresolved_base,
                context_role={"type": "null"},
                represented_by_source_ids={
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 0,
                },
            ),
            branch(
                unresolved_base,
                relation={"type": "string", "enum": ["unknown"]},
                context_role={"type": "string", "enum": ["administrative"]},
            ),
            branch(
                unresolved_base,
                relation={"type": "string", "enum": ["unknown"]},
                context_role={"type": "string", "enum": ["sample_plan", "project_workflow"]},
                represented_by_source_ids={
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 0,
                },
            ),
            branch(
                unresolved_base,
                relation={"type": "string", "enum": ["unknown"]},
                context_role={"type": "string", "enum": ["method_context", "already_represented"]},
                represented_by_source_ids={
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            ),
        ]
    }
    no_wording = branch(
        aligned_claim,
        context_equivalence={"type": "null"},
        value_equivalence={"type": "null"},
        joint_scope_equivalence={"type": "null"},
        scope_relation={"type": "null"},
        value_relation={"type": "null"},
    )
    one_id = {**claim["properties"]["capability_claim_ids"], "minItems": 1, "maxItems": 1}
    equivalence = {"$ref": "#/$defs/ProposedEquivalence"}
    definitions["ClaimAlignment"] = {
        "anyOf": [
            no_wording,
            branch(
                no_wording,
                capability_claim_ids=one_id,
                scope_relation=claim["properties"]["scope_relation"],
                value_relation=claim["properties"]["value_relation"],
            ),
            branch(
                aligned_claim,
                capability_claim_ids=one_id,
                context_equivalence=equivalence,
                joint_scope_equivalence={"type": "null"},
                scope_relation={"type": "null"},
                value_relation={"type": "null"},
            ),
            branch(
                aligned_claim,
                capability_claim_ids=one_id,
                value_equivalence=equivalence,
                joint_scope_equivalence={"type": "null"},
                scope_relation={"type": "null"},
                value_relation={"type": "null"},
            ),
            branch(
                aligned_claim,
                capability_claim_ids=one_id,
                context_equivalence={"type": "null"},
                value_equivalence={"type": "null"},
                joint_scope_equivalence=equivalence,
                scope_relation={"type": "null"},
                value_relation={"type": "null"},
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
            "Return every required key exactly once. Do not repeat IDs inside values. "
            "Legacy *_equivalence fields are omitted from this compact wire; never emit them. "
            "Native-only rows omit context_role/represented_by_source_ids too."
        )
    schema = matching_output_schema()
    # Null-valued optional fields reconstruct from schema defaults. Keep every
    # actual value, including zero, False and empty lists; never trim quotations
    # or the nested JSON strings used by the joint-scope proof.
    payload = project_matching_input(snapshot).model_dump(mode="json", exclude_none=True)
    payload["native_source_claim_ids"] = {
        row.requirement.id: [claim.id for claim in row.requirement.atomic_claims]
        for row in snapshot.requirements
    }
    payload["project_obligation_ids"] = {
        row.requirement.id: [
            action["claim_id"]
            for action in row.normalized.normalization_actions
            if action.get("rule") == "shared_project_delivery_instruction"
        ]
        for row in snapshot.requirements
    }
    wire_payload = pool_matching_text(payload)
    if "claim_body_table" in wire_payload:
        prompt += (
            "\nINPUT SERIALIZATION: A lab claim {id,claim_body_ref} keeps its own ID "
            "and uses the EXACT fields in claim_body_table[claim_body_ref]. Read those "
            "fields in place, including any text_ref. This is lossless storage reuse, "
            "not permission to mix capabilities or assume equivalent services."
        )
    return StructuredGenerationRequest(
        system_prompt=prompt,
        user_prompt=json.dumps(
            wire_payload, ensure_ascii=False, separators=(",", ":")
        ),
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
                provider_failure_kind=exc.failure_kind,
                provider_http_status=exc.http_status,
            )
            if exc.provider:
                fields["provider"] = exc.provider
            if exc.model:
                fields["model"] = exc.model
            cause = {
                "timeout": "The AI service timed out while comparing these requirements. ",
                "network": "The connection to the AI service was interrupted. ",
                "http": "The AI service returned an error while comparing these requirements. ",
                "invalid_request": "The comparison request could not be prepared. ",
                "invalid_response": "The AI service returned an unreadable response. ",
            }.get(exc.failure_kind, "The matching provider did not return a usable response. ")
            raise fail(
                "provider_error", cause
                + "The failed attempt is retained; retry must be requested explicitly.",
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

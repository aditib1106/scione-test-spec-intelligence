"""Compile accepted semantic links into deterministic, source-preserving checks.

The model chooses correspondences, never capability verdicts. In particular an
unknown alignment must not accidentally use another similarly named lab row.
Method identity/edition decisions are supplied by the deterministic caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from scione.matching.literal_text import literal_comparison_claim
from scione.matching.metadata import canonical_alignment_pair
from scione.matching.models import SemanticMatchingRun
from scione.matching.optional_routes import optional_route_check
from scione.matching.parameter_policy import comparison_pair
from scione.matching.semantics import semantic_pair
from scione.matching.service import _compatible_context, validate_matching_output
from scione.parameters import normalize_claim, resolve_definition
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, ClaimCheck, StructuredComparison
from scione.structured.normalization import known_source_fact_problem

MethodStatus = Literal["supported", "gap", "unknown"]


def _unknown_check(claim: AtomicClaim, reason: str) -> ClaimCheck:
    return ClaimCheck(
        id=claim.id,
        parameter_id=claim.parameter_id,
        label=claim.label,
        kind=claim.kind,
        stage=claim.stage,
        unit=claim.unit,
        requested=claim.display_value,
        status="unknown",
        reason=reason,
        source_pages=list(claim.source_pages),
        requirement_claim=claim.model_copy(deep=True),
    )


def _exact_field_check(claim, normalized, source, declared, method_status):
    """A single registered field can be decisive without a model correspondence.

    This never searches another capability or uses names/custom IDs, and never
    overrides source uncertainty, missing scope, conflicting declarations or an
    unresolved method. The raw AI deferral remains stored unchanged.
    """
    if (
        method_status != "supported"
        or claim.normalization_state == "unresolved"
        or len({row.configuration for row in declared.values() if row.configuration}) > 1
        or not (definition := resolve_definition(claim.parameter_id))
        or known_source_fact_problem(claim, source.requirement)
    ):
        return None
    candidates = []
    for lab in declared.values():
        lab_definition = resolve_definition(normalize_claim(lab).parameter_id)
        if not lab_definition or lab_definition.id != definition.id:
            continue
        try:
            request, declaration = comparison_pair(claim, lab)
            request, declaration, actions = canonical_alignment_pair(
                request, declaration, source.requirement, list(declared.values())
            )
        except ValueError:
            continue
        if _compatible_context(request, declaration) is None:
            candidates.append((request, declaration, actions, lab))
    if len(candidates) != 1:
        return None
    request, declaration, actions, original_lab = candidates[0]
    comparison = compare_requirement(
        normalized.model_copy(update={"claims": [request], "review_issues": []}),
        [declaration],
        method_status=method_status,
    )
    check = comparison.checks[-1]
    if check.status not in {"supported", "gap"}:
        return None
    check.parameter_id = claim.parameter_id
    check.label = claim.label
    check.kind = claim.kind
    check.requirement_claim = claim.model_copy(deep=True)
    check.comparison_basis = "deterministic_exact_fields"
    check.comparison_metadata = actions
    if any(
        action.get("side") == "declaration" and action.get("field") == "text_value"
        for action in actions
    ):
        check.declared = original_lab.display_value
    return check


def _literal_field_check(
    literal, original, normalized, declared, method_status, source_requirement
):
    """An exact, complete descriptive declaration needs no invented parameter.

    This is scoped to the already selected test/method. A number, partial
    residual or unlocated quotation cannot reach this function as ``literal``.
    """
    if (
        literal is None
        or method_status != "supported"
        or len({row.configuration for row in declared.values() if row.configuration}) > 1
    ):
        return None
    candidates = []
    for lab in declared.values():
        if (
            lab.normalization_state == "unresolved"
            or lab.operator not in {"text", "required"}
            or not lab.text_value
        ):
            continue
        try:
            request, declaration = comparison_pair(literal, lab)
            request, declaration, actions = canonical_alignment_pair(
                request, declaration, source_requirement, list(declared.values())
            )
        except ValueError:
            continue
        if (
            request.text_value == declaration.text_value
            and _compatible_context(request, declaration) is None
        ):
            candidates.append((request, declaration, actions, lab))
    if len(candidates) != 1:
        return None
    request, declaration, actions, original_lab = candidates[0]
    result = compare_requirement(
        normalized.model_copy(update={"claims": [request], "review_issues": []}),
        [declaration],
        method_status=method_status,
    )
    check = result.checks[-1]
    if check.status not in {"supported", "gap"}:
        return None
    check.parameter_id = original.parameter_id
    check.label = original.label
    check.requirement_claim = original.model_copy(deep=True)
    check.comparison_basis = "deterministic_literal_text"
    check.declared = original_lab.display_value
    check.comparison_metadata = [
        *actions,
        {
            "rule": "complete_located_nonnumeric_source_text",
            "field": "comparison_type",
            "canonical": "verbatim_source_clause",
        },
    ]
    return check


def compile_matching_run(
    run: SemanticMatchingRun,
    method_status_by_requirement: Mapping[str, MethodStatus],
) -> list[StructuredComparison]:
    """Compare only accepted links against an explicitly supplied method policy.

    Parameters and numeric source data are never rewritten in ``run``. When
    names differ, comparison copies use an existing registered ID from either
    side (or the requested custom ID) solely for that check. Original IDs, values, units,
    contexts, availability and raw text remain unchanged and traceable.

    Every requirement needs a method status from the deterministic caller. An
    AI selection cannot turn a method gap into support. A missing/unresolved AI
    test/capability selection also cannot be certified just because the catalog
    contains a familiar method. This function does not persist human decisions.
    """

    if run.status != "completed" or run.output is None:
        raise ValueError("Only a completed matching run can be compiled.")
    requirements = {row.requirement.id: row for row in run.input_snapshot.requirements}
    if set(method_status_by_requirement) != requirements.keys():
        raise ValueError("Supply exactly one method status for every input requirement.")
    if any(
        status not in {"supported", "gap", "unknown"}
        for status in method_status_by_requirement.values()
    ):
        raise ValueError("Method status must be supported, gap or unknown.")
    # Validate again at the boundary, including persisted/reloaded objects. Never
    # compile the raw proposal or trust a caller-mutated list of claim IDs.
    accepted, _ = validate_matching_output(run.input_snapshot, run.output)
    alignments = {row.requirement_id: row for row in accepted.items}
    capabilities = {row.capability.id: row for row in run.input_snapshot.capabilities}
    comparisons = []
    for identifier, source in requirements.items():
        alignment = alignments[identifier]
        status = method_status_by_requirement[identifier]
        if status == "supported" and (
            alignment.identity_status != "aligned" or alignment.capability_id is None
        ):
            status = "unknown"
        normalized = source.normalized.model_copy(deep=True)
        method_comparison = compare_requirement(
            normalized.model_copy(update={"claims": []}), [], method_status=status
        )
        checks = method_comparison.checks
        # Reuse only the established non-capability workflow classification.
        # The complete source is needed to detect mixed operating clauses;
        # no capability, numeric/support judgment or guessed link is borrowed.
        workflow_notes = {
            check.id: check
            for check in compare_requirement(normalized, [], method_status=status).checks
            if check.status == "informational"
        }
        declared = (
            {row.id: row for row in capabilities[alignment.capability_id].claims}
            if alignment.capability_id is not None
            else {}
        )
        claim_links = {row.requirement_claim_id: row for row in alignment.claim_alignments}
        for claim in normalized.claims:
            link = claim_links[claim.id]
            route_note = optional_route_check(run, source, claim)
            if route_note is not None:
                checks.append(route_note)
                continue
            if claim.id in workflow_notes:
                checks.append(workflow_notes[claim.id])
                continue
            literal = literal_comparison_claim(claim, source.requirement)
            if (
                claim.normalization_state == "unresolved" or not claim.parameter_id
            ) and literal is None:
                checks.append(
                    _unknown_check(
                        claim,
                        "The source fact has not been normalized reliably; "
                        "semantic correspondence does not repair or replace source facts.",
                    )
                )
                continue
            if link.relation != "same_parameter":
                proved = _literal_field_check(
                    literal, claim, normalized, declared, status, source.requirement
                )
                proved = proved or _exact_field_check(claim, normalized, source, declared, status)
                checks.append(proved or _unknown_check(claim, link.reason))
                continue
            # The accepted link is scoped to one exact input claim. Do not pass
            # the full capability to the comparator, which could silently fall
            # back to an unrelated row sharing the same parameter name.
            policy_pairs = [
                comparison_pair(literal or claim, declared[lab_id])
                for lab_id in link.capability_claim_ids
            ]
            projected_pairs = [
                canonical_alignment_pair(
                    request,
                    lab,
                    source.requirement,
                    list(declared.values()),
                    wording_bridge=bool(link.context_equivalence or link.joint_scope_equivalence),
                )
                for request, lab in policy_pairs
            ]
            policy_pairs = [(request, lab) for request, lab, _actions in projected_pairs]
            metadata_actions = [
                {**action, "capability_claim_id": lab_id}
                for (_request, _lab, actions), lab_id in zip(
                    projected_pairs, link.capability_claim_ids, strict=True
                )
                for action in actions
            ]
            # Validation has rejected conflicting registered definitions. All
            # accepted declarations for one request must also share one policy;
            # otherwise an unfamiliar ID could mix unrelated known concepts.
            if len({request.parameter_id for request, _ in policy_pairs}) != 1:
                checks.append(
                    _unknown_check(claim, "Linked declarations use different parameter policies.")
                )
                continue
            comparison_claim = policy_pairs[0][0]
            pairs = [
                semantic_pair(request, lab, link, source=source.requirement)
                for request, lab in policy_pairs
            ]
            linked_claims = [pair.declaration for pair in pairs]
            one = compare_requirement(
                # Requirement-wide review is already retained once in the
                # initial method comparison; never duplicate it per claim.
                normalized.model_copy(update={"claims": [comparison_claim], "review_issues": []}),
                linked_claims,
                method_status=status,
            )
            for check in one.checks[1:]:
                # Canonical policy selection is not an edit to the customer's
                # stored claim or its displayed source identity.
                check.parameter_id = claim.parameter_id
                check.label = claim.label
                check.kind = claim.kind
                check.requirement_claim = claim.model_copy(deep=True)
                check.comparison_basis = (
                    "literal_descriptive_correspondence" if literal else "semantic_alignment"
                )
                check.comparison_metadata = list(metadata_actions)
                if link.joint_scope_equivalence:
                    check.comparison_metadata.append(
                        {
                            "field": "joint_scope_wording",
                            "semantic_inference": True,
                            "rule": "source_owned_cross_field_wording",
                            **link.joint_scope_equivalence.model_dump(),
                        }
                    )
                if any(
                    action.get("side") == "declaration" and action.get("field") == "text_value"
                    for action in metadata_actions
                ):
                    check.declared = "; ".join(
                        declared[lab_id].display_value for lab_id in link.capability_claim_ids
                    )
                if any(pair.applied for pair in pairs):
                    check.declared = "; ".join(
                        declared[lab_id].display_value for lab_id in link.capability_claim_ids
                    )
                    check.reason += (
                        " Wording correspondence uses an explicit AI proposal between "
                        "the recorded source and lab fields; original values remain preserved."
                    )
                checks.append(check)
        raw = source.requirement
        if not normalized.claims and any(
            (
                raw.conditions,
                raw.acceptance_criteria,
                raw.applicability,
                raw.special_instructions,
                raw.atomic_claims,
            )
        ):
            checks.append(
                ClaimCheck(
                    id=f"{identifier}_missing_normalized_claims",
                    parameter_id=None,
                    label="Unresolved source requirements",
                    kind="scope",
                    requested="Source-stated requirements retained in the extraction",
                    status="unknown",
                    reason="The source includes requirements but no usable normalized claims. "
                    "A method match alone does not establish that these requirements are covered.",
                    source_pages=sorted({row.page for row in raw.evidence}),
                )
            )
        counts = {
            state: sum(check.status == state for check in checks)
            for state in ("supported", "gap", "unknown")
        }
        if status == "gap":
            overall = "not_matched"
        elif counts["gap"]:
            overall = "partially_matched" if counts["supported"] else "not_matched"
        elif counts["unknown"]:
            overall = "needs_information"
        else:
            overall = "fully_matched"
        comparisons.append(
            StructuredComparison(
                requirement_id=identifier,
                status=overall,
                checks=checks,
                supported_count=counts["supported"],
                gap_count=counts["gap"],
                unknown_count=counts["unknown"],
            )
        )
    return comparisons

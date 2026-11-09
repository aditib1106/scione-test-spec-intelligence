"""Compile accepted semantic links into deterministic, source-preserving checks.

The model chooses correspondences, never capability verdicts. In particular an
unknown alignment must not accidentally use another similarly named lab row.
Method identity/edition decisions are supplied by the deterministic caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from scione.matching.models import SemanticMatchingRun
from scione.matching.service import validate_matching_output
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, ClaimCheck, StructuredComparison

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


def compile_matching_run(
    run: SemanticMatchingRun,
    method_status_by_requirement: Mapping[str, MethodStatus],
) -> list[StructuredComparison]:
    """Compare only accepted links against an explicitly supplied method policy.

    Parameters and numeric source data are never rewritten in ``run``. When
    names differ, a *copy* of a linked lab claim uses the requested parameter ID
    solely for that one deterministic check. Original claim IDs, values, units,
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
        declared = (
            {row.id: row for row in capabilities[alignment.capability_id].claims}
            if alignment.capability_id is not None
            else {}
        )
        claim_links = {row.requirement_claim_id: row for row in alignment.claim_alignments}
        for claim in normalized.claims:
            link = claim_links[claim.id]
            if claim.normalization_state == "unresolved" or not claim.parameter_id:
                checks.append(
                    _unknown_check(
                        claim,
                        "The source fact has not been normalized reliably; "
                        "semantic correspondence does not repair or replace source facts.",
                    )
                )
                continue
            if link.relation != "same_parameter":
                checks.append(_unknown_check(claim, link.reason))
                continue
            # The accepted link is scoped to one exact input claim. Do not pass
            # the full capability to the comparator, which could silently fall
            # back to an unrelated row sharing the same parameter name.
            linked_claims = [
                declared[lab_id].model_copy(deep=True, update={"parameter_id": claim.parameter_id})
                for lab_id in link.capability_claim_ids
            ]
            one = compare_requirement(
                normalized.model_copy(update={"claims": [claim]}),
                linked_claims,
                method_status=status,
            )
            checks.extend(one.checks[1:])
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

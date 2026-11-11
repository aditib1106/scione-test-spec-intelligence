"""Attribute saved explanation wording without changing the assessment snapshot."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from scione.matching.models import SemanticMatchingRun

ReasonSource = Literal["system", "ai", "ai_assisted", "unrecorded"]

_SYSTEM_BASES = {
    "deterministic_exact_fields",
    "deterministic_literal_text",
    "source_optional_route_policy",
}
_SEMANTIC_BASES = {"semantic_alignment", "literal_descriptive_correspondence"}
_CONTEXT_PREFIXES = (
    "Sample submission/allocation retained for scheduling and quoting; "
    "it is not a machine operating limit. ",
    "Shared project deliverable retained for project review; "
    "it does not establish another test capability. ",
    "Retained as source context, not a separate capability demand. ",
)
_SYSTEM_REASONS = {
    "The requested test/method is present in the selected lab declaration.",
    "The requested test/method is not listed by this lab.",
    "The requested test/method still needs an identity decision.",
    "The lab has no declaration for this parameter in the requested stage.",
    "The requested setting or range is covered by this lab declaration in the same context.",
    "The requested value or range is covered by this lab declaration in the same stage.",
    "The request exceeds this lab declaration's stated range in the same context.",
    "The requested value or range lies outside the lab's declared capability.",
    "The lab has not declared a complete selectable or reportable range.",
    "The lab has not declared a usable value for this requirement.",
    "The stage, subject, source context, sequence or configuration is not equivalent.",
    "The stage, subject, sequence, or selected configuration differs.",
    "The source fact has not been normalized reliably; "
    "semantic correspondence does not repair or replace source facts.",
    "Linked declarations use different parameter policies.",
    "The complete source-bound specimen allocation is retained for planning and quoting; "
    "it does not describe machine operating capacity.",
    "The source includes requirements but no usable normalized claims. "
    "A method match alone does not establish that these requirements are covered.",
    "Confirm the requested test/method before comparing this requirement with lab declarations.",
}


def _matching_evidence(matching_run):
    accepted = {}
    proposals = {}
    validation = {}
    pending = [matching_run] if matching_run is not None else []
    while pending:
        run = pending.pop(0)
        for name, destination in (("output", accepted), ("proposal", proposals)):
            output = getattr(run, name, None)
            for item in getattr(output, "items", ()):
                for alignment in item.claim_alignments:
                    key = (item.requirement_id, alignment.requirement_claim_id)
                    destination.setdefault(key, alignment)
        for issue in getattr(run, "validation_issues", ()):
            if issue.code == "incompatible_claim_context" and issue.requirement_claim_id:
                key = (issue.requirement_id, issue.requirement_claim_id)
                validation.setdefault(key, set()).add(issue.reason)
        pending.extend(getattr(run, "child_attempts", ()))
    return accepted, proposals, validation


def _reason_source(check, row, saved, accepted, proposals, validation) -> ReasonSource:
    reason = check.get("reason", "")
    basis = check.get("comparison_basis")
    metadata = check.get("comparison_metadata") or []
    if (
        row.get("comparison_engine", saved.get("comparison_engine")) == "deterministic_rules"
        or check.get("kind") in {"method", "source"}
        or check.get("parameter_id")
        in {"method", "test_method", "source_review", "lab_scope_review", "conditional_method"}
        or basis in _SYSTEM_BASES
        or any(entry.get("numeric_evaluation") is True for entry in metadata)
    ):
        return "system"
    key = (row.get("requirement_id"), check.get("id"))
    if reason in validation.get(key, ()):
        return "system"
    alignment = accepted.get(key)
    proposal = proposals.get(key)
    ai_reason = getattr(proposal, "reason", None)
    if alignment is not None:
        accepted_reason = alignment.reason
        if getattr(alignment, "context_role", None) and check.get("status") == "informational":
            if ai_reason == accepted_reason and any(
                reason == prefix + ai_reason for prefix in _CONTEXT_PREFIXES
            ):
                return "ai_assisted"
        elif alignment.relation != "same_parameter":
            if reason == accepted_reason:
                return "ai" if reason == ai_reason else "unrecorded"
        elif getattr(alignment, "value_relation", None) == "incompatible":
            if reason == accepted_reason and reason == ai_reason:
                return "ai"
        elif basis in _SEMANTIC_BASES:
            return "system"
    if any(
        entry.get("semantic_inference") is True and entry.get("reason") == reason
        for entry in metadata
    ):
        return "ai"
    if reason.partition(" Wording correspondence uses")[0] in _SYSTEM_REASONS:
        return "system"
    return "unrecorded"


def present_comparison_explanations(
    saved: dict, matching_run: SemanticMatchingRun | None = None
) -> dict:
    """Add wording attribution to a detached web view, without inference or storage writes."""
    result = deepcopy(saved)
    if matching_run is not None and saved.get("matching_run_id") not in {
        None,
        matching_run.run_id,
    }:
        matching_run = None
    accepted, proposals, validation = _matching_evidence(matching_run)
    sources = {}
    for row in result.get("requirements", ()):
        requirement_sources = sources.setdefault(row["requirement_id"], {})
        for check in row.get("comparison", {}).get("checks", ()):
            requirement_sources[check["id"]] = _reason_source(
                check, row, result, accepted, proposals, validation
            )
    result["explanation_sources"] = sources
    return result

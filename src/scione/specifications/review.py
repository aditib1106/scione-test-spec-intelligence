"""Conservative, deterministic triage for an extracted customer draft.

``ready`` means no automatic *row-level* flag was found. It is neither calibrated
AI confidence nor evidence of complete extraction, a catalog match, or lab support.
Document-level coverage warnings must remain visible independently of these states.
Human decisions are stored separately and never change the original extraction.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from scione.schemas import IngestedDocument
from scione.specifications.extraction import validate_customer_sources
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.pdf_preview import PDFPreviewError, inspect_pdf_highlights
from scione.specifications.store import RequirementReviewSet

REVIEW_POLICY_VERSION = "0.2"

RequirementState = Literal["needs_attention", "ready", "confirmed", "excluded"]
_REQUIREMENT_ID = re.compile(
    r"(?<![A-Za-z0-9_-])req_[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?![A-Za-z0-9_-])"
)


@dataclass(frozen=True)
class RequirementAssessment:
    """A workflow state with retained, explanatory automatic review flags."""

    state: RequirementState
    reasons: tuple[str, ...] = ()


def assess_requirements(
    run: CustomerExtractionRun,
    document: IngestedDocument,
    review: RequirementReviewSet | None = None,
    *,
    pdf_bytes: bytes | None = None,
) -> dict[str, RequirementAssessment]:
    """Assess rows without API calls or model self-confidence.

    Source citations are checked again so historical runs receive the same checks
    as new ones. Optional matching PDF bytes allow a formatting-only literal-source
    warning to be resolved when quotation segments form one exact source span or
    identify a unique detected table row, including repeated values in its cells. This
    is source location, not validation that an extracted value is semantically right.
    Model issues naming exact requirement IDs affect only those rows;
    unscoped or unrouteable issues conservatively flag every row. An explicit human
    confirmation or exclusion determines the displayed state, but does not erase
    automatic reasons or certify completeness. ``needs_review`` remains actionable.
    """

    if run.document_id != document.document_id:
        raise ValueError("Extraction and source document do not match.")
    if review is not None and (
        review.document_id != run.document_id or review.run_id != run.run_id
    ):
        raise ValueError("Human review belongs to a different extraction run.")
    if pdf_bytes is not None and (
        not isinstance(pdf_bytes, bytes) or hashlib.sha256(pdf_bytes).hexdigest() != document.sha256
    ):
        raise ValueError("PDF bytes do not match the source document.")

    requirements = run.extraction.requirements
    reasons: dict[str, list[str]] = {requirement.id: [] for requirement in requirements}
    source_issues = validate_customer_sources(run.extraction, document)
    located_issues = _located_source_issues(run, source_issues, pdf_bytes)
    issues = [
        *run.extraction.review_issues,
        *source_issues,
    ]
    for issue in issues:
        # Only an exact generated source-check message can be suppressed. Other
        # model cautions remain actionable, even when their source text is located.
        if issue in located_issues:
            continue
        named = set(_REQUIREMENT_ID.findall(issue))
        # Unknown IDs cannot safely be routed. Do not silently discard such issues.
        targets = named if named and named <= reasons.keys() else reasons.keys()
        for requirement_id in targets:
            if issue not in reasons[requirement_id]:
                reasons[requirement_id].append(issue)

    assessed: dict[str, RequirementAssessment] = {}
    for requirement in requirements:
        row_reasons = reasons[requirement.id]
        if not requirement.requested_methods:
            row_reasons.append("No test method was identified; check the source.")
        if not requirement.conditions and not requirement.acceptance_criteria:
            row_reasons.append(
                "No conditions or acceptance criteria were extracted; check the source."
            )

        state: RequirementState = "needs_attention" if row_reasons else "ready"
        decision = review.decisions.get(requirement.id) if review is not None else None
        if decision is not None:
            if decision.status == "confirmed":
                state = "confirmed"
            elif decision.status in {"excluded", "rejected"}:
                state = "excluded"
            elif decision.status == "needs_review":
                state = "needs_attention"
                row_reasons.append("Marked for review by a user.")
        assessed[requirement.id] = RequirementAssessment(state, tuple(row_reasons))
    return assessed


def _located_source_issues(
    run: CustomerExtractionRun, source_issues: list[str], pdf_bytes: bytes | None
) -> set[str]:
    """Recognize harmless quotation-format differences without rewriting warnings."""

    if pdf_bytes is None or not source_issues:
        return set()
    located: set[str] = set()
    by_page: dict[int, list[tuple[str, list[str]]]] = {}
    for requirement in run.extraction.requirements:
        for index, evidence in enumerate(requirement.evidence, start=1):
            prefix = f"{requirement.id} evidence {index}: quoted text was not found "
            matching_issues = [issue for issue in source_issues if issue.startswith(prefix)]
            if not matching_issues:
                continue
            by_page.setdefault(evidence.page, []).append((evidence.quote, matching_issues))
    for page, entries in by_page.items():
        for offset in range(0, len(entries), 100):
            batch = entries[offset : offset + 100]
            try:
                result = inspect_pdf_highlights(
                    pdf_bytes, page_number=page, quotes=tuple(quote for quote, _ in batch)
                )
            except PDFPreviewError:
                # Rendering/locator limitations are never evidence of a good row.
                continue
            if len(result.matches) != len(batch):
                continue
            for match, (_, matching_issues) in zip(result.matches, batch, strict=True):
                if match.status == "matched":
                    located.update(matching_issues)
    return located

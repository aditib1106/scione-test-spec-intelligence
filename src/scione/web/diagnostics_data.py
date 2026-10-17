"""Read-only projections for current customer-spec developer diagnostics.

These tables describe saved model output and local checks. They do not measure
test recall/accuracy, infer canonical identities, or certify lab capability.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping
from itertools import chain

from scione.schemas import IngestedDocument
from scione.specifications.extraction import validate_customer_sources
from scione.specifications.models import (
    CustomerExtractionRun,
    CustomerRequirement,
    RequirementValue,
)
from scione.specifications.pdf_preview import PDFPreviewError, inspect_pdf_highlights
from scione.specifications.review import RequirementAssessment
from scione.specifications.store import RequirementReviewSet

_STATE_LABELS = {
    "ready": "No row flags",
    "needs_attention": "Needs attention",
    "confirmed": "Confirmed",
    "excluded": "Excluded",
}


def run_summary(run: CustomerExtractionRun) -> dict[str, object]:
    """Flatten reported metadata; absent token counts remain unknown, not zero."""

    requirements = run.extraction.requirements
    return {
        "Run": run.run_id,
        "Started": run.started_at.isoformat(),
        "Provider": run.provider,
        "Model": run.model,
        "Prompt": run.prompt_version,
        "Strategy": run.strategy,
        "Records": len(requirements),
        "Method references": sum(len(row.requested_methods) for row in requirements),
        "Cited pages": len({item.page for row in requirements for item in row.evidence}),
        "Calls": len(run.calls) or (1 if run.strategy != "focused_passes" else None),
        "Latency (s)": round(run.latency_ms / 1000, 3),
        "Input tokens": run.usage.input_tokens,
        "Output tokens": run.usage.output_tokens,
        "Total tokens": run.usage.total_tokens,
    }


def _values_text(values: list[RequirementValue]) -> str:
    lines = []
    for value in values:
        detail = f"{value.name}: {value.raw_value}"
        if value.unit:
            detail += f" (unit: {value.unit})"
        if value.context:
            detail += f" [{value.context}]"
        lines.append(detail)
    return "\n".join(lines)


def _check_review(run: CustomerExtractionRun, review: RequirementReviewSet | None) -> None:
    if review is not None and (
        review.document_id != run.document_id or review.run_id != run.run_id
    ):
        raise ValueError("Human review belongs to a different extraction run.")


def requirement_rows(
    run: CustomerExtractionRun,
    assessments: Mapping[str, RequirementAssessment] | None = None,
    review: RequirementReviewSet | None = None,
) -> list[dict[str, object]]:
    """Show extracted fields and explicit review without inventing AI confidence."""

    _check_review(run, review)
    rows: list[dict[str, object]] = []
    for requirement in run.extraction.requirements:
        assessed = assessments.get(requirement.id) if assessments is not None else None
        state = _STATE_LABELS[assessed.state] if assessed else "Not assessed"
        decision = review.decisions.get(requirement.id) if review is not None else None
        if decision is not None:
            state = {
                "confirmed": "Confirmed",
                "excluded": "Excluded",
                "needs_review": "Needs attention",
            }[decision.status]
        rows.append(
            {
                "ID": requirement.id,
                "Test": requirement.original_test_name,
                "Methods": "; ".join(method.code for method in requirement.requested_methods),
                "Conditions": _values_text(requirement.conditions),
                "Criteria": _values_text(requirement.acceptance_criteria),
                "Scope": "\n".join(requirement.applicability),
                "Instructions": "\n".join(requirement.special_instructions),
                "Source pages": ", ".join(
                    str(page) for page in sorted({item.page for item in requirement.evidence})
                ),
                "Review state": state,
                "Review flags": "\n".join(assessed.reasons) if assessed else "",
            }
        )
    return rows


def evidence_rows(
    run: CustomerExtractionRun,
    document: IngestedDocument,
    pdf_bytes: bytes | None = None,
) -> list[dict[str, object]]:
    """Separate literal source-text checks from conservative PDF-word location."""

    if run.document_id != document.document_id:
        raise ValueError("Extraction and source document do not match.")
    if pdf_bytes is not None and (
        not isinstance(pdf_bytes, bytes) or hashlib.sha256(pdf_bytes).hexdigest() != document.sha256
    ):
        raise ValueError("PDF bytes do not match the source document.")

    issues = validate_customer_sources(run.extraction, document)
    pages = {page.page_number for page in document.pages}
    rows: list[dict[str, object]] = []
    for requirement in run.extraction.requirements:
        for index, evidence in enumerate(requirement.evidence, start=1):
            prefix = f"{requirement.id} evidence {index}:"
            valid_page = evidence.page in pages and evidence.page <= document.page_count
            text_check = (
                "Invalid page"
                if not valid_page
                else "Not found"
                if any(issue.startswith(prefix) for issue in issues)
                else "Found"
            )
            highlight = "Invalid page" if not valid_page else "Not checked"
            if valid_page and pdf_bytes is not None:
                try:
                    result = inspect_pdf_highlights(
                        pdf_bytes, page_number=evidence.page, quotes=(evidence.quote,)
                    )
                    highlight = {
                        "matched": "Located",
                        "partial": "Partly located",
                        "unmatched": "Not located",
                        "ambiguous": "Ambiguous",
                    }[result.matches[0].status]
                except PDFPreviewError:
                    highlight = "Unavailable"
            rows.append(
                {
                    "ID": requirement.id,
                    "Test": requirement.original_test_name,
                    "Page": evidence.page,
                    "Section": evidence.section or "",
                    "Quote": evidence.quote,
                    "Text check": text_check,
                    "Highlight": highlight,
                }
            )
    return rows


def _comparison_key(requirement: CustomerRequirement) -> tuple[str, tuple[str, ...]]:
    """Only whitespace/case formatting, never synonyms or standard equivalence."""

    return (
        " ".join(requirement.original_test_name.casefold().split()),
        tuple(
            sorted(
                {" ".join(item.code.casefold().split()) for item in requirement.requested_methods}
            )
        ),
    )


def _comparison_pages(requirements: list[CustomerRequirement]) -> str:
    return ", ".join(
        str(page) for page in sorted({item.page for row in requirements for item in row.evidence})
    )


def comparison_rows(
    baseline: CustomerExtractionRun, current: CustomerExtractionRun
) -> list[dict[str, object]]:
    """Compare two saved drafts, without pretending either is ground truth.

    IDs are run-local, so rows link only by case/whitespace-normalized test name
    and the exact method-code set. Duplicate signatures remain ambiguous. An
    unmatched row can be a rename/split/merge, not necessarily an added/lost test.
    """

    if baseline.document_id != current.document_id:
        raise ValueError("Only runs from the same source document can be compared.")
    old: dict[tuple[str, tuple[str, ...]], list[CustomerRequirement]] = defaultdict(list)
    new: dict[tuple[str, tuple[str, ...]], list[CustomerRequirement]] = defaultdict(list)
    for requirement in baseline.extraction.requirements:
        old[_comparison_key(requirement)].append(requirement)
    for requirement in current.extraction.requirements:
        new[_comparison_key(requirement)].append(requirement)

    rows = []
    for key in dict.fromkeys(chain(old, new)):
        before, after = old.get(key, []), new.get(key, [])
        if len(before) > 1 or len(after) > 1:
            change = "Ambiguous"
            detail = "Repeated name/method signature; no automatic row pairing."
        elif not before:
            change = "Selected only"
            detail = "No exact name/method link in the baseline; may be renamed, split, or new."
        elif not after:
            change = "Baseline only"
            detail = "No exact name/method link in the selected run; may be renamed or merged."
        else:
            changed = [
                name
                for name in CustomerRequirement.model_fields
                if name != "id" and getattr(before[0], name) != getattr(after[0], name)
            ]
            change = "Changed" if changed else "Unchanged"
            detail = (
                ", ".join(changed) if changed else "Same extracted fields; run-local IDs ignored."
            )
        display = (after or before)[0]
        rows.append(
            {
                "Change": change,
                "Test": display.original_test_name,
                "Methods": "; ".join(item.code for item in display.requested_methods),
                "Baseline IDs": ", ".join(item.id for item in before),
                "Selected IDs": ", ".join(item.id for item in after),
                "Baseline pages": _comparison_pages(before),
                "Selected pages": _comparison_pages(after),
                "Detail": detail,
            }
        )
    return rows

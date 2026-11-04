"""Business-facing PDF export of the same saved comparison shown in the UI."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from html import escape

import pymupdf

from scione.structured.models import ClaimCheck, StructuredComparison


class ComparisonPDFError(RuntimeError):
    """A saved structured assessment could not be exported without losing data."""


_STATUS = {
    "fully_matched": "Fully matched",
    "partially_matched": "Partially matched",
    "not_matched": "Not matched",
    "needs_information": "Needs information",
    "excluded": "Excluded",
    "supported": "Supported",
    "gap": "Gap",
    "unknown": "Needs information",
    "informational": "For reference",
}
_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #243247; line-height: 1.23; }
h1 { font-size: 24pt; line-height: 1.05; margin: 0 0 7pt; color: #162944; }
h2 { font-size: 13pt; margin: 15pt 0 5pt; color: #234b82; }
p { margin: 3pt 0 5pt; }
.eyebrow { font-size: 8pt; color: #596e8e; margin: 0 0 5pt; }
.meta { font-size: 8.4pt; color: #5a687d; }
.draft { font-size: 8.5pt; color: #87621f; margin: 8pt 0; }
.summary { background: #f0f4fb; padding: 9pt; margin: 9pt 0; }
.summary b { color: #203f6b; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0 8pt; font-size: 8pt; }
th { color: #294b78; text-align: left; font-size: 7.6pt; border-bottom: 1pt solid #becde2; }
td, th { padding: 5pt 4pt; border-bottom: 0.5pt solid #dce3ee; vertical-align: top; }
.test-head { page-break-inside: avoid; }
.stage, .source { font-size: 7.5pt; color: #64748b; }
.supported { color: #24704f; }
.gap { color: #a13e35; }
.unknown { color: #8a641c; }
.informational { color: #64748b; }
.long-check { border-bottom: 0.5pt solid #dce3ee; padding-bottom: 6pt; }
"""


def _text(value: object) -> str:
    """Escape every source string and allow long unbroken source tokens to wrap."""

    text = re.sub(
        r"\S{65,}",
        lambda match: "\u200b".join(
            match[0][index : index + 30] for index in range(0, len(match[0]), 30)
        ),
        str(value),
    )
    return escape(text, quote=True).replace("\n", "<br>")


def _value(document: dict, *keys: str, fallback: str = "Not supplied") -> str:
    for key in keys:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _date(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return "Not supplied"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%d %b %Y · %H:%M UTC")


def _methods(values: object) -> str:
    if not isinstance(values, list):
        return "Method not stated"
    result = []
    for value in values:
        text = (
            value
            if isinstance(value, str)
            else value.get("code")
            if isinstance(value, dict)
            else None
        )
        if text:
            result.append(str(text))
    return "; ".join(result) or "Method not stated"


def _stage(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _checked_rows(payload: dict) -> list[tuple[dict, StructuredComparison]]:
    requirements = payload.get("requirements", [])
    if not isinstance(requirements, list):
        raise ValueError("Requirements must be a list.")
    result = []
    for row in requirements:
        if not isinstance(row, dict):
            raise ValueError("Every test requires a comparison record.")
        if _excluded(row):
            continue
        comparison = StructuredComparison.model_validate(row.get("comparison", {}))
        result.append((row, comparison))
    return result


def _excluded(row: dict) -> bool:
    comparison = row.get("comparison")
    return bool(
        row.get("status") == "excluded"
        or row.get("excluded")
        or isinstance(comparison, dict)
        and comparison.get("status") == "excluded"
    )


def _signature(check: ClaimCheck) -> tuple:
    # Legacy unparsed instruction labels include the current test name. The
    # identical instruction's text, stage, result and evidence define its scope;
    # that presentation label must not force four copies of a common paragraph.
    label = (
        None
        if check.parameter_id is None and check.kind in {"instruction", "scope"}
        else check.label
    )
    return (
        check.parameter_id,
        label,
        check.kind,
        check.stage,
        check.unit,
        check.requested,
        check.declared,
        check.status,
        check.reason,
        tuple(sorted(set(check.source_pages))),
        check.requirement_claim.sequence if check.requirement_claim else None,
        check.requirement_claim.configuration if check.requirement_claim else None,
    )


def _shared_checks(
    rows: list[tuple[dict, StructuredComparison]],
) -> tuple[list[ClaimCheck], dict[str, list[str]], set[tuple]]:
    """Present identical shared clauses once while preserving their test scope.

    Identity, criteria and source-review decisions stay with individual tests.
    Different lab declarations or outcomes never collapse into a common row.
    """

    candidates: dict[tuple, tuple[ClaimCheck, list[int]]] = {}
    for index, (_row, comparison) in enumerate(rows):
        for check in comparison.checks:
            if check.kind not in {"condition", "scope", "instruction"}:
                continue
            signature = _signature(check)
            if signature not in candidates:
                candidates[signature] = (check, [])
            indices = candidates[signature][1]
            if index not in indices:
                indices.append(index)
    shared = []
    applicability = {}
    signatures = set()
    for signature, (check, indices) in candidates.items():
        if len(indices) < 2:
            continue
        shared.append(
            check.model_copy(update={"label": "Shared instruction"})
            if check.parameter_id is None and check.kind == "instruction"
            else check
        )
        applicability[check.id] = [
            rows[index][0].get("test_name", "Requested test") for index in indices
        ]
        signatures.add(signature)
    return shared, applicability, signatures


def _check_rows(
    comparison: StructuredComparison,
    applies_to: dict[str, list[str]] | None = None,
) -> str:
    parts = [
        "<table><tr><th width='30%'>Customer requirement</th>"
        "<th width='25%'>Lab declaration</th><th width='15%'>Result</th>"
        "<th width='30%'>Reason / action</th></tr>"
    ]
    for check in comparison.checks:
        requested = check.requested
        declared = check.declared or "Not recorded by the lab"
        reason = check.reason
        sequence = check.requirement_claim.sequence if check.requirement_claim else None
        stage_label = _stage(check.stage)
        if sequence is not None:
            stage_label += f" · Step {sequence}"
        stage = (
            f"<br><span class='stage'>{_text(stage_label)}</span>"
            if check.stage != "general" or sequence is not None
            else ""
        )
        sources = (
            "<br><span class='source'>Spec p. "
            + _text(", ".join(str(page) for page in sorted(set(check.source_pages))))
            + "</span>"
            if check.source_pages
            else ""
        )
        scope = (
            "<br><span class='stage'>Applies to: "
            + _text(", ".join(applies_to[check.id]))
            + "</span>"
            if applies_to and check.id in applies_to
            else ""
        )
        # Very long source prose needs flowing paragraphs. An unbreakable table
        # row must never consume the entire page or silently lose a requirement.
        if max(map(len, (requested, declared, reason))) > 600:
            parts.extend(
                [
                    "</table><div class='long-check'>",
                    f"<p><b>{_text(check.label)}</b>{stage}{sources}{scope}</p>",
                    f"<p><b>Customer requirement:</b> {_text(requested)}</p>",
                    f"<p><b>Lab declaration:</b> {_text(declared)}</p>",
                    f"<p class='{check.status}'><b>{_STATUS[check.status]}:</b> "
                    f"{_text(reason)}</p></div>",
                    "<table>",
                ]
            )
            continue
        parts.append(
            f"<tr><td><b>{_text(check.label)}</b><br>{_text(requested)}{stage}{sources}{scope}</td>"
            f"<td>{_text(declared)}</td><td class='{check.status}'>"
            f"<b>{_STATUS[check.status]}</b></td><td>{_text(reason)}</td></tr>"
        )
    parts.append("</table>")
    return "".join(parts)


def _html(payload: dict) -> str:
    document = payload.get("document", {})
    if not isinstance(document, dict):
        raise ValueError("Document metadata must be a record.")
    rows = _checked_rows(payload)
    counts = Counter(comparison.status for _, comparison in rows)
    shared, applicability, shared_signatures = _shared_checks(rows)
    title = _value(document, "title", "original_name", fallback="Customer specification")
    parts = [
        "<html><body><p class='eyebrow'>SCIONE · LAB WORKSPACE</p>",
        "<h1>Lab capability gap report</h1>",
        "<p class='draft'><b>DRAFT · For lab review</b></p>",
        f"<p><b>{_text(title)}</b></p>",
        f"<p class='meta'>Customer: {_text(_value(document, 'company_name'))}<br>"
        f"Specification: {_text(_value(document, 'specification_id', 'identifier'))} · "
        f"Revision: {_text(_value(document, 'revision'))}<br>"
        f"Lab: {_text(_value(payload, 'lab_name'))}<br>"
        f"Prepared: {_text(_date(payload.get('created_at')))}</p>",
        "<div class='summary'>",
        f"<p><b>{len(rows)} tests assessed</b></p>",
        f"<p>{counts['fully_matched']} fully matched · "
        f"{counts['partially_matched']} partially matched · "
        f"{counts['not_matched']} not matched · "
        f"{counts['needs_information']} need information</p></div>",
        "<p class='meta'>This report compares customer requirements with the lab's "
        "saved declarations. A match describes the ability to perform or report a test; "
        "it does not predict whether a specimen will pass. Missing information requires "
        "clarification and is kept separate from an explicit capability gap.</p>",
    ]
    if document.get("provider") == "manual_reference":
        parts.append("<p class='draft'><b>Prepared reference example; no model was called.</b></p>")
    excluded = sum(
        _excluded(row) for row in payload.get("requirements", []) if isinstance(row, dict)
    )
    if excluded:
        parts.append(f"<p class='meta'>{excluded} tests excluded from this assessment.</p>")
    if rows:
        parts.append(
            "<h2>Test overview</h2><table><tr><th width='40%'>Test</th>"
            "<th width='32%'>Requested method</th><th width='28%'>Assessment</th></tr>"
        )
        for row, comparison in rows:
            parts.append(
                f"<tr><td>{_text(row.get('test_name', 'Requested test'))}</td>"
                f"<td>{_text(_methods(row.get('methods')))}</td>"
                f"<td><b>{_STATUS[comparison.status]}</b></td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p>No active test requirements are included in this assessment.</p>")
    for index, (row, comparison) in enumerate(rows, 1):
        own_checks = [
            check for check in comparison.checks if _signature(check) not in shared_signatures
        ]
        parts.extend(
            [
                "<div class='test-head'>",
                f"<h2>{index:02d} · {_text(row.get('test_name', 'Requested test'))}</h2>",
                f"<p><b>{_STATUS[comparison.status]}</b> · "
                f"{_text(_methods(row.get('methods')))}</p></div>",
                (
                    "<p class='meta'>Shared preparation and reporting checks also apply; "
                    "see the shared requirements section below.</p>"
                    if len(own_checks) < len(comparison.checks)
                    else ""
                ),
                _check_rows(comparison.model_copy(update={"checks": own_checks})),
            ]
        )
    if shared:
        parts.extend(
            [
                "<h2>Shared requirements</h2>",
                "<p class='meta'>Identical requirements, declarations and outcomes are shown "
                "once here. The named tests retain these checks in their assessments above.</p>",
                _check_rows(
                    StructuredComparison(
                        requirement_id="shared_presentation",
                        status="needs_information",
                        checks=shared,
                    ),
                    applicability,
                ),
            ]
        )
    parts.append(
        "<p class='meta'>Pricing has not been calculated for this assessment. "
        "Resolve scope and missing declarations before preparing an approved quotation.</p>"
        "</body></html>"
    )
    return "".join(parts)


def render_comparison_pdf(payload: dict) -> bytes:
    """Render a saved assessment without model calls, source mutation or diagnostics."""

    try:
        story = pymupdf.Story(html=_html(payload), user_css=_CSS)
        media = pymupdf.paper_rect("a4")
        content = pymupdf.Rect(36, 62, media.width - 36, media.height - 45)

        def page_rect(_number: int, _filled: pymupdf.Rect) -> tuple:
            return media, content, None

        with story.write_with_links(page_rect) as pdf:
            total = len(pdf)
            for index, page in enumerate(pdf, 1):
                # Story's continued tables can leave an active clipping path in
                # the page content. Isolate it before drawing repeated chrome.
                page.wrap_contents()
                page.draw_rect(
                    pymupdf.Rect(0, 0, media.width, 42),
                    color=None,
                    fill=(0.10, 0.20, 0.36),
                )
                page.insert_text(
                    (36, 26),
                    "SCIONE  /  LAB CAPABILITY GAP REPORT",
                    fontsize=8,
                    fontname="hebo",
                    color=(1, 1, 1),
                )
                page.insert_text(
                    (media.width - 71, 26),
                    "DRAFT",
                    fontsize=8,
                    color=(1, 1, 1),
                )
                page.draw_line(
                    (36, media.height - 30),
                    (media.width - 36, media.height - 30),
                    color=(0.83, 0.87, 0.92),
                    width=0.5,
                )
                page.insert_text(
                    (36, media.height - 18),
                    "Declared capability assessment / For lab review",
                    fontsize=7,
                    color=(0.38, 0.43, 0.51),
                )
                page.insert_textbox(
                    pymupdf.Rect(
                        media.width - 125, media.height - 25, media.width - 36, media.height - 12
                    ),
                    f"Page {index} of {total}",
                    fontsize=7,
                    align=2,
                    color=(0.38, 0.43, 0.51),
                )
            pdf.set_metadata(
                {
                    "title": "Lab capability gap report - draft",
                    "author": "SciOne lab workspace",
                    "subject": "Customer requirements and declared lab capability",
                }
            )
            return pdf.tobytes(garbage=4, deflate=True, no_new_id=True)
    except Exception as exc:
        raise ComparisonPDFError(f"Unable to create the comparison PDF: {exc}") from exc

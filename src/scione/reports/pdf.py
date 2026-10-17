"""Concise, lab-facing PDF presentation of an immutable report snapshot.

The PDF is an operational handoff, not a developer audit archive. Full matching
diagnostics and provenance remain in the separately exported snapshot JSON.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC
from decimal import Decimal
from html import escape

import pymupdf

from scione.reports.matching import lab_fingerprint
from scione.reports.models import GapLine, ReportSnapshot
from scione.specifications.models import RequirementValue


class ReportPDFError(RuntimeError):
    """The saved report could not be rendered; no replacement data is invented."""


_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #233d36; line-height: 1.22; }
h1 { font-size: 22pt; line-height: 1.08; margin: 0 0 7pt; color: #143f34; }
h2 { font-size: 12pt; margin: 12pt 0 5pt; color: #176752;
     border-bottom: 0.6pt solid #b8d4c8; padding-bottom: 4pt; }
h3 { font-size: 10.2pt; line-height: 1.18; margin: 8pt 0 3pt; color: #163f34; }
p { margin: 2pt 0 3pt; }
.eyebrow { font-size: 8pt; color: #527267; margin: 0 0 5pt; }
.draft { color: #79571f; font-size: 8.5pt; margin: 6pt 0; }
.meta { font-size: 8.5pt; color: #526c62; }
.status { font-size: 8.5pt; color: #315f50; margin: 1pt 0 4pt; }
.pending { color: #79571f; }
.source { color: #60746a; font-size: 8pt; margin-top: 3pt; }
.item { border-bottom: 0.4pt solid #dce7df; padding-bottom: 4pt; }
.itemhead { page-break-inside: avoid; }
.summary { margin-bottom: 6pt; }
.overview { width: 100%; border-collapse: collapse; font-size: 8pt; margin: 5pt 0; }
.overview th { background: #eaf2ed; color: #234b3c; text-align: left; font-size: 7.8pt; }
.overview td, .overview th { border-bottom: 0.5pt solid #dce7df; padding: 5pt 4pt;
                            vertical-align: top; }
.overview .sub { font-size: 7.3pt; color: #526c62; }
"""


def _text(value: object) -> str:
    """Escape source strings and permit layout-only breaks in long tokens.

    Everything is flowing prose, not unbreakable table rows. Even an unusually
    long requirement can continue onto another page without clipping its text.
    """

    text = re.sub(
        r"\S{71,}",
        lambda match: "\u200b".join(
            match[0][index : index + 30] for index in range(0, len(match[0]), 30)
        ),
        str(value),
    )
    return escape(text, quote=True).replace("\n", "<br>")


def _paragraph(label: str, value: object, *, css_class: str = "") -> str:
    attribute = f" class='{css_class}'" if css_class else ""
    return f"<p{attribute}><b>{_text(label)}:</b> {_text(value)}</p>"


def _money(value: Decimal) -> str:
    # Supplied precision is retained. Rounding a reference rate could alter it.
    return format(value, "f")


def _rate(currency: str, amount: Decimal, basis: str | None) -> str:
    """Keep a supplied billing basis readable without writing '/ per panel'."""

    price = f"{currency} {_money(amount)}"
    if not basis:
        return price
    if basis.casefold().startswith("per "):
        return f"{price} {basis}"
    return f"{price} / {basis}"


def _scope_confirmed(line: GapLine, current_lab: str) -> bool:
    choice = line.mapping_choice
    return (
        line.status == "in_house"
        and choice is not None
        and choice.capability_verdict == "supported"
        and choice.lab_fingerprint == current_lab
    )


def _status_label(line: GapLine, current_lab: str) -> str:
    if _scope_confirmed(line, current_lab):
        return "Scope confirmed by lab reviewer"
    return {
        "in_house": "Method listed — scope to confirm",
        "method_gap": "Different method",
        "test_gap": "Not listed in lab catalog",
        "needs_review": "Match needs clarification",
        "condition_gap": "Outside declared scope",
        "excluded": "Excluded from scope",
    }[line.status]


def _value_text(value: RequirementValue) -> str:
    # Generic extraction labels add no information; specific parameter names do.
    generic = {"customer condition", "customer criterion", "customer criteria"}
    label = "" if value.name.casefold() in generic else f"{value.name}: "
    text = label + value.raw_value
    if value.unit:
        text += f" (unit: {value.unit})"
    if value.context:
        text += f" — {value.context}"
    return text


def _values(title: str, values: list[RequirementValue]) -> str:
    if not values:
        return _paragraph(title, "Not stated in the customer requirement.")
    return _paragraph(title, "; ".join(_value_text(value) for value in values))


def _business_note(line: GapLine, current_lab: str) -> str:
    """Explain the actionable gap without copying technical matching reasons."""

    notes = []
    if line.extraction_review_state == "needs_attention":
        notes.append("Customer details need confirmation against the specification.")
    if line.status == "method_gap":
        notes.append("The lab does not list every assessed method. Alternatives need approval.")
    elif line.status == "test_gap":
        notes.append("Ask the lab about availability; no capability is declared for this test.")
    elif line.status == "condition_gap":
        notes.append("The lab reviewer marked the requested conditions or scope unsupported.")
    elif line.status == "in_house" and not _scope_confirmed(line, current_lab):
        notes.append("Lab review of customer conditions, acceptance criteria and scope is pending.")
    return " ".join(notes)


def _pricing(line: GapLine) -> str:
    if line.quote is not None:
        quote = line.quote
        return _paragraph(
            "Draft line estimate",
            f"{_money(quote.quantity)} × "
            f"{_rate(quote.currency, quote.unit_price, quote.pricing_basis)} "
            f"= {quote.currency} {_money(quote.subtotal)}",
        ) + _paragraph("Quantity / scope note", quote.note)
    capability = line.capability
    if line.status == "in_house" and capability is not None and capability.price is not None:
        return _paragraph(
            "Reference rate",
            f"{_rate(capability.currency, capability.price, capability.pricing_basis)} "
            "(not a quote; quantity and scope not priced)",
        )
    # The summary makes unpriced scope explicit once; repeating the same empty
    # pricing field for every requirement obscures the actual customer details.
    return ""


def _listed_overview(active: list[GapLine], current_lab: str) -> str:
    """Surface available declarations and real prices before unresolved records.

    This is a short business overview, not a duplicate index of every extracted
    row. Full customer conditions and all remaining requirements follow below.
    A bounded preview avoids turning a long catalog into a second full report.
    """

    listed = [line for line in active if line.status == "in_house" or line.quote is not None]
    if not listed:
        return (
            "<h2>Listed methods &amp; draft pricing</h2>"
            "<p>No requested methods are currently matched to a lab declaration. "
            "See the gap and clarification details below; availability is not yet confirmed.</p>"
        )
    # Put supplied prices where they can be found immediately. Stable sorting
    # retains source order within equally priced/unpriced groups.
    listed.sort(
        key=lambda line: (
            line.quote is None,
            line.capability is None or line.capability.price is None,
        )
    )
    parts = [
        f"<h2>Listed methods &amp; draft pricing · {len(listed)}</h2>",
        "<table class='overview'><tr>",
        "<th width='31%'>Test / requested method</th>",
        "<th width='27%'>Review status</th>",
        "<th width='22%'>Reference rate</th>",
        "<th width='20%'>Quantity / estimate</th></tr>",
    ]
    source_labels = {
        "ready": "Source: no row flags",
        "confirmed": "Source: confirmed",
        "needs_attention": "Source: check details",
        "excluded": "Source: excluded",
    }
    for line in listed[:8]:
        # An unusually long name remains complete in its detail record, while
        # this compact preview must stay small enough to be useful at the front.
        name = line.requirement.original_test_name
        short_name = name if len(name) <= 100 else name[:97] + "…"
        methods = "; ".join(method.code for method in line.requirement.requested_methods)
        if len(methods) > 90:
            methods = methods[:87] + "…"
        if _scope_confirmed(line, current_lab):
            lab_status = "Lab: scope confirmed"
        elif line.status == "in_house":
            lab_status = "Lab: confirm scope"
        else:
            lab_status = _status_label(line, current_lab)
        quote = line.quote
        capability = line.capability
        if quote is not None:
            rate = _rate(quote.currency, quote.unit_price, quote.pricing_basis)
            quantity = (
                f"{_money(quote.quantity)}<br>"
                f"<b>{_text(quote.currency)} {_text(_money(quote.subtotal))}</b>"
            )
        else:
            rate = (
                _rate(capability.currency, capability.price, capability.pricing_basis)
                if capability is not None and capability.price is not None
                else "Not supplied"
            )
            quantity = "Not supplied<br><span class='sub'>Not priced</span>"
        source_status = source_labels[line.extraction_review_state]
        parts.extend(
            [
                "<tr>",
                f"<td><b>{_text(short_name)}</b><br>"
                f"<span class='sub'>{_text(methods or 'Method not stated')}</span></td>",
                f"<td>{_text(lab_status)}<br><span class='sub'>{_text(source_status)}</span></td>",
                f"<td>{_text(rate)}</td><td>{quantity}</td>",
                "</tr>",
            ]
        )
    parts.append("</table>")
    if len(listed) > 8:
        parts.append(
            f"<p class='meta'>First 8 of {len(listed)} listed / priced requirements shown. "
            "All test details follow below.</p>"
        )
    parts.append(
        "<p class='meta'>Automatic method matching is not lab approval. Reference rates "
        "are not line estimates; blank quantities and prices are not zero. "
        "Other requirements are listed in the gap and clarification sections below.</p>"
    )
    return "".join(parts)


def _line_html(line: GapLine, number: int, current_lab: str, content_review_note: str = "") -> str:
    requirement = line.requirement
    requested = []
    for method in requirement.requested_methods:
        text = method.code
        if method.revision:
            text += f" (revision: {method.revision})"
        if method.role:
            text += f" — {method.role}"
        requested.append(text)
    parts = [
        "<div class='item'>",
        "<div class='itemhead'>",
        f"<h3>{number}. {_text(requirement.original_test_name)}</h3>",
        f"<p class='status'><b>{_text(_status_label(line, current_lab))}</b></p>",
        _paragraph("Requested method(s)", "; ".join(requested) or "Not stated"),
        "</div>",
    ]
    if (
        line.canonical_test_name
        and line.canonical_test_name.casefold() != requirement.original_test_name.casefold()
    ):
        parts.append(_paragraph("Lab test", line.canonical_test_name))
    original_codes = [method.code for method in requirement.requested_methods]
    if line.considered_method_codes and set(line.considered_method_codes) != set(original_codes):
        parts.append(_paragraph("Assessed method(s)", "; ".join(line.considered_method_codes)))
    if line.unknown_methods:
        parts.append(_paragraph("Method details to confirm", "; ".join(line.unknown_methods)))
    parts.append(_values("Customer conditions", requirement.conditions))
    parts.append(_values("Acceptance criteria", requirement.acceptance_criteria))
    if requirement.applicability:
        parts.append(_paragraph("Applicability", "; ".join(requirement.applicability)))
    if requirement.special_instructions:
        parts.append(
            _paragraph("Special instructions", "; ".join(requirement.special_instructions))
        )
    lab_methods = "; ".join(line.lab_method_identifiers)
    if lab_methods:
        parts.append(_paragraph("Lab methods", lab_methods))
    elif line.status != "needs_review":
        parts.append(_paragraph("Lab methods", "No matched declaration"))
    if line.capability is not None:
        if line.capability.declared_constraints:
            parts.append(_paragraph("Lab limits", line.capability.declared_constraints))
        if line.capability.notes:
            parts.append(_paragraph("Lab note", line.capability.notes))
    note = _business_note(line, current_lab)
    if note:
        parts.append(_paragraph("Action needed", note, css_class="pending"))
    review_notes = [content_review_note]
    if line.mapping_choice is not None:
        review_notes.append(line.mapping_choice.note)
    for note in dict.fromkeys(note for note in review_notes if note):
        parts.append(_paragraph("Reviewer note", note))
    parts.append(_pricing(line))
    pages = sorted({evidence.page for evidence in requirement.evidence})
    parts.append(
        _paragraph(
            "Source pages",
            ", ".join(str(page) for page in pages) or "Not supplied",
            css_class="source",
        )
    )
    parts.append("</div>")
    return "".join(parts)


def _pricing_summary(snapshot: ReportSnapshot, active: list[GapLine]) -> str:
    priced = sum(line.quote is not None for line in active)
    parts = ["<h2>Draft pricing</h2>"]
    if snapshot.totals_by_currency:
        for currency, total in sorted(snapshot.totals_by_currency.items()):
            parts.append(_paragraph("Priced line subtotal", f"{currency} {_money(total)}"))
    else:
        parts.append("<p>No confirmed line estimates yet; reference rates are shown per test.</p>")
    parts.append(
        f"<p class='meta'>{priced} priced; {len(active) - priced} unpriced requirements. "
        "Subtotals cover priced lines only, not the whole job. Quantities are confirmed "
        "separately; overlapping requirements must not be counted twice. "
        "Requirements without a line estimate or reference rate are unpriced. "
        "Partner costs, preparation, taxes and lead times are not estimated.</p>"
    )
    return "".join(parts)


def _report_html(snapshot: ReportSnapshot) -> str:
    source = snapshot.source_run.extraction.document
    generated = snapshot.created_at
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    current_lab = lab_fingerprint(snapshot.lab)
    active = [line for line in snapshot.lines if line.status != "excluded"]
    excluded = [line for line in snapshot.lines if line.status == "excluded"]
    counts = Counter(line.status for line in active)
    gaps = counts["test_gap"] + counts["method_gap"] + counts["condition_gap"]
    pending_content = sum(line.extraction_review_state == "needs_attention" for line in active)
    pending_scope = sum(
        line.status == "in_house" and not _scope_confirmed(line, current_lab) for line in active
    )
    parts = [
        "<html><body><div class='summary'>",
        "<p class='eyebrow'>SCIONE · LAB WORKSPACE</p>",
        "<h1>Lab capability gap report</h1>",
        "<p class='draft'><b>DRAFT · Engineer review required — not an approved quotation</b></p>",
        _paragraph("Customer specification", source.title or snapshot.original_name),
        _paragraph(
            "Document / revision",
            f"{source.identifier or 'Not stated'} · {source.revision or 'Not stated'}",
        ),
        _paragraph("Lab", snapshot.lab.name),
        _paragraph("Prepared", generated.astimezone(UTC).strftime("%d %b %Y · %H:%M UTC")),
        "<h2>Review summary</h2>",
        f"<p><b>{len(active)} test requirements</b> · {gaps} gaps to resolve · "
        f"{counts['needs_review']} need clarification · {counts['in_house']} listed methods</p>",
        f"<p class='meta'>Customer details awaiting confirmation: {pending_content}. "
        f"Test / method clarification: {counts['needs_review']}. "
        f"Listed methods awaiting scope review: {pending_scope}.</p>",
        "<p class='meta'>A listed method is not proof that every customer condition is met. "
        "An unlisted test may still be available; different methods are not automatically "
        "equivalent. These are requirement records, not billable quantities.</p>",
    ]
    if snapshot.catalog.synthetic:
        parts.append("<p class='meta'>Prototype: the reference test catalog is illustrative.</p>")
    if any(warning.startswith("Coverage review:") for warning in snapshot.warnings):
        parts.append(
            "<p class='pending'>Check the source specification for potentially missing "
            "requirements before relying on this draft.</p>"
        )
    parts.append(_listed_overview(active, current_lab))
    parts.append(_pricing_summary(snapshot, active))
    parts.append("</div>")
    groups = (
        ("Listed methods — customer requirements", {"in_house"}),
        ("Gaps to resolve", {"method_gap", "test_gap", "condition_gap"}),
        ("Needs clarification", {"needs_review"}),
    )
    number = 0
    for title, statuses in groups:
        selected = [line for line in active if line.status in statuses]
        if not selected:
            continue
        parts.append(f"<h2>{_text(title)} · {len(selected)}</h2>")
        if title == "Needs clarification":
            parts.append(
                "<p class='meta'>Confirm the intended test and applicable method(s) before "
                "assessing lab availability. These requirements are not classified as "
                "available or unavailable yet.</p>"
            )
        for line in selected:
            number += 1
            decision = snapshot.source_review.decisions.get(line.requirement.id)
            parts.append(_line_html(line, number, current_lab, decision.note if decision else ""))
    if not active:
        parts.append("<p>No active test requirements are included in this report.</p>")
    if excluded:
        parts.append(f"<h2>Excluded from scope · {len(excluded)}</h2>")
        parts.append("<p class='meta'>Not included in capability assessment or pricing.</p>")
        for line in excluded:
            decision = snapshot.source_review.decisions.get(line.requirement.id)
            note = f" — {decision.note}" if decision and decision.note else ""
            parts.append(f"<p>{_text(line.requirement.original_test_name + note)}</p>")
    parts.append("</body></html>")
    return "".join(parts)


def render_report_pdf(snapshot: ReportSnapshot) -> bytes:
    """Render readable A4 pages without truncation or developer-only appendices.

    Paragraphs flow across pages instead of fitting records into fixed-height
    table cells. The source snapshot is validated but never changed.
    """

    try:
        checked = ReportSnapshot.model_validate(snapshot.model_dump())
        story = pymupdf.Story(html=_report_html(checked), user_css=_CSS)
        media = pymupdf.paper_rect("a4")
        content = pymupdf.Rect(40, 69, media.width - 40, media.height - 49)

        def page_rect(rect_number: int, filled: pymupdf.Rect) -> tuple:
            del rect_number, filled
            return media, content, None

        with story.write_with_links(page_rect) as pdf:
            total = len(pdf)
            for index, page in enumerate(pdf, start=1):
                page.draw_rect(
                    pymupdf.Rect(0, 0, media.width, 46),
                    color=None,
                    fill=(0.063, 0.216, 0.18),
                    overlay=True,
                )
                page.insert_text(
                    (40, 28),
                    "SCIONE  /  LAB CAPABILITY GAP REPORT",
                    fontsize=8.5,
                    fontname="hebo",
                    color=(1, 1, 1),
                )
                page.insert_text(
                    (media.width - 76, 28),
                    "DRAFT",
                    fontsize=8.5,
                    fontname="hebo",
                    color=(0.87, 0.95, 0.9),
                )
                page.draw_line(
                    (40, media.height - 31),
                    (media.width - 40, media.height - 31),
                    color=(0.78, 0.85, 0.81),
                    width=0.5,
                )
                # A short business reference links PDF and JSON without exposing
                # internal run, document, catalog or fingerprint identifiers.
                reference = checked.report_id.encode("ascii", "replace").decode("ascii")[:8]
                page.insert_text(
                    (40, media.height - 19),
                    f"Draft  /  Reference {reference}",
                    fontsize=7,
                    color=(0.34, 0.43, 0.39),
                )
                page.insert_textbox(
                    pymupdf.Rect(
                        media.width - 120, media.height - 26, media.width - 40, media.height - 13
                    ),
                    f"Page {index} of {total}",
                    fontsize=7,
                    align=2,
                    color=(0.34, 0.43, 0.39),
                )
            pdf.set_metadata(
                {
                    "title": "Lab capability gap report — draft",
                    "author": "SciOne lab workspace prototype",
                    "subject": "Customer test requirements, lab availability and draft pricing",
                }
            )
            return pdf.tobytes(garbage=4, deflate=True)
    except Exception as exc:
        raise ReportPDFError(f"Unable to render the saved report as PDF: {exc}") from exc

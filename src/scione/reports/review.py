"""Lab-review workflow states shared by the HTTP and Streamlit adapters."""

from scione.reports.matching import lab_fingerprint, support_basis
from scione.reports.models import GapLine, ReportSnapshot


def scope_review_state(report: ReportSnapshot, line: GapLine) -> str:
    if line.status == "excluded":
        return "Excluded"
    if line.status == "condition_gap":
        return "Outside lab scope"
    if line.status == "test_gap":
        return "Not listed in lab catalog — no confirmation needed"
    if line.status == "method_gap":
        return "Requested method not listed — no confirmation needed"
    if line.status != "in_house":
        return "Resolve test / method first"
    basis = support_basis(line, lab_fingerprint(report.lab))
    if basis == "human":
        return "Confirmed by lab reviewer"
    if basis == "automatic":
        return "Operating conditions matched automatically"
    return "Pending lab review"


def needs_review(report: ReportSnapshot, line: GapLine) -> bool:
    stale_scope = (
        line.status == "in_house"
        and line.mapping_choice is not None
        and line.mapping_choice.capability_verdict != "unreviewed"
        and line.mapping_choice.lab_fingerprint != lab_fingerprint(report.lab)
    )
    return line.status != "excluded" and (
        line.status == "needs_review"
        or line.extraction_review_state == "needs_attention"
        or stale_scope
        or scope_review_state(report, line) == "Pending lab review"
    )


def quote_eligible(report: ReportSnapshot, line: GapLine) -> bool:
    return (
        line.status == "in_house"
        and line.extraction_review_state in {"ready", "confirmed"}
        and support_basis(line, lab_fingerprint(report.lab)) is not None
        and line.capability is not None
        and line.capability.price is not None
    )


def batch_eligible(line: GapLine) -> bool:
    return line.status in {"in_house", "condition_gap"} and line.capability is not None

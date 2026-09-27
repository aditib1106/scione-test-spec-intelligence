"""Pure presentation helpers shared by the Streamlit app and its tests."""

from __future__ import annotations

from typing import Any

from scione.evaluation import EvaluationReport
from scione.schemas import SourceEvidence, TestMethodExtraction


def _append_evidence(
    rows: list[dict[str, Any]],
    *,
    category: str,
    item: str,
    evidence: list[SourceEvidence],
) -> None:
    for source in evidence:
        rows.append(
            {
                "page": source.page,
                "category": category,
                "item": item,
                "clause": source.clause or "—",
                "quote": source.quote,
            }
        )


def evidence_rows(extraction: TestMethodExtraction) -> list[dict[str, Any]]:
    """Flatten all provenance-bearing fields into reviewable table rows."""

    rows: list[dict[str, Any]] = []
    _append_evidence(
        rows,
        category="document",
        item=extraction.document.title,
        evidence=extraction.document.evidence,
    )
    for item in extraction.test_items:
        _append_evidence(
            rows,
            category="test item",
            item=item.normalized_name,
            evidence=item.evidence,
        )
    for method in extraction.methods:
        method_label = f"{method.method_id} · {method.name}"
        _append_evidence(
            rows,
            category="method",
            item=method_label,
            evidence=method.evidence,
        )
        for exposure in method.exposures:
            _append_evidence(
                rows,
                category="exposure",
                item=f"{method.method_id} · {exposure.name}",
                evidence=exposure.evidence,
            )
            for condition in exposure.conditions:
                _append_evidence(
                    rows,
                    category="condition",
                    item=f"{method.method_id} · {condition.name}",
                    evidence=condition.evidence,
                )
        for parameter in method.parameters:
            _append_evidence(
                rows,
                category="parameter",
                item=f"{method.method_id} · {parameter.name}",
                evidence=parameter.evidence,
            )
        for level in method.classifications:
            _append_evidence(
                rows,
                category="classification",
                item=f"{method.method_id} · {level.label}",
                evidence=level.evidence,
            )
    for reference in extraction.referenced_documents:
        _append_evidence(
            rows,
            category="reference",
            item=reference.code,
            evidence=reference.evidence,
        )
    for criterion in extraction.acceptance_criteria:
        _append_evidence(
            rows,
            category="acceptance criterion",
            item=f"{criterion.method_id or 'document'} · {criterion.parameter}",
            evidence=criterion.evidence,
        )
    return sorted(rows, key=lambda row: (row["page"], row["category"], row["item"]))


def evaluation_rows(report: EvaluationReport) -> list[dict[str, Any]]:
    """Create compact score rows while retaining detailed errors elsewhere."""

    return [
        {
            "dimension": metric.name.replace("_", " ").title(),
            "precision": round(metric.precision, 3),
            "recall": round(metric.recall, 3),
            "f1": round(metric.f1, 3),
            "matched": metric.matched,
            "predicted": metric.predicted,
            "expected": metric.expected,
        }
        for metric in report.metrics
    ]

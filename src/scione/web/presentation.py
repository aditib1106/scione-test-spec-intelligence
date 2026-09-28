"""Pure presentation helpers shared by the Streamlit app and its tests."""

from __future__ import annotations

from typing import Any

from scione.evaluation import EvaluationReport, SetMetric
from scione.runs import StoredMethodRun
from scione.schemas import SourceEvidence, TestMethodExtraction

EVALUATION_LABELS = {
    "document_identity": "Document identity",
    "test_items": "Test items",
    "methods": "Test methods",
    "exposures": "Exposure names",
    "exposure_cycles": "Exposure cycle counts",
    "procedure_conditions": "Procedure condition labels",
    "procedure_condition_values": "Procedure condition values",
    "parameters": "Parameter concepts",
    "parameter_units": "Parameter names and units",
    "classification_labels": "Classification labels",
    "referenced_documents": "Referenced documents",
    "acceptance_criteria": "Acceptance criteria",
}


def _metric_label(name: str) -> str:
    return EVALUATION_LABELS.get(name, name.replace("_", " ").title())


def _quality_label(f1: float) -> str:
    if f1 >= 0.9:
        return "Strong"
    if f1 >= 0.7:
        return "Mostly correct"
    if f1 >= 0.4:
        return "Needs review"
    return "Weak"


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
            "dimension": _metric_label(metric.name),
            "result": _quality_label(metric.f1),
            "truth found": f"{metric.matched}/{metric.expected}",
            "extra values": len(metric.unexpected),
            "precision": round(metric.precision, 3),
            "recall": round(metric.recall, 3),
            "f1": round(metric.f1, 3),
            "matched": metric.matched,
            "predicted": metric.predicted,
            "expected": metric.expected,
        }
        for metric in report.metrics
    ]


def evaluation_summary(report: EvaluationReport) -> dict[str, Any]:
    """Summarize quality without allowing one score to hide field-level errors."""

    mean_f1 = (
        sum(metric.f1 for metric in report.metrics) / len(report.metrics)
        if report.metrics
        else None
    )
    total_matched = sum(metric.matched for metric in report.metrics)
    total_expected = sum(metric.expected for metric in report.metrics)
    total_unexpected = sum(len(metric.unexpected) for metric in report.metrics)
    grounding = report.evidence_grounding
    return {
        "mean_f1": mean_f1,
        "matched": total_matched,
        "expected": total_expected,
        "unexpected": total_unexpected,
        "evidence_grounded": grounding.grounded if grounding else None,
        "evidence_total": grounding.total if grounding else None,
        "evidence_rate": grounding.rate if grounding else None,
    }


def evaluation_detail_rows(metric: SetMetric) -> list[dict[str, str]]:
    """Show normalized truth and prediction values with an explicit disposition."""

    rows = [
        {
            "status": "Match",
            "ground truth": value,
            "model output": value,
        }
        for value in metric.matched_values
    ]
    rows.extend(
        {
            "status": "Missing from model",
            "ground truth": value,
            "model output": "—",
        }
        for value in metric.missing
    )
    rows.extend(
        {
            "status": "Unexpected from model",
            "ground truth": "—",
            "model output": value,
        }
        for value in metric.unexpected
    )
    return rows


def comparison_summary_rows(
    runs: list[StoredMethodRun],
    reports: dict[str, EvaluationReport] | None = None,
) -> list[dict[str, Any]]:
    """Summarize persisted runs without triggering additional inference."""

    rows: list[dict[str, Any]] = []
    for stored in runs:
        report = (
            reports.get(stored.run.run_id, stored.evaluation)
            if reports is not None
            else stored.evaluation
        )
        mean_f1 = (
            sum(metric.f1 for metric in report.metrics) / len(report.metrics)
            if report and report.metrics
            else None
        )
        rows.append(
            {
                "run": f"{stored.run.run_id[:8]}…",
                "provider / model": f"{stored.run.provider} / {stored.run.model}",
                "latency (s)": round(stored.run.latency_ms / 1000, 2),
                "input tokens": stored.run.usage.input_tokens,
                "output tokens": stored.run.usage.output_tokens,
                "total tokens": stored.run.usage.total_tokens,
                "methods": len(stored.run.extraction.methods),
                "evidence": len(evidence_rows(stored.run.extraction)),
                "mean field F1": round(mean_f1, 3) if mean_f1 is not None else None,
                "evidence grounded": (
                    f"{report.evidence_grounding.grounded}/"
                    f"{report.evidence_grounding.total}"
                    if report and report.evidence_grounding
                    else None
                ),
            }
        )
    return rows


def comparison_evaluation_rows(
    selected: EvaluationReport,
    comparison: EvaluationReport,
) -> list[dict[str, Any]]:
    """Align field-level F1 scores for two evaluations."""

    selected_by_name = {metric.name: metric for metric in selected.metrics}
    comparison_by_name = {metric.name: metric for metric in comparison.metrics}
    rows: list[dict[str, Any]] = []
    for name in sorted(selected_by_name.keys() | comparison_by_name.keys()):
        selected_metric = selected_by_name.get(name)
        comparison_metric = comparison_by_name.get(name)
        selected_f1 = selected_metric.f1 if selected_metric else None
        comparison_f1 = comparison_metric.f1 if comparison_metric else None
        delta = (
            selected_f1 - comparison_f1
            if selected_f1 is not None and comparison_f1 is not None
            else None
        )
        rows.append(
            {
                "dimension": _metric_label(name),
                "selected F1": round(selected_f1, 3) if selected_f1 is not None else None,
                "comparison F1": (
                    round(comparison_f1, 3) if comparison_f1 is not None else None
                ),
                "delta": round(delta, 3) if delta is not None else None,
            }
        )
    return rows

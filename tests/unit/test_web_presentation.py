"""Tests for web-facing evidence and metric transformations."""

from pathlib import Path

from scione.evaluation import TestMethodEvaluator
from scione.extraction import MethodExtractionRunner
from scione.ingestion import PyMuPDFTextIngestor
from scione.providers import StaticModelProvider
from scione.runs import FileRunStore
from scione.schemas import TestMethodExtraction as MethodExtractionSchema
from scione.web.presentation import (
    comparison_evaluation_rows,
    comparison_summary_rows,
    evaluation_detail_rows,
    evaluation_rows,
    evaluation_summary,
    evidence_rows,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)


def test_presentation_helpers_flatten_evidence_and_metrics() -> None:
    truth = MethodExtractionSchema.model_validate_json(GROUND_TRUTH.read_text(encoding="utf-8"))
    rows = evidence_rows(truth)
    report = TestMethodEvaluator().evaluate(truth, truth)
    metrics = evaluation_rows(report)
    summary = evaluation_summary(report)
    details = evaluation_detail_rows(report.metrics[0])

    assert rows
    assert {row["category"] for row in rows} >= {
        "document",
        "method",
        "condition",
        "classification",
    }
    assert {row["page"] for row in rows} <= {1, 2, 3, 4}
    assert all(row["f1"] == 1.0 for row in metrics)
    assert all(row["result"] == "Strong" for row in metrics)
    assert summary["matched"] == summary["expected"]
    assert details
    assert all(row["status"] == "Match" for row in details)


def test_comparison_helpers_use_saved_results(tmp_path: Path) -> None:
    truth = MethodExtractionSchema.model_validate_json(GROUND_TRUTH.read_text(encoding="utf-8"))
    document = PyMuPDFTextIngestor().ingest(SAMPLE_PDF)
    run = MethodExtractionRunner(StaticModelProvider.from_json_file(GROUND_TRUTH)).run(document)
    report = TestMethodEvaluator().evaluate(run.extraction, truth)
    store = FileRunStore(tmp_path)
    store.save_method_run(document=document, run=run, evaluation=report)
    stored = store.load_method_run(run.run_id)

    summaries = comparison_summary_rows([stored, stored])
    metrics = comparison_evaluation_rows(report, report)

    assert len(summaries) == 2
    assert summaries[0]["mean field F1"] == 1.0
    assert summaries[0]["provider / model"] == "static / static-fixture"
    assert all(row["delta"] == 0.0 for row in metrics)

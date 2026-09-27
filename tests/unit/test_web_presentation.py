"""Tests for web-facing evidence and metric transformations."""

from pathlib import Path

from scione.evaluation import TestMethodEvaluator
from scione.schemas import TestMethodExtraction as MethodExtractionSchema
from scione.web.presentation import evaluation_rows, evidence_rows

REPOSITORY_ROOT = Path(__file__).parents[2]
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


def test_presentation_helpers_flatten_evidence_and_metrics() -> None:
    truth = MethodExtractionSchema.model_validate_json(GROUND_TRUTH.read_text(encoding="utf-8"))
    rows = evidence_rows(truth)
    metrics = evaluation_rows(TestMethodEvaluator().evaluate(truth, truth))

    assert rows
    assert {row["category"] for row in rows} >= {
        "document",
        "method",
        "condition",
        "classification",
    }
    assert {row["page"] for row in rows} <= {1, 2, 3, 4}
    assert all(row["f1"] == 1.0 for row in metrics)

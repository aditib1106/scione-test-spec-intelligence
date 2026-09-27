"""Unit tests for transparent method-extraction metrics."""

import json
from copy import deepcopy
from pathlib import Path

from scione.evaluation import TestMethodEvaluator
from scione.schemas import TestMethodExtraction as MethodExtractionSchema

REPOSITORY_ROOT = Path(__file__).parents[2]
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


def _load_truth_payload() -> dict[str, object]:
    return json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))


def test_perfect_prediction_receives_perfect_field_metrics() -> None:
    truth = MethodExtractionSchema.model_validate(_load_truth_payload())

    report = TestMethodEvaluator().evaluate(truth, truth)

    assert all(metric.f1 == 1.0 for metric in report.metrics)
    assert report.unsupported_acceptance_criteria == 0


def test_reports_missing_parameter_and_hallucinated_acceptance_criterion() -> None:
    truth_payload = _load_truth_payload()
    prediction_payload = deepcopy(truth_payload)
    prediction_payload["methods"][0]["parameters"].pop()
    prediction_payload["acceptance_criteria"] = [
        {
            "method_id": "A",
            "parameter": "portal stability classification",
            "operator": ">=",
            "value": "4A",
            "unit": None,
            "evidence": [{"page": 2, "clause": "Fig. 1", "quote": "4A"}],
        }
    ]
    prediction = MethodExtractionSchema.model_validate(prediction_payload)
    truth = MethodExtractionSchema.model_validate(truth_payload)

    report = TestMethodEvaluator().evaluate(prediction, truth)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["parameters"].recall < 1.0
    assert metrics["acceptance_criteria"].precision == 0.0
    assert report.unsupported_acceptance_criteria == 1


def test_method_id_prefix_does_not_cascade_through_nested_metrics() -> None:
    truth_payload = _load_truth_payload()
    prediction_payload = deepcopy(truth_payload)
    prediction_payload["methods"][0]["method_id"] = "Test Method A"
    prediction_payload["methods"][1]["method_id"] = "Method B"
    prediction = MethodExtractionSchema.model_validate(prediction_payload)
    truth = MethodExtractionSchema.model_validate(truth_payload)

    report = TestMethodEvaluator().evaluate(prediction, truth)
    metrics = {metric.name: metric for metric in report.metrics}

    for name in (
        "methods",
        "exposures",
        "procedure_conditions",
        "parameters",
        "classification_labels",
    ):
        assert metrics[name].f1 == 1.0


def test_machine_style_names_and_plural_units_are_format_equivalent() -> None:
    truth_payload = _load_truth_payload()
    prediction_payload = deepcopy(truth_payload)
    prediction_payload["methods"][0]["parameters"][0]["name"] = "enchantment_density"
    prediction_payload["methods"][0]["parameters"][1]["unit"] = "spectral degrees"
    prediction_payload["methods"][0]["exposures"][0]["conditions"][0]["name"] = "fringe_setting"
    prediction = MethodExtractionSchema.model_validate(prediction_payload)
    truth = MethodExtractionSchema.model_validate(truth_payload)

    report = TestMethodEvaluator().evaluate(prediction, truth)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["parameters"].f1 == 1.0
    assert metrics["procedure_conditions"].f1 == 1.0

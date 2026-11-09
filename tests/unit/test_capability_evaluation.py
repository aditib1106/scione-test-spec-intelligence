"""Evaluation must expose abstention, unsafe support and missing outputs honestly."""

import importlib
import json
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from scione.evaluation.capability import (
    CasePrediction,
    EvaluationDataset,
    PredictionSnapshot,
    compare_snapshots,
    dataset_sha256,
    evaluate_snapshot,
    load_dataset,
    offline_guard,
    report_markdown,
    run_current_comparator,
    snapshot_from_matching_artifacts,
)


def labelled_dataset(labels=("supported", "gap", "unknown")):
    return EvaluationDataset(
        schema_version="1.0",
        dataset_id="metric_test",
        annotation_status="user_supplied_unreviewed",
        scope="Test decision metric arithmetic only.",
        limitations=["Not domain gold."],
        cases=[
            {
                "id": f"case_{index}",
                "family": "metric_arithmetic",
                "split": "development",
                "requirement_statement": "Human-labelled request.",
                "lab_statement": "Human-labelled capability.",
                "expected_checks": {"claim": label},
                "rationale": "A supplied human decision for metric arithmetic.",
            }
            for index, label in enumerate(labels)
        ],
    )


def snapshot_for(dataset, outputs):
    return PredictionSnapshot(
        strategy="metric_test",
        input_kind="saved_output_replay",
        captured_at="2026-11-09T00:00:00Z",
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256(dataset),
        predictions=[
            CasePrediction(case_id=f"case_{index}", checks={"claim": status})
            for index, status in enumerate(outputs)
        ],
    )


def test_all_unknown_is_not_perfect_and_has_zero_automation():
    dataset = labelled_dataset()
    result = evaluate_snapshot(dataset, snapshot_for(dataset, ["unknown"] * 3))
    metrics = result["metrics"]
    assert metrics["correct_count"] == 1
    assert metrics["accuracy"] == pytest.approx(1 / 3)
    assert metrics["automation_coverage"] == 0
    assert metrics["unknown_rate"] == 1
    assert metrics["correctly_resolved_decidable_rate"] == 0
    assert metrics["false_support_rate"] is None
    assert metrics["support_precision"] is None
    assert metrics["selective_error_rate"] is None


def test_false_support_rate_uses_predicted_support_denominator_and_counts_unknown_gold():
    dataset = labelled_dataset()
    metrics = evaluate_snapshot(dataset, snapshot_for(dataset, ["supported"] * 3))["metrics"]
    assert metrics["false_support_count"] == 2
    assert metrics["predicted_supported_count"] == 3
    assert metrics["false_support_rate"] == pytest.approx(2 / 3)
    assert metrics["support_precision"] == pytest.approx(1 / 3)
    assert metrics["confusion_matrix"]["gap"]["supported"] == 1
    assert metrics["confusion_matrix"]["unknown"]["supported"] == 1


def test_missing_case_and_missing_claim_stay_in_denominator_as_errors():
    dataset = labelled_dataset()
    snapshot = snapshot_for(dataset, ["supported", "gap"])
    snapshot.predictions[1].checks = {}
    result = evaluate_snapshot(dataset, snapshot)
    assert result["metrics"]["assertion_count"] == 3
    assert result["metrics"]["correct_count"] == 1
    assert result["metrics"]["error_count"] == 2
    assert result["metrics"]["unknown_count"] == 0
    assert len(result["errors"]) == 2


def test_case_exception_does_not_turn_into_legitimate_unknown():
    dataset = labelled_dataset(["unknown"])
    snapshot = snapshot_for(dataset, ["unknown"])
    snapshot.predictions[0].error = "Comparator raised a runtime error."
    result = evaluate_snapshot(dataset, snapshot)
    assert result["metrics"]["accuracy"] == 0
    assert result["metrics"]["error_rate"] == 1
    assert result["metrics"]["unknown_rate"] == 0


def test_stale_case_hash_and_extra_case_ids_are_rejected():
    dataset = labelled_dataset()
    snapshot = snapshot_for(dataset, ["supported", "gap", "unknown"])
    snapshot.dataset_sha256 = "0" * 64
    with pytest.raises(ValueError, match="dataset differ"):
        evaluate_snapshot(dataset, snapshot)
    snapshot.dataset_sha256 = dataset_sha256(dataset)
    snapshot.predictions[0].case_id = "unrecognised_case"
    with pytest.raises(ValueError, match="unknown case IDs"):
        evaluate_snapshot(dataset, snapshot)


def test_extra_unlabelled_check_ids_are_rejected():
    dataset = labelled_dataset(["supported"])
    snapshot = snapshot_for(dataset, ["supported"])
    snapshot.predictions[0].checks["unlabelled_claim"] = "supported"
    with pytest.raises(ValueError, match="unlabelled check IDs"):
        evaluate_snapshot(dataset, snapshot)


def test_empty_datasets_and_duplicate_case_outputs_cannot_claim_accuracy():
    dataset = labelled_dataset()
    empty = dataset.model_dump(mode="json")
    empty["cases"] = []
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(empty)
    snapshot = snapshot_for(dataset, ["supported", "gap", "unknown"]).model_dump(mode="json")
    snapshot["predictions"].append(snapshot["predictions"][0])
    with pytest.raises(ValidationError, match="duplicate outputs"):
        PredictionSnapshot.model_validate(snapshot)


def test_overall_gate_failure_is_reported_even_when_parameter_is_correct():
    dataset = labelled_dataset(["supported"])
    dataset.cases[0].expected_overall = "needs_information"
    snapshot = snapshot_for(dataset, ["supported"])
    snapshot.predictions[0].overall = "fully_matched"
    report = compare_snapshots(dataset, snapshot)
    assert report["current"]["metrics"]["accuracy"] == 1
    assert report["current"]["overall_accuracy"] == 0
    assert report["current"]["case_correct_count"] == 0
    assert len(report["current"]["overall_errors"]) == 1
    assert "overall gate" in report_markdown(report)


def test_offline_guard_blocks_network_and_provider_imports():
    with offline_guard():
        with pytest.raises(RuntimeError, match="Network access"):
            socket.create_connection(("localhost", 443))
        with pytest.raises(RuntimeError, match="API providers"):
            importlib.import_module("scione.providers.forbidden_during_evaluation")


def test_fixture_run_is_reproducible_and_explicitly_not_independent_gold():
    path = Path(__file__).parents[1] / "fixtures" / "capability_gold_v1.json"
    dataset = load_dataset(path)
    assert dataset.annotation_status == "author_written_unreviewed"
    assert len(dataset.cases) == 29
    first = run_current_comparator(dataset)
    second = run_current_comparator(dataset)
    assert first.metadata == {"model_calls": 0, "network_allowed": False}
    assert [p.model_dump() for p in first.predictions] == [
        p.model_dump() for p in second.predictions
    ]
    assert first.source_policy == second.source_policy
    report = compare_snapshots(dataset, first)
    assert report["current"]["metrics"]["assertion_count"] == 30
    assert "not a client accuracy claim" in report_markdown(report)
    assert report["current"]["by_split"]["author_designed_challenge"]["assertion_count"] > 0
    # A saved actual comparison is round-trippable without re-running its producer.
    restored = PredictionSnapshot.model_validate_json(first.model_dump_json())
    assert json.loads(restored.model_dump_json())["predictions"][0]["checks"]


def artifact_comparison():
    return {
        "requirement_id": "actual_req",
        "status": "partially_matched",
        "checks": [
            {
                "id": "actual_req_method",
                "label": "Method",
                "kind": "method",
                "requested": "Method M",
                "status": "supported",
                "reason": "Method listed.",
            },
            {
                "id": "actual_claim",
                "label": "Temperature",
                "kind": "condition",
                "requested": "80 °C",
                "status": "gap",
                "reason": "Declared maximum is 70 °C.",
            },
        ],
    }


def write_artifact(tmp_path, comparison=None, **extra):
    artifact = {
        "manifest": {
            "status": "completed",
            "matching_run_id": "match_actual",
            "extraction_run_id": "extract_actual",
            "human_corrections_included": True,
        },
        "matching_machine": {
            "status": "completed",
            "provider": "fixture_provider",
            "model": "fixture_model",
            "prompt_version": "fixture_prompt",
            "validation_version": "fixture_validation",
        },
        "comparison_validated": [comparison or artifact_comparison()],
        "human_actions_at_matching": {"lab_support": "supported"},
        **extra,
    }
    path = tmp_path / "actual_artifact.json"
    path.write_text(json.dumps(artifact))
    return path


def mapped_dataset():
    dataset = labelled_dataset(["gap"])
    dataset.cases[0].artifact_requirement_id = "actual_req"
    dataset.cases[0].artifact_claim_ids = {"claim": "actual_claim"}
    dataset.cases[0].expected_overall = "partially_matched"
    return dataset


def test_matching_artifact_replay_uses_validated_machine_decisions_and_explicit_ids(tmp_path):
    dataset = mapped_dataset()
    snapshot = snapshot_from_matching_artifacts(dataset, write_artifact(tmp_path))
    assert snapshot.input_kind == "saved_output_replay"
    assert snapshot.predictions[0].checks == {"claim": "gap"}
    assert snapshot.metadata["comparison_source"] == "comparison_validated"
    assert snapshot.metadata["human_corrections_included"] is True
    assert snapshot.metadata["matching_run_id"] == "match_actual"
    assert snapshot.metadata["prompt_version"] == "fixture_prompt"
    assert snapshot.metadata["new_model_calls"] == 0
    result = evaluate_snapshot(dataset, snapshot)
    assert result["metrics"]["correct_count"] == 1
    assert result["overall_correct_count"] == 1


def test_matching_artifact_missing_claim_is_error_not_unknown(tmp_path):
    dataset = mapped_dataset()
    comparison = artifact_comparison()
    comparison["checks"] = comparison["checks"][:1]
    snapshot = snapshot_from_matching_artifacts(dataset, write_artifact(tmp_path, comparison))
    result = evaluate_snapshot(dataset, snapshot)
    assert result["metrics"]["error_count"] == 1
    assert result["metrics"]["unknown_count"] == 0


def test_matching_artifact_extra_check_and_duplicate_requirement_are_rejected(tmp_path):
    dataset = mapped_dataset()
    comparison = artifact_comparison()
    extra = dict(comparison["checks"][-1], id="unlabelled_claim")
    comparison["checks"].append(extra)
    with pytest.raises(ValueError, match="unlabelled checks"):
        snapshot_from_matching_artifacts(dataset, write_artifact(tmp_path, comparison))
    with pytest.raises(ValueError, match="duplicate requirement"):
        snapshot_from_matching_artifacts(
            dataset,
            write_artifact(
                tmp_path,
                comparison_validated=[artifact_comparison(), artifact_comparison()],
            ),
        )


def test_matching_artifact_failed_run_preserves_full_error_denominator(tmp_path):
    dataset = mapped_dataset()
    path = write_artifact(
        tmp_path,
        manifest={"status": "failed"},
        matching_machine={"status": "failed"},
        comparison_validated=[],
    )
    snapshot = snapshot_from_matching_artifacts(dataset, path)
    assert evaluate_snapshot(dataset, snapshot)["metrics"]["error_rate"] == 1


def test_artifact_mapping_cannot_double_count_one_prediction():
    data = labelled_dataset(["gap"]).model_dump(mode="json")
    data["cases"][0]["expected_checks"]["second_claim"] = "gap"
    data["cases"][0]["artifact_claim_ids"] = {
        "claim": "actual_claim",
        "second_claim": "actual_claim",
    }
    with pytest.raises(ValidationError, match="one-to-one"):
        EvaluationDataset.model_validate(data)

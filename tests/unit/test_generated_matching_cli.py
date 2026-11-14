"""Offline reproduction reports complete coverage and preserves saved inputs."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scione.evaluation import generated_matching as challenge

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_generated_matching.py"


def cli_module():
    spec = importlib.util.spec_from_file_location("generated_matching_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_policy_cli_requires_no_api_and_marks_results_as_policy_only(tmp_path):
    output = tmp_path / "policies.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(output.read_text())
    assert report["mode"] == "offline_policy_probes_NOT_model_accuracy"
    assert report["fixture_sha256"] == challenge.FROZEN_SHA256
    probe = report["policy_probes"]["manual_oracle"]
    assert probe["complete_batch_coverage"]
    assert probe["correct_parameter_decisions"] == 29
    assert probe["false_support_count"] == 0


def test_partial_saved_batch_cannot_be_reported_as_full_reference_success(tmp_path):
    snapshot, _ = challenge.build_batch("batch_01")
    attempt = challenge.oracle_run(snapshot)
    run_path, output = tmp_path / "run.json", tmp_path / "report.json"
    run_path.write_text(attempt.model_dump_json())
    before = run_path.read_bytes()
    subprocess.run(
        [sys.executable, str(SCRIPT), "--run", str(run_path), "--output", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    scores = json.loads(output.read_text())["scores"]
    assert scores["supplied_batches"] == ["batch_01"]
    assert len(scores["missing_batches"]) == 4
    assert not scores["complete_batch_coverage"]
    assert scores["executed_parameter_accuracy"] == 1
    assert scores["full_reference_parameter_accuracy"] < 1
    assert run_path.read_bytes() == before


def test_duplicate_saved_batches_cannot_inflate_aggregate_score():
    fixture = challenge.load_reference()
    snapshot, _ = challenge.build_batch("batch_01", fixture)
    score = challenge.evaluate_matching(challenge.oracle_run(snapshot, fixture), fixture)
    with pytest.raises(ValueError, match="duplicate batches"):
        cli_module().aggregate([score, score], fixture)

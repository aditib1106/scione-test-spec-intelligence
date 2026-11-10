"""The semantic challenge CLI replays saved outputs without inference or settings."""

import hashlib
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from scione.evaluation.semantic_challenge import (
    FROZEN_SHA256,
    batch_snapshot,
    e2e_source_plan,
    load_challenge,
    oracle_run,
)
from scione.schemas import IngestedDocument

SCRIPT = Path(__file__).parents[2] / "scripts/evaluate_semantic_matching.py"


def cli():
    spec = importlib.util.spec_from_file_location("offline_semantic_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_cli_never_loads_settings_or_opens_network(tmp_path, monkeypatch):
    import scione.config

    def forbidden(*_args, **_kwargs):
        raise AssertionError("The offline evaluator must not use settings or network.")

    monkeypatch.setattr(scione.config, "Settings", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert cli().main(["--output", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["new_api_calls"] == 0
    assert summary["comparison_mode"] == "CURRENT_POLICY_RECOMPILE"
    assert summary["mode"] == "manual_reference_policy_probe"
    assert summary["manual_reference_run_count"] == 2
    assert summary["fixture_sha256"] == FROZEN_SHA256
    assert summary["unique_case_count"] == 24
    assert summary["expected_parameter_decisions_across_trials"] == 28
    assert all(result["score"]["cases"] for result in summary["results"])
    assert all(
        result["score"]["comparison_policy"]["recompilation"]
        == "current_imported_production_policy"
        for result in summary["results"]
    )
    assert all(
        "matching/comparison.py" in result["score"]["comparison_policy"]["source_sha256"]
        for result in summary["results"]
    )
    assert all(Path(result["score_path"]).exists() for result in summary["results"])


def test_multiple_runs_preserve_source_and_mark_overlapping_trials(tmp_path):
    fixture = load_challenge()
    attempt = oracle_run(batch_snapshot(fixture, "batch_a"), fixture)
    path = tmp_path / "original.run.json"
    original = attempt.model_dump_json().encode()
    path.write_bytes(original)
    output = tmp_path / "evaluation"
    assert cli().main(["--run", str(path), "--run", str(path), "--output", str(output)]) == 0
    assert path.read_bytes() == original
    summary = json.loads((output / "summary.json").read_text())
    assert summary["new_api_calls"] == 0
    assert summary["mode"] == "saved_matching_output_replay"
    assert summary["manual_reference_run_count"] == 2
    assert summary["run_count"] == 2
    assert summary["case_trial_count"] == 24
    assert summary["unique_case_count"] == 12
    assert summary["overlapping_case_trials"]
    assert summary["expected_parameter_decisions_across_trials"] == 26
    assert summary["results"][0]["input_artifact_sha256"] == hashlib.sha256(original).hexdigest()
    assert summary["results"][0]["metadata"]["input_fingerprint"] == attempt.input_fingerprint


def test_one_run_argument_accepts_several_paths(tmp_path):
    fixture = load_challenge()
    paths = []
    for batch in fixture["batches"]:
        path = tmp_path / f"{batch['id']}.json"
        path.write_text(oracle_run(batch_snapshot(fixture, batch), fixture).model_dump_json())
        paths.append(str(path))
    output = tmp_path / "evaluation"
    assert cli().main(["--run", *paths, "--output", str(output)]) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["run_count"] == 2
    assert summary["unique_case_count"] == 24
    assert not summary["overlapping_case_trials"]


def test_invalid_artifact_is_not_converted_to_a_correct_unknown(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"not_a_matching_run":true}')
    output = tmp_path / "evaluation"
    assert cli().main(["--run", str(path), "--output", str(output)]) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["new_api_calls"] == 0
    assert summary["invalid_artifact_count"] == 1
    assert summary["run_count"] == 0
    assert summary["expected_parameter_decisions_across_trials"] == 0
    assert summary["parameter_accuracy_across_trials"] is None
    assert path.read_text() == '{"not_a_matching_run":true}'


def test_output_directory_cannot_overwrite_an_input_artifact(tmp_path):
    path = tmp_path / "summary.json"
    fixture = load_challenge()
    original = oracle_run(batch_snapshot(fixture, "batch_a"), fixture).model_dump_json()
    path.write_text(original)
    with pytest.raises(SystemExit) as caught:
        cli().main(["--run", str(path), "--output", str(tmp_path)])
    assert caught.value.code == 2
    assert path.read_text() == original


def test_e2e_plan_preserves_frozen_labels_and_explicit_physical_page_boundaries():
    fixture = load_challenge()
    before = json.dumps(fixture, sort_keys=True)
    plan = e2e_source_plan(fixture)
    document = IngestedDocument.model_validate(plan["document"])
    assert plan["expected_test_requests"] == document.page_count == 6
    assert plan["expected_source_facts"] == 7
    assert json.dumps(fixture, sort_keys=True) == before
    assert plan == e2e_source_plan(fixture)
    source = "\f".join(page.text for page in document.pages)
    assert hashlib.sha256(source.encode()).hexdigest() == document.sha256
    for expected, page in zip(plan["expectations"], document.pages, strict=True):
        assert expected["physical_page"] == page.page_number
        assert f"INDEPENDENT CUSTOMER REQUEST {expected['request_anchor']}" in page.text
        for claim in expected["reference_claims"]:
            assert claim["source_pages"] == [page.page_number]
            assert claim["raw_text"] in page.text
            assert f"Stage: {claim['stage']}." in page.text
    # These two requests share a method: source association must use page/request boundaries.
    assert plan["expectations"][1]["method_codes"] == plan["expectations"][2]["method_codes"]
    assert plan["expectations"][1]["request_anchor"] != plan["expectations"][2]["request_anchor"]

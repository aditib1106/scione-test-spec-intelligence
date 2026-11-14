"""Frozen source matching probes; no provider or user workspace is used."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

import scione.evaluation.generated_matching as challenge
from scione.matching import SemanticMatchingRun, input_fingerprint, validate_matching_output
from scione.structured.models import ClaimCheck


@pytest.fixture
def fixture():
    return challenge.load_reference()


def test_frozen_labels_cover_five_complete_batches(fixture, tmp_path):
    assert fixture["frozen_before_matching_model_outputs"] is True
    assert len(fixture["cases"]) == 28
    assert [len(row["case_ids"]) for row in fixture["batches"]] == [6, 6, 6, 5, 5]
    assert len({case["id"] for case in fixture["cases"]}) == 28
    assert sum(len(case["normalized"]["claims"]) for case in fixture["cases"]) == 29
    assert (
        sum(case["source"]["origin"] == "synthetic_modified_source" for case in fixture["cases"])
        == 2
    )
    path = tmp_path / "changed.json"
    path.write_bytes(challenge.FIXTURE.read_bytes() + b" ")
    with pytest.raises(ValueError, match="bytes changed"):
        challenge.load_reference(path)
    changed = deepcopy(fixture)
    changed["cases"][0]["expected_outcomes"][0]["checks"]["q01_1"] = "gap"
    with pytest.raises(ValueError, match="labels changed"):
        challenge.build_batch("batch_01", changed)


@pytest.mark.parametrize("batch_id", [f"batch_{index:02d}" for index in range(1, 6)])
def test_bounded_candidates_never_borrow_another_scenarios_lab_row(fixture, batch_id):
    snapshot, bundle = challenge.build_batch(batch_id, fixture)
    cases = {row["requirement"]["id"]: row for row in bundle["cases"]}
    assert len(snapshot.catalog.tests) <= len(snapshot.requirements) * 8
    assert len(snapshot.catalog.methods) < 30
    all_caps = {row.capability.id for row in snapshot.capabilities}
    for row in snapshot.requirements:
        local = {cap["id"] for cap in cases[row.requirement.id]["capabilities"]}
        assert set(row.capability_ids) == local
        assert local <= all_caps
        assert 1 <= len(row.catalog_test_ids) <= 8
    payload = snapshot.model_dump_json()
    assert "expected_outcomes" not in payload
    assert all(case["id"] not in payload for case in bundle["cases"])


def test_physical_source_provenance_is_exact_and_addenda_are_explicit(fixture):
    pymupdf = pytest.importorskip("pymupdf")
    root = Path(__file__).resolve().parents[2]
    checked = 0
    for case in fixture["cases"]:
        source = case["source"]
        path = root / source["pdf"]
        if path.exists():
            with pymupdf.open(path) as pdf:
                for evidence in source["evidence"]:
                    text = pdf[evidence["page"] - 1].get_text()
                    assert (
                        text[evidence["source_start"] : evidence["source_end"]] == evidence["quote"]
                    )
                    checked += 1
        if source["origin"] == "synthetic_modified_source":
            assert source["addendum"].startswith("Controlled synthetic addendum:")
        else:
            assert source["addendum"] is None
    if not checked:
        pytest.skip("Optional source PDFs are local; frozen quoted evidence remains available.")


def test_oracle_and_rules_remain_policy_probes_not_model_accuracy(fixture):
    oracle_correct = rules_correct = decisions = false_support = 0
    for batch in fixture["batches"]:
        snapshot, _ = challenge.build_batch(batch["id"], fixture)
        score = challenge.evaluate_matching(challenge.oracle_run(snapshot, fixture), fixture)
        rule_score = challenge.evaluate_matching(
            challenge.rules_only_run(snapshot, fixture), fixture
        )
        assert score["mode"] == rule_score["mode"] == "offline_policy_probe_NOT_model_accuracy"
        assert score["zero_false_support_all_layers"]
        assert score["correct_overall"] == score["expected_cases"]
        oracle_correct += score["correct_parameter_decisions"]
        rules_correct += rule_score["correct_parameter_decisions"]
        decisions += score["expected_parameter_decisions"]
        false_support += score["false_support_count"]
    assert oracle_correct == decisions == 29
    assert rules_correct < decisions
    assert false_support == 0


def test_method_statuses_come_from_production_roles_and_actual_lab_methods(fixture):
    snapshot, _ = challenge.build_batch("batch_05", fixture)
    attempt = challenge.oracle_run(snapshot, fixture)
    statuses = challenge.method_statuses(snapshot, attempt.output)
    assert statuses["req_r26"] == "gap"
    assert statuses["req_r27"] == "supported"
    assert statuses["req_r28"] == "gap"
    original = next(
        row.requirement for row in snapshot.requirements if row.requirement.id == "req_r27"
    )
    assert len(original.requested_methods) == 2
    assert "approval required" in original.requested_methods[1].role


def test_failed_attempt_keeps_all_facts_in_denominator(fixture):
    snapshot, _ = challenge.build_batch("batch_03", fixture)
    attempt = challenge.oracle_run(snapshot, fixture)
    data = attempt.model_dump(mode="json")
    data.update(status="failed", proposal=None, output=None, error_code="provider_error")
    score = challenge.evaluate_matching(SemanticMatchingRun.model_validate(data), fixture)
    assert score["contract_failure"]
    assert score["expected_cases"] == 6
    assert score["expected_parameter_decisions"] == score["predicted_counts"]["error"] == 7
    assert score["correct_parameter_decisions"] == score["correct_overall"] == 0


def test_wrong_identity_cannot_gain_credit_for_coincidentally_unknown_fact(fixture):
    snapshot, _ = challenge.build_batch("batch_01", fixture)
    attempt = challenge.oracle_run(snapshot, fixture)
    item = next(row for row in attempt.output.items if row.requirement_id == "req_r04")
    item.identity_status = "unknown"
    item.canonical_test_id = item.capability_id = None
    case = next(
        row
        for row in challenge.evaluate_matching(attempt, fixture)["cases"]
        if row["requirement_id"] == "req_r04"
    )
    assert not case["identity_correct"]
    assert case["claims"][0]["raw_status_agreement"]
    assert not case["claims"][0]["correct"]


def test_proposal_links_and_guarded_links_are_separate_metrics(fixture):
    snapshot, _ = challenge.build_batch("batch_02", fixture)
    attempt = challenge.oracle_run(snapshot, fixture)
    wrong = next(row for row in attempt.proposal.items if row.requirement_id == "req_r08")
    wrong.claim_alignments[0].relation = "same_parameter"
    wrong.claim_alignments[0].capability_claim_ids = ["l08_1"]
    attempt.output, attempt.validation_issues = validate_matching_output(snapshot, attempt.proposal)
    score = challenge.evaluate_matching(attempt, fixture)
    assert score["correct_links"] == 6
    assert score["raw_proposal_correct_links"] == 5
    assert score["zero_false_support_all_layers"]


def test_changed_source_cannot_be_scored_even_after_rehashing(fixture):
    snapshot, _ = challenge.build_batch("batch_02", fixture)
    attempt = challenge.oracle_run(snapshot, fixture)
    attempt.input_snapshot.requirements[0].normalized.claims[0].value = Decimal("999")
    with pytest.raises(ValueError, match="fingerprint"):
        challenge.evaluate_matching(attempt, fixture)
    attempt.input_fingerprint = input_fingerprint(attempt.input_snapshot)
    with pytest.raises(ValueError, match="source facts"):
        challenge.evaluate_matching(attempt, fixture)


def test_unlabelled_added_support_counts_as_false_support(fixture, monkeypatch):
    snapshot, _ = challenge.build_batch("batch_02", fixture)
    attempt = challenge.oracle_run(snapshot, fixture)
    original = challenge.compile_matching_run

    def unsafe(run, statuses):
        results = original(run, statuses)
        results[0].checks.append(
            ClaimCheck(
                id="unlabelled_support",
                label="Added",
                kind="condition",
                requested="Not in source",
                status="supported",
                reason="Test-only unsafe output",
            )
        )
        return results

    monkeypatch.setattr(challenge, "compile_matching_run", unsafe)
    score = challenge.evaluate_matching(attempt, fixture)
    assert score["false_support_count"] == 1
    assert score["extra_supported"] == [
        {"requirement_id": "req_r07", "claim_id": "unlabelled_support"}
    ]
    assert not score["zero_false_support_all_layers"]

"""Frozen labels, candidate isolation and honest matching-only challenge scoring."""

from copy import deepcopy
from decimal import Decimal

import pytest

import scione.evaluation.semantic_challenge as challenge
from scione.matching.models import SemanticMatchingRun
from scione.matching.service import input_fingerprint
from scione.structured.models import ClaimCheck


@pytest.fixture
def fixture():
    return challenge.load_challenge()


def test_fixture_contains_two_frozen_batches_and_all_expected_claims(fixture):
    assert len(fixture["cases"]) == 24
    assert [len(batch["case_ids"]) for batch in fixture["batches"]] == [12, 12]
    assert sum(len(case["normalized"]["claims"]) for case in fixture["cases"]) == 28
    assert {case["id"] for case in fixture["cases"]} == {
        identifier for batch in fixture["batches"] for identifier in batch["case_ids"]
    }
    assert fixture["annotation_status"] == "author_written_frozen_before_live_outputs"


def test_changed_fixture_bytes_and_in_memory_labels_are_rejected(tmp_path, fixture):
    path = tmp_path / "modified.json"
    path.write_bytes(challenge.FIXTURE.read_bytes() + b" ")
    with pytest.raises(ValueError, match="bytes changed"):
        challenge.load_challenge(path)
    changed = deepcopy(fixture)
    changed["cases"][0]["expected_outcomes"][0]["checks"]["q01_gloss"] = "gap"
    with pytest.raises(ValueError, match="labels changed"):
        challenge.batch_snapshot(changed, "batch_a")


@pytest.mark.parametrize("batch_id", ["batch_a", "batch_b"])
def test_batch_candidates_do_not_borrow_other_cases_lab_declarations(fixture, batch_id):
    snapshot = challenge.batch_snapshot(fixture, batch_id)
    cases = {row["requirement"]["id"]: row for row in fixture["cases"]}
    all_capabilities = {row.capability.id for row in snapshot.capabilities}
    assert len(snapshot.requirements) == 12
    for row in snapshot.requirements:
        local = {capability["id"] for capability in cases[row.requirement.id]["capabilities"]}
        assert set(row.capability_ids) == local
        assert local <= all_capabilities
        assert set(row.catalog_test_ids) == set(cases[row.requirement.id]["candidate_test_ids"])
    if batch_id == "batch_a":
        missing = next(
            row
            for row in snapshot.requirements
            if row.requirement.id == "req_c12_known_test_absent_from_lab"
        )
        assert missing.capability_ids == []


def test_method_policy_distinguishes_unknown_absent_and_wrong_edition(fixture):
    first = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_a"), fixture)
    status = challenge.method_statuses(first.input_snapshot, first.output)
    assert status["req_c11_unknown_catalog_test"] == "unknown"
    assert status["req_c12_known_test_absent_from_lab"] == "gap"
    assert status["req_c01_bilingual_typo"] == "supported"
    # Declining an available matching method is not evidence the lab lacks it.
    first.output.items[0].capability_id = None
    first.output.items[0].claim_alignments[0].relation = "unknown"
    first.output.items[0].claim_alignments[0].capability_claim_ids = []
    status = challenge.method_statuses(first.input_snapshot, first.output)
    assert status["req_c01_bilingual_typo"] == "unknown"
    second = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_b"), fixture)
    status = challenge.method_statuses(second.input_snapshot, second.output)
    assert status["req_c13_method_edition_gap"] == "gap"


def test_allowed_alternative_is_selected_as_one_complete_outcome(fixture):
    attempt = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_b"), fixture)
    row = next(case for case in fixture["cases"] if case["id"] == "c13_method_edition_gap")
    alternative = type(attempt.output.items[0]).model_validate(
        row["expected_outcomes"][1]["alignment"]
    )
    attempt.output.items[0] = alternative
    score = challenge.evaluate_matching(attempt, fixture)
    result = next(row for row in score["cases"] if row["case_id"] == "c13_method_edition_gap")
    assert result["selected_outcome_index"] == 1
    assert result["identity_correct"]
    assert result["exact_links"] == 1
    assert result["correct_decisions"] == 1
    assert result["claims"][0]["expected"] == "unknown"
    assert result["overall_correct"]
    assert result["overall_predicted"] == "not_matched"


def test_failed_attempts_stay_errors_in_the_full_parameter_denominator(fixture):
    oracle = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_b"), fixture)
    data = oracle.model_dump(mode="json")
    data.update(status="failed", proposal=None, output=None, error_code="provider_error")
    failed = SemanticMatchingRun.model_validate(data)
    score = challenge.evaluate_matching(failed, fixture)
    assert score["contract_failure"]
    assert score["expected_cases"] == 12
    assert score["expected_parameter_decisions"] == 15
    assert score["correct_parameter_decisions"] == 0
    assert score["predicted_counts"]["error"] == 15
    assert score["unknown_rate"] == 0
    assert score["error_rate"] == 1
    assert score["false_support_rate"] is None


def test_missing_claim_alignment_is_a_contract_failure_not_a_valid_unknown(fixture):
    attempt = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_a"), fixture)
    attempt.output.items[0].claim_alignments = []
    score = challenge.evaluate_matching(attempt, fixture)
    assert score["contract_failure"]
    assert score["compilation_error"] is not None
    assert score["predicted_counts"]["error"] == 13
    assert score["correct_parameter_decisions"] == 0


def test_wrong_identity_cannot_pass_an_accidentally_correct_unknown_verdict(fixture):
    attempt = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_a"), fixture)
    wrong = next(
        row for row in attempt.output.items if row.requirement_id == "req_c11_unknown_catalog_test"
    )
    wrong.identity_status = "aligned"
    wrong.canonical_test_id = "jet_adhesion"
    wrong.capability_id = "cap_c11_unknown_catalog_test_main"
    score = challenge.evaluate_matching(attempt, fixture)
    result = next(row for row in score["cases"] if row["case_id"] == "c11_unknown_catalog_test")
    assert not result["identity_correct"]
    assert result["claims"][0]["raw_status_agreement"]
    assert not result["claims"][0]["correct"]
    assert not result["overall_correct"]


def test_false_support_and_extra_unlabelled_support_are_both_counted(fixture, monkeypatch):
    attempt = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_a"), fixture)
    original = challenge.compile_matching_run

    def unsafe_compile(run, statuses):
        rows = original(run, statuses)
        row = next(row for row in rows if row.requirement_id == "req_c05_missing_parameter")
        check = next(check for check in row.checks if check.id == "q05_temp")
        check.status = "supported"
        row.checks.append(
            ClaimCheck(
                id="unlabelled_added_support",
                label="Extra",
                kind="condition",
                requested="Not in the labelled fixture",
                status="supported",
                reason="Test output",
            )
        )
        return rows

    monkeypatch.setattr(challenge, "compile_matching_run", unsafe_compile)
    score = challenge.evaluate_matching(attempt, fixture)
    assert score["false_support_count"] == 2
    assert score["extra_supported"] == [
        {"requirement_id": "req_c05_missing_parameter", "claim_id": "unlabelled_added_support"}
    ]
    assert score["expected_parameter_decisions"] == 13


def test_changed_snapshot_or_source_cannot_reuse_frozen_expected_labels(fixture):
    attempt = challenge.oracle_run(challenge.batch_snapshot(fixture, "batch_a"), fixture)
    attempt.input_snapshot.requirements[0].normalized.claims[0].minimum = Decimal("86")
    with pytest.raises(ValueError, match="fingerprint"):
        challenge.evaluate_matching(attempt, fixture)
    attempt.input_fingerprint = input_fingerprint(attempt.input_snapshot)
    with pytest.raises(ValueError, match="source facts"):
        challenge.evaluate_matching(attempt, fixture)

"""The frozen live challenge evaluator must not reward missing or wrong facts."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scione.matching import build_matching_input, input_fingerprint
from scione.matching.models import SemanticMatchingOutput, SemanticMatchingRun


@pytest.fixture
def experiment():
    path = Path(__file__).parents[2] / "scripts/live_capability_experiment.py"
    spec = importlib.util.spec_from_file_location("live_capability_evaluation_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manual_matching_attempt(experiment, *, unknown_thickness=False):
    bundle = experiment.build_challenge()
    snapshot = build_matching_input(
        bundle["oracle_run"],
        bundle["catalog"],
        bundle["lab"],
        capability_revision="frozen_live_challenge_v1",
        normalized_requirements=bundle["oracle_normalized"],
    )
    items = []
    for row in bundle["data"]["tests"]:
        unknown = unknown_thickness and row["canonical_test_id"] == "dry_film_thickness"
        items.append(
            {
                "requirement_id": row["id"],
                "identity_status": "unknown" if unknown else "aligned",
                "canonical_test_id": None if unknown else row["canonical_test_id"],
                "capability_id": None if unknown else "cap_live_" + row["canonical_test_id"],
                "reason": "Hand-authored unit test alignment, not a model result.",
                "claim_alignments": [
                    {
                        "requirement_claim_id": claim["id"],
                        "capability_claim_ids": []
                        if unknown
                        else experiment.EXPECTED_LINKS[claim["id"]],
                        "relation": (
                            "same_parameter"
                            if not unknown and experiment.EXPECTED_LINKS[claim["id"]]
                            else "unknown"
                        ),
                        "reason": "Hand-authored test correspondence.",
                    }
                    for claim in row["claims"]
                ],
            }
        )
    output = SemanticMatchingOutput(schema_version="0.1", items=items)
    return SemanticMatchingRun(
        run_id="unit_matching",
        started_at=datetime(2026, 11, 9, tzinfo=UTC),
        finished_at=datetime(2026, 11, 9, tzinfo=UTC),
        status="completed",
        provider="unit_fixture",
        model="none",
        input_fingerprint=input_fingerprint(snapshot),
        input_snapshot=snapshot,
        request={
            "system_prompt": "Unit fixture.",
            "user_prompt": "Unit fixture.",
            "schema_name": "unit_fixture",
            "json_schema": {},
        },
        proposal=output,
        output=output,
    )


def test_preparation_and_replay_never_call_a_provider(experiment, monkeypatch):
    from scione.providers import GeminiModelProvider, GroqModelProvider

    def forbidden(*args, **kwargs):
        pytest.fail("A replay/preparation helper attempted live inference.")

    monkeypatch.setattr(GeminiModelProvider, "generate_structured", forbidden)
    monkeypatch.setattr(GroqModelProvider, "generate_structured", forbidden)
    bundle = experiment.build_challenge()
    result = experiment.evaluate_extraction(bundle["oracle_run"])
    assert result["raw_model_atoms"]["exact_claim_true_positives"] == 20
    assert result["literal_evidence"]["rate"] == 1
    assert len(bundle["catalog"].tests) == 4


def test_code_baseline_exposes_novel_parameter_correspondence_gap(experiment):
    bundle = experiment.build_challenge()
    result = experiment.evaluate_code_only(
        bundle["oracle_run"], normalized_requirements=bundle["oracle_normalized"]
    )
    force = next(row for row in result["rows"] if row["gold_claim_id"] == "gold_thickness_force")
    assert force["expected"] == "supported"
    assert force["predicted"] == "unknown"
    assert not force["correct"]
    assert result["correct_decisions"] == 19


def test_wrong_extracted_attribute_is_error_even_when_capability_gold_is_unknown(experiment):
    bundle = experiment.build_challenge()
    for row in bundle["oracle_normalized"]:
        for claim in row.claims:
            if claim.id == "gold_thickness_material":
                claim.text_value = "A different material"
    result = experiment.evaluate_code_only(
        bundle["oracle_run"], normalized_requirements=bundle["oracle_normalized"]
    )
    material = next(
        row for row in result["rows"] if row["gold_claim_id"] == "gold_thickness_material"
    )
    assert material["expected"] == "unknown"
    assert material["predicted"] == "error"
    assert material["source_fact_correct"] is False
    assert material["correct"] is False


def test_wrong_stage_cannot_receive_an_exact_extraction_match(experiment):
    run = experiment.build_challenge()["oracle_run"]
    run.extraction.requirements[0].atomic_claims[0].stage = "curing"
    result = experiment.evaluate_extraction(run)
    assert result["raw_model_atoms"]["exact_claim_true_positives"] == 19
    assert result["raw_model_atoms"]["false_negative_claims"] == 1


def test_correct_synthetic_links_can_be_scored_without_real_api(experiment):
    result = experiment.evaluate_matching(manual_matching_attempt(experiment))
    assert result["correct_decisions"] == 20
    assert result["link_exact_correct_count"] == 20
    assert result["unsafe_supported_count"] == 0
    assert result["predicted_counts"] == {"supported": 17, "unknown": 2, "gap": 1}


def test_unresolved_ai_identity_cannot_pass_missing_attribute_labels(experiment):
    result = experiment.evaluate_matching(
        manual_matching_attempt(experiment, unknown_thickness=True)
    )
    thickness = [row for row in result["rows"] if row["test_id"] == "req_gold_thickness"]
    assert len(thickness) == 4
    assert all(row["predicted"] == "error" for row in thickness)
    assert all(row["correct"] is False for row in thickness)


def test_frozen_fixture_change_is_rejected_without_relabelling(experiment, monkeypatch, tmp_path):
    path = tmp_path / "changed_fixture.json"
    path.write_text(experiment.FIXTURE.read_text() + " ")
    monkeypatch.setattr(experiment, "FIXTURE", path)
    with pytest.raises(ValueError, match="Do not revise labels"):
        experiment.build_challenge()

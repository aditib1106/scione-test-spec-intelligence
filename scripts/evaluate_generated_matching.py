"""Score saved generated-corpus matching runs, or probe policy with no inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scione.evaluation.generated_matching import (
    EVALUATOR_SHA256,
    FROZEN_SHA256,
    build_batch,
    evaluate_matching,
    load_reference,
    oracle_run,
    rules_only_run,
)
from scione.matching.models import SemanticMatchingRun


def aggregate(scores: list[dict], fixture: dict) -> dict:
    supplied = [score["batch_id"] for score in scores]
    if len(supplied) != len(set(supplied)):
        raise ValueError(
            "Choose one attempt per batch; duplicate batches cannot inflate the score."
        )
    missing = sorted({row["id"] for row in fixture["batches"]} - set(supplied))
    expected = sum(len(case["normalized"]["claims"]) for case in fixture["cases"])
    executed = sum(score["expected_parameter_decisions"] for score in scores)
    correct = sum(score["correct_parameter_decisions"] for score in scores)
    return {
        "supplied_batches": sorted(supplied),
        "missing_batches": missing,
        "complete_batch_coverage": not missing,
        "expected_reference_cases": len(fixture["cases"]),
        "expected_reference_parameter_decisions": expected,
        "executed_parameter_decisions": executed,
        "correct_parameter_decisions": correct,
        "full_reference_parameter_accuracy": correct / expected if expected else None,
        "executed_parameter_accuracy": correct / executed if executed else None,
        "correct_links": sum(score["correct_links"] for score in scores),
        "raw_proposal_correct_links": sum(score["raw_proposal_correct_links"] for score in scores),
        "correct_identities": sum(score["correct_identities"] for score in scores),
        "correct_methods": sum(score["correct_methods"] for score in scores),
        "correct_overall": sum(score["correct_overall"] for score in scores),
        "false_support_count": sum(score["false_support_count"] for score in scores),
        "false_supported_method_count": sum(
            score["false_supported_method_count"] for score in scores
        ),
        "false_supported_item_count": sum(score["false_supported_item_count"] for score in scores),
        "contract_failures": sum(score["contract_failure"] for score in scores),
        "batch_scores": scores,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="Exact frozen fixture bytes, if moved.")
    parser.add_argument(
        "--run",
        type=Path,
        action="append",
        default=[],
        help="Saved SemanticMatchingRun JSON; repeat once per batch.",
    )
    parser.add_argument("--output", type=Path, help="Write a new report; otherwise print JSON.")
    args = parser.parse_args()
    fixture = load_reference(args.fixture)
    result = {
        "dataset_id": fixture["dataset_id"],
        "fixture_sha256": FROZEN_SHA256,
        "evaluator_sha256": EVALUATOR_SHA256,
        "limitations": fixture["limitations"],
    }
    if args.run:
        scores = [
            evaluate_matching(SemanticMatchingRun.model_validate_json(path.read_text()), fixture)
            for path in args.run
        ]
        result.update(mode="saved_matching_attempts", scores=aggregate(scores, fixture))
    else:
        probes = {}
        for name, builder in [("manual_oracle", oracle_run), ("rules_only", rules_only_run)]:
            scores = []
            for batch in fixture["batches"]:
                snapshot, _ = build_batch(batch["id"], fixture)
                scores.append(evaluate_matching(builder(snapshot, fixture), fixture))
            probes[name] = aggregate(scores, fixture)
        result.update(mode="offline_policy_probes_NOT_model_accuracy", policy_probes=probes)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if any(args.output.resolve() == path.resolve() for path in args.run):
            parser.error("The report output cannot replace a saved input run.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

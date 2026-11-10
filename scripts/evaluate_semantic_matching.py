#!/usr/bin/env python3
"""Replay frozen semantic-matching artifacts offline, or compile manual references.

This command never loads settings or credentials, creates a provider, calls a
model, starts a server, or updates application data. Source artifacts stay intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scione.evaluation.semantic_challenge import (
    FROZEN_SHA256,
    batch_snapshot,
    evaluate_matching,
    load_challenge,
    oracle_run,
)
from scione.matching.models import SemanticMatchingRun


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _metadata(attempt: SemanticMatchingRun) -> dict[str, Any]:
    return {
        "run_id": attempt.run_id,
        "provider": attempt.provider,
        "model": attempt.model,
        "prompt_version": attempt.prompt_version,
        "validation_version": attempt.validation_version,
        "input_fingerprint": attempt.input_fingerprint,
        "generation_settings": getattr(attempt, "generation_settings", {}),
        "usage": attempt.usage.model_dump(mode="json"),
        "latency_ms": attempt.latency_ms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        nargs="+",
        type=Path,
        default=[],
        help="One or more saved SemanticMatchingRun JSON paths; may be repeated.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_data/experiments/semantic-matching-replay"),
        help="Evaluation output directory; application source artifacts are never rewritten.",
    )
    args = parser.parse_args(argv)
    paths = [path for group in args.run for path in group]
    fixture = load_challenge()
    outputs = {args.output / "summary.json"}
    outputs.update(
        args.output / f"run_{index:03d}.score.json" for index in range(1, len(paths) + 1)
    )
    if any(path.resolve() in {output.resolve() for output in outputs} for path in paths):
        parser.error("Choose an output directory that does not overwrite an input artifact.")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    invalid = []
    if paths:
        for index, path in enumerate(paths, start=1):
            try:
                raw = path.read_bytes()
                attempt = SemanticMatchingRun.model_validate_json(raw)
                score = evaluate_matching(attempt, fixture)
            except (OSError, ValueError) as exc:
                invalid.append({"input_path": str(path), "error_type": type(exc).__name__})
                continue
            score_path = args.output / f"run_{index:03d}.score.json"
            _write(score_path, score)
            results.append(
                {
                    "input_path": str(path),
                    "input_artifact_sha256": hashlib.sha256(raw).hexdigest(),
                    "score_path": str(score_path),
                    "metadata": _metadata(attempt),
                    "score": score,
                }
            )
    else:
        for batch in fixture["batches"]:
            attempt = oracle_run(batch_snapshot(fixture, batch), fixture)
            score = evaluate_matching(attempt, fixture)
            score_path = args.output / f"{batch['id']}.oracle.score.json"
            _write(score_path, score)
            results.append(
                {
                    "input_kind": "manual_frozen_reference",
                    "batch_id": batch["id"],
                    "score_path": str(score_path),
                    "metadata": _metadata(attempt),
                    "score": score,
                }
            )
    claims = sum(result["score"]["expected_parameter_decisions"] for result in results)
    correct = sum(result["score"]["correct_parameter_decisions"] for result in results)
    seen_cases = [case["case_id"] for result in results for case in result["score"]["cases"]]
    summary = {
        "schema_version": "1.0",
        "dataset_id": fixture["dataset_id"],
        "fixture_sha256": FROZEN_SHA256,
        "comparison_mode": "CURRENT_POLICY_RECOMPILE",
        "new_api_calls": 0,
        "mode": "saved_matching_output_replay" if paths else "manual_reference_policy_probe",
        "manual_reference_run_count": sum(
            result["metadata"]["provider"] == "manual_oracle" for result in results
        ),
        "run_count": len(results),
        "invalid_artifact_count": len(invalid),
        "invalid_artifacts": invalid,
        "unique_case_count": len(set(seen_cases)),
        "case_trial_count": len(seen_cases),
        "overlapping_case_trials": len(set(seen_cases)) != len(seen_cases),
        "expected_parameter_decisions_across_trials": claims,
        "correct_parameter_decisions_across_trials": correct,
        "parameter_accuracy_across_trials": correct / claims if claims else None,
        "false_support_count_across_trials": sum(
            result["score"]["false_support_count"] for result in results
        ),
        "false_gap_count_across_trials": sum(
            result["score"]["false_gap_count"] for result in results
        ),
        "contract_failure_count": sum(result["score"]["contract_failure"] for result in results),
        "results": results,
        "limitations": fixture["limitations"],
        "aggregation_note": (
            "Totals count run trials, not independent or unique production samples. "
            "Repeated artifacts/cases remain visible. Manual reference mode measures policy "
            "with author-typed inputs and is never an LLM extraction or matching result."
        ),
    }
    _write(args.output / "summary.json", summary)
    print(
        f"Offline semantic CURRENT_POLICY_RECOMPILE: {len(results)} runs, "
        f"{correct}/{claims} parameter decisions, "
        f"{len(invalid)} invalid artifacts, 0 API calls. Results: {args.output / 'summary.json'}"
    )
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())

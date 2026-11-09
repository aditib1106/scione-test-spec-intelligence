#!/usr/bin/env python3
"""Evaluate local comparison policy or replay saved decisions, with no API calls.

Examples (from the repository root after installing the project):
  python scripts/evaluate_capability.py
  python scripts/evaluate_capability.py --capture-baseline-only
  python scripts/evaluate_capability.py --baseline-snapshot path/to/baseline.json
  python scripts/evaluate_capability.py --cases human_labels.json --predictions saved_outputs.json
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from scione.evaluation import (
    compare_snapshots,
    load_dataset,
    load_snapshot,
    run_current_comparator,
    snapshot_from_matching_artifacts,
    write_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/capability_gold_v1.json")
    )
    parser.add_argument("--baseline-snapshot", type=Path)
    parser.add_argument(
        "--predictions", type=Path, help="Replay explicit saved matching decisions."
    )
    parser.add_argument(
        "--artifacts", type=Path, help="Replay a matching artifact exported by the API."
    )
    parser.add_argument("--capture-baseline-only", action="store_true")
    parser.add_argument("--strategy", default="current_comparator")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_data/experiments")
        / datetime.now(UTC).strftime("capability-%Y%m%dT%H%M%SZ"),
    )
    args = parser.parse_args(argv)
    if args.predictions and args.artifacts:
        parser.error("Choose either --predictions or --artifacts.")
    if args.capture_baseline_only and (args.predictions or args.artifacts):
        parser.error("A replay cannot be captured as a fresh comparator baseline.")
    try:
        dataset = load_dataset(args.cases)
        fingerprint = hashlib.sha256(args.cases.read_bytes()).hexdigest()
        if args.artifacts:
            current = snapshot_from_matching_artifacts(
                dataset, args.artifacts, fixture_sha256=fingerprint
            )
        elif args.predictions:
            current = load_snapshot(args.predictions)
        else:
            current = run_current_comparator(
                dataset, strategy=args.strategy, fixture_path=args.cases
            )
        baseline = load_snapshot(args.baseline_snapshot) if args.baseline_snapshot else None
        report = compare_snapshots(dataset, current, baseline, fixture_sha256=fingerprint)
        args.output.mkdir(parents=True, exist_ok=True)
        snapshot_path = args.output / (
            "baseline_snapshot.json" if args.capture_baseline_only else "current_snapshot.json"
        )
        snapshot_path.write_text(current.model_dump_json(indent=2) + "\n", encoding="utf-8")
        json_path, markdown_path = write_report(report, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Offline evaluation could not run: {error}\n")
    metrics = report["current"]["metrics"]
    print(
        f"{current.strategy}: {metrics['correct_count']}/{metrics['assertion_count']} "
        f"parameter decisions correct; {metrics['false_support_count']}/"
        f"{metrics['predicted_supported_count']} false-supported."
    )
    print(f"Annotation status: {dataset.annotation_status}. No model calls.")
    print(f"Snapshot: {snapshot_path}\nReports: {json_path} and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

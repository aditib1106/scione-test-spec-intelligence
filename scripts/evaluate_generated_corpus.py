#!/usr/bin/env python3
"""Score saved extraction artifacts against an explicit partial source reference offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scione.evaluation.generated_corpus import evaluate_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--reference-sha256")
    parser.add_argument("--errata", type=Path)
    parser.add_argument("--errata-sha256")
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.runs.is_dir():
        parser.error("--runs must be an existing saved-artifact directory.")
    output = args.output.resolve()
    inputs = {args.reference.resolve(), *args.runs.rglob("*.json")}
    if args.errata:
        inputs.add(args.errata.resolve())
    if output in {path.resolve() for path in inputs}:
        parser.error("Choose an output path that does not overwrite source artifacts.")
    try:
        result = evaluate_corpus(
            args.reference,
            args.runs,
            reference_sha256=args.reference_sha256,
            errata=args.errata,
            errata_sha256=args.errata_sha256,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"Cannot evaluate supplied inputs: {type(exc).__name__}: {exc}")
    result["provenance"]["cli_source_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = result["summary"]
    print(
        f"Offline partial source evaluation: {summary['valid_saved_contract_documents']}/"
        f"{summary['expected_documents']} valid saved contracts, "
        f"{summary['recovered_method_references']}/{summary['expected_method_references']} "
        f"exact method references; 0 API calls. Output: {args.output}"
    )
    return 1 if summary["failed_or_missing_documents"] or result["unassigned_artifacts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

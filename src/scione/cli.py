"""Command-line entry points for the extraction workbench."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from scione.evaluation import TestMethodEvaluator
from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.schemas import TestMethodExtraction, TextReadingOrder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scione",
        description="Inspect and extract structured information from test documents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Extract page-aware text and metadata from a text-based PDF.",
    )
    ingest_parser.add_argument("pdf", type=Path, help="Path to the PDF document.")
    ingest_parser.add_argument(
        "--reading-order",
        choices=[order.value for order in TextReadingOrder],
        default=TextReadingOrder.CONTENT_STREAM.value,
        help="Plain-text reading order to request from PyMuPDF.",
    )

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Compare a method-extraction prediction with ground truth.",
    )
    evaluate_parser.add_argument("prediction", type=Path)
    evaluate_parser.add_argument("ground_truth", type=Path)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "ingest":
        try:
            document = PyMuPDFTextIngestor(
                reading_order=TextReadingOrder(args.reading_order)
            ).ingest(args.pdf)
        except IngestionError as exc:
            print(f"Ingestion failed: {exc}")
            return 1

        print(document.model_dump_json(indent=2))
        return 0

    if args.command == "evaluate":
        try:
            prediction = TestMethodExtraction.model_validate_json(
                args.prediction.read_text(encoding="utf-8")
            )
            ground_truth = TestMethodExtraction.model_validate_json(
                args.ground_truth.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            print(f"Evaluation input is invalid: {exc}")
            return 1

        report = TestMethodEvaluator().evaluate(prediction, ground_truth)
        print(report.model_dump_json(indent=2))
        return 0

    return 2

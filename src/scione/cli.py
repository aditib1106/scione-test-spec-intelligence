"""Command-line entry points for the extraction workbench."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from scione.config import ConfigurationError, Settings
from scione.evaluation import TestMethodEvaluator
from scione.extraction import ExtractionError, MethodExtractionRunner
from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.providers import ProviderError, StaticModelProvider
from scione.runs import RunStoreError
from scione.schemas import TestMethodExtraction, TextReadingOrder
from scione.workbench import create_configured_provider, execute_method_workbench


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

    extract_parser = subparsers.add_parser(
        "extract-method-static",
        help="Run the full extraction pipeline with a deterministic JSON fixture.",
    )
    extract_parser.add_argument("pdf", type=Path)
    extract_parser.add_argument("static_response", type=Path)
    extract_parser.add_argument(
        "--reading-order",
        choices=[order.value for order in TextReadingOrder],
        default=TextReadingOrder.CONTENT_STREAM.value,
    )

    groq_parser = subparsers.add_parser(
        "extract-method-groq",
        help="Extract a synthetic method PDF with the configured Groq model.",
    )
    groq_parser.add_argument("pdf", type=Path)
    groq_parser.add_argument(
        "--ground-truth",
        type=Path,
        help="Optional answer key used to produce an evaluation artifact.",
    )
    groq_parser.add_argument(
        "--reading-order",
        choices=[order.value for order in TextReadingOrder],
        default=TextReadingOrder.CONTENT_STREAM.value,
    )

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

    if args.command == "extract-method-static":
        try:
            document = PyMuPDFTextIngestor(
                reading_order=TextReadingOrder(args.reading_order)
            ).ingest(args.pdf)
            provider = StaticModelProvider.from_json_file(args.static_response)
            run = MethodExtractionRunner(provider).run(document)
        except (IngestionError, ProviderError, ExtractionError) as exc:
            print(f"Extraction failed: {exc}")
            return 1

        print(run.model_dump_json(indent=2))
        return 0

    if args.command == "extract-method-groq":
        try:
            settings = Settings()
            result = execute_method_workbench(
                args.pdf,
                provider=create_configured_provider(settings),
                runs_dir=settings.scione_runs_dir,
                reading_order=TextReadingOrder(args.reading_order),
                ground_truth_path=args.ground_truth,
            )
        except (
            ConfigurationError,
            ExtractionError,
            IngestionError,
            OSError,
            RunStoreError,
            ValidationError,
        ) as exc:
            print(f"Extraction failed: {exc}")
            return 1

        summary = {
            "run_id": result.run.run_id,
            "run_directory": str(result.run_directory),
            "provider": result.run.provider,
            "model": result.run.model,
            "prompt_version": result.run.prompt_version,
            "schema_version": result.run.schema_version,
            "latency_ms": result.run.latency_ms,
            "usage": result.run.usage.model_dump(),
            "evaluated": result.evaluation is not None,
        }
        print(json.dumps(summary, indent=2))
        return 0

    return 2

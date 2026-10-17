"""Offline command-line inspection for customer specification PDFs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.schemas import TextReadingOrder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scione",
        description=(
            "Inspect page-aware text from customer specification PDFs, without model calls."
        ),
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        document = PyMuPDFTextIngestor(
            reading_order=TextReadingOrder(args.reading_order)
        ).ingest(args.pdf)
    except IngestionError as exc:
        print(f"Ingestion failed: {exc}")
        return 1

    print(document.model_dump_json(indent=2))
    return 0

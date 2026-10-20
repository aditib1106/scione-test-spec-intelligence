"""Offline PDF inspection and recoverable local demo maintenance."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.schemas import TextReadingOrder
from scione.workspace_reset import DemoResetError, reset_demo_workspace


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

    reset_parser = subparsers.add_parser(
        "reset-demo",
        help="Back up local lab, uploaded specifications, reviews, and reports for a fresh demo.",
    )
    reset_parser.add_argument(
        "--yes", action="store_true", help="Explicitly confirm moving saved demo state to a backup."
    )
    reset_parser.add_argument(
        "--workspace", type=Path, help="Local data directory (defaults to configured workspace)."
    )
    reset_parser.add_argument(
        "--backup-dir", type=Path, help="Recovery directory named .demo-backups."
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "reset-demo":
        if not args.yes:
            print("Reset cancelled. Stop active demo operations, then add --yes to confirm.")
            return 1
        from scione.config import Settings

        try:
            workspace = args.workspace or Settings().scione_workspace_dir
            result = reset_demo_workspace(workspace, args.backup_dir)
        except (DemoResetError, ValueError) as exc:
            print(f"Demo reset failed: {exc}")
            return 1
        if result.backup_path is None:
            print("No saved demo state to reset. Credentials and project files were not changed.")
        else:
            print(
                f"Moved {result.file_count} saved demo files to {result.backup_path}. "
                "Credentials and project files were not changed. Restart or refresh the web app."
            )
        return 0

    try:
        document = PyMuPDFTextIngestor(reading_order=TextReadingOrder(args.reading_order)).ingest(
            args.pdf
        )
    except IngestionError as exc:
        print(f"Ingestion failed: {exc}")
        return 1

    print(document.model_dump_json(indent=2))
    return 0

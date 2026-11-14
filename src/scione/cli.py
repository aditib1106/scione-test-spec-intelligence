"""Local workspace server, offline PDF inspection and recoverable demo maintenance."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from scione.ingestion import IngestionError, PyMuPDFTextIngestor
from scione.schemas import TextReadingOrder
from scione.workspace_reset import DemoResetError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scione",
        description=(
            "Run the local lab workspace, inspect customer PDFs offline, or manage demo state."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    from scione.auth.accounts_cli import configure_cli

    configure_cli(subparsers)

    serve_parser = subparsers.add_parser(
        "serve", help="Run the local MUI workspace and its Python API. Build frontend first."
    )
    serve_parser.add_argument("--port", type=int, default=8000, help="Local HTTP port.")

    subparsers.add_parser(
        "database-upgrade", help="Apply additive SQL lab-storage migrations; preserve saved data."
    )

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

    if args.command == "accounts":
        import os

        from sqlalchemy.exc import SQLAlchemyError

        from scione.api.tenancy import TenantWorkspaceRegistry
        from scione.auth.accounts_cli import run_command
        from scione.auth.store import AuthStoreError
        from scione.config import ConfigurationError, Settings
        from scione.deployment import (
            prepare_data_directory,
            prepare_host_environment,
            provision_callback,
        )

        registry = None
        try:
            if os.environ.get("RAILWAY_SERVICE_ID") or (
                os.environ.get("SCIONE_ENVIRONMENT") == "production"
            ):
                prepare_host_environment()
            settings = Settings()
            if settings.scione_environment == "production":
                # An operator's new Docker exec process starts with its own
                # environment and UID. Match hosted startup before any account
                # or tenant files are created, including the privilege drop.
                prepare_data_directory(settings)
            registry = TenantWorkspaceRegistry(settings)
            return run_command(args, settings, provision_callback(registry))
        except (
            ConfigurationError,
            AuthStoreError,
            ValueError,
            SQLAlchemyError,
            OSError,
        ):
            print("Account operation failed. Check the private server and storage configuration.")
            return 1
        finally:
            if registry is not None:
                registry.close_all()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("scione.api.app:app", host="127.0.0.1", port=args.port)
        return 0

    if args.command == "database-upgrade":
        from sqlalchemy.exc import SQLAlchemyError

        from scione.catalogs.sql_lab_store import upgrade_lab_schema
        from scione.config import Settings
        from scione.library.assessment_reviews import upgrade_assessment_reviews_schema
        from scione.library.store import LibraryStore, LibraryStoreError
        from scione.matching.store import upgrade_matching_schema
        from scione.monitoring.store import upgrade_monitoring_schema
        from scione.specifications.corrections import upgrade_corrections_schema

        settings = Settings()
        store = LibraryStore(
            settings.scione_workspace_dir,
            settings.scione_database_url.get_secret_value()
            if settings.scione_database_url
            else None,
        )
        try:
            upgrade_lab_schema(store)
            upgrade_matching_schema(store)
            upgrade_corrections_schema(store)
            upgrade_assessment_reviews_schema(store)
            upgrade_monitoring_schema(store)
        except (LibraryStoreError, SQLAlchemyError):
            print("Database upgrade failed. Check connection settings; saved data is preserved.")
            return 1
        finally:
            store.close()
        print("SQL lab-storage schema is up to date. Existing archive records were preserved.")
        return 0

    if args.command == "reset-demo":
        if not args.yes:
            print("Reset cancelled. Stop active demo operations, then add --yes to confirm.")
            return 1
        from scione.config import Settings
        from scione.library.reset import reset_library_workspace

        try:
            settings = Settings()
            if args.workspace:
                settings = settings.model_copy(update={"scione_workspace_dir": args.workspace})
            result = reset_library_workspace(settings, backup_root=args.backup_dir)
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

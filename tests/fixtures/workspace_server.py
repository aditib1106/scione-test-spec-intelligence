"""Serve the real MUI/API stack with disposable data and forbidden provider calls.

Used by Playwright; never points at a developer's local_data or reads .env.
"""

import argparse
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import uvicorn

from scione.catalogs import FileLabStore, load_reference_catalog
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.reports.store import FileReportStore
from scione.specifications.store import FileSpecStore


def reject_provider(*_args, **_kwargs):
    raise AssertionError("The browser fixture must never make a real model call.")


def seed_review(root: Path):
    path = Path(__file__).parents[1] / "integration" / "test_reports_page.py"
    spec = importlib.util.spec_from_file_location("offline_browser_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stores = (
        FileSpecStore(root),
        FileReportStore(root),
        FileLabStore(root, load_reference_catalog()),
    )
    document, run = module.seed(stores, name="offline-customer-coating.pdf")
    run = run.model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.review_issues = ["req_pencil: applicability needs checking."]
    stores[0].save_extraction(document.document_id, run)
    module.seed(stores, name="offline-two-test-spec.pdf", count=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--seed-review", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scione-browser-") as directory:
        root = Path(directory)
        os.environ["SCIONE_WORKSPACE_DIR"] = str(root)
        os.environ["GROQ_API_KEY"] = ""
        os.environ["GEMINI_API_KEY"] = ""
        Settings.model_config["env_file"] = None
        GroqModelProvider.generate_structured = reject_provider
        GeminiModelProvider.generate_structured = reject_provider
        if args.seed_review:
            seed_review(root)
        from scione.api.app import create_app

        app = create_app(Settings(_env_file=None, scione_workspace_dir=root))
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

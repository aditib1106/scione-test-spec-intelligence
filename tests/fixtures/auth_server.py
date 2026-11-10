"""Disposable authenticated browser workspace; no credentials or model calls."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pymupdf
import uvicorn

from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth import AuthManager
from scione.auth.passwords import hash_password
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.openai_compatible import OpenAICompatibleModelProvider
from scione.specifications.models import CustomerExtractionRun

FIXTURE_PASSWORD = "Synthetic-fixture-password-2026"


def reject_provider(*_args, **_kwargs):
    raise AssertionError("The authenticated browser fixture forbids model calls.")


def seed_lab(workspace, name: str, filename: str):
    lab = workspace.labs.load_or_create()
    lab.name = name
    workspace.labs.save(lab)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text(
            (40, 50), f"{name} customer: Gloss test ISO 2813. Measure at 60 degrees."
        )
        document = workspace.specs.import_pdf(filename, pdf.tobytes())
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "document_id": document.document_id,
            "started_at": datetime.now(UTC),
            "schema_version": "0.1",
            "prompt_version": "offline_auth_browser_fixture",
            "provider": "offline",
            "model": "no_network",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "PRIVATE INFERENCE ARTIFACT MUST NOT ENTER LAB VIEW",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": f"{name} finish requirements"},
                "requirements": [
                    {
                        "id": "req_gloss",
                        "original_test_name": "Gloss",
                        "requested_methods": [{"code": "ISO 2813"}],
                        "conditions": [
                            {"name": "Measurement angle", "raw_value": "60", "unit": "°"}
                        ],
                        "evidence": [{"page": 1, "quote": "Gloss test ISO 2813."}],
                    }
                ],
            },
        }
    )
    workspace.specs.save_extraction(document.document_id, run)
    workspace.library.index(document.document_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scione-auth-browser-") as directory:
        root = Path(directory).resolve()
        Settings.model_config["env_file"] = None
        for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "FIREWORKS_API_KEY", "SAIL_API_KEY"):
            os.environ[key] = ""
        for provider in (GroqModelProvider, GeminiModelProvider, OpenAICompatibleModelProvider):
            provider.generate_structured = reject_provider
        settings = Settings(
            _env_file=None,
            scione_workspace_dir=root,
            scione_database_url=None,
            scione_auth_database_url=None,
            scione_bootstrap_accounts=None,
            scione_environment="development",
            scione_public_url=f"http://127.0.0.1:{args.port}",
            scione_auth_enabled=True,
            scione_cookie_secure=False,
            groq_api_key=None,
            gemini_api_key=None,
            fireworks_api_key=None,
            sail_api_key=None,
        )
        manager = AuthManager(settings)
        manager.initialize()
        registry = TenantWorkspaceRegistry(settings)
        password = hash_password(FIXTURE_PASSWORD)
        for name, username, filename in (
            ("Laboratory Alpha", "labalpha", "alpha-customer-spec.pdf"),
            ("Laboratory Beta", "labbeta", "beta-customer-spec.pdf"),
        ):
            lab = manager.store.create_lab(name)
            registry.provision(lab.id)
            manager.store.mark_lab_provisioned(lab.id)
            manager.store.create_user(username, name, "lab", password, lab_id=lab.id)
            seed_lab(registry.workspace_for(lab.id), name, filename)
        manager.store.create_user("maintainer", "SciOne maintainer", "maintainer", password)
        for name in ("password.desktop", "password.narrow"):
            manager.store.create_user(
                name, "Password test account", "lab", password,
                lab_id=manager.store.lab_by_name("Laboratory Alpha").id,
            )
        manager.close()
        registry.close_all()
        from scione.api.app import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

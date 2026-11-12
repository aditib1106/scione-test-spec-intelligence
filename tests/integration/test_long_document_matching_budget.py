"""Matching call allowance uses stored PDF length, never model-authored metadata."""

from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest

from scione.api.workspace import Workspace
from scione.config import Settings
from scione.specifications.models import CustomerExtractionRun

SOURCE = "Pencil hardness ISO 15184: operating temperature 23 degrees C."


def pdf_bytes(page_count: int) -> bytes:
    with pymupdf.open() as pdf:
        for index in range(page_count):
            page = pdf.new_page()
            page.insert_text((40, 40), SOURCE)
            page.insert_text((40, 65), f"Physical source page {index + 1} of {page_count}.")
        return pdf.tobytes()


@pytest.mark.parametrize("page_count,expected_calls", [(2, 8), (10, 8), (11, 16), (13, 16)])
def test_stored_physical_pages_select_bounded_allowance_despite_conflicting_metadata(
    tmp_path, monkeypatch, page_count, expected_calls,
):
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path, scione_database_url=None,
        groq_api_key=None, gemini_api_key=None, sail_api_key=None, fireworks_api_key=None,
    )
    workspace = Workspace(settings)
    observed = {}
    provider_calls = []
    claimed_page_count = 99 if page_count <= 10 else 2

    class ReachedBoundedMatcher(RuntimeError):
        pass

    def capture(provider, snapshot, **options):
        observed.update(options)
        observed["extraction_run_id"] = snapshot.extraction_run_id
        raise ReachedBoundedMatcher

    def forbid_provider(*_args, **_kwargs):
        provider_calls.append(True)
        pytest.fail("The matching budget integration probe must not call a model.")

    monkeypatch.setattr(
        import_module("scione.matching.workspace"), "run_bounded_matching", capture,
    )
    try:
        stored = workspace.specs.import_pdf(
            f"model-claims-{claimed_page_count}-pages.pdf", pdf_bytes(page_count),
        )
        assert stored.document.page_count == page_count
        extraction = CustomerExtractionRun.model_validate({
            "run_id": str(uuid4()), "started_at": datetime.now(UTC),
            "document_id": stored.document_id, "prompt_version": "offline_budget_probe",
            "schema_version": "0.1", "provider": "static", "model": "offline_probe",
            "generation_settings": {"page_count": claimed_page_count, "max_calls": 999},
            "latency_ms": 1, "usage": {},
            "raw_response": f"Untrusted response claims this file has {claimed_page_count} pages.",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": f"Untrusted title: {claimed_page_count} pages"},
                "requirements": [{
                    "id": "req_pencil", "original_test_name": "Pencil hardness",
                    "requested_methods": [{"code": "ISO 15184"}],
                    "conditions": [{"name": "Temperature", "raw_value": "23", "unit": "°C"}],
                    "evidence": [{"page": 1, "quote": SOURCE}],
                }],
            },
        })
        workspace.specs.save_extraction(stored.document_id, extraction)
        report = workspace.draft(stored.document_id)
        body = SimpleNamespace(
            run_id=report.source_run.run_id, input_fingerprint=report.input_fingerprint,
            rerun=False,
        )
        provider = SimpleNamespace(
            provider_name="static", model_name=f"metadata-says-{claimed_page_count}-pages",
            generation_settings={"page_count": claimed_page_count, "max_calls": 999},
            generate_structured=forbid_provider,
        )
        with pytest.raises(ReachedBoundedMatcher):
            workspace.matching.run(stored.document_id, body, provider)
        assert observed["max_calls"] == expected_calls
        assert observed["max_input_characters"] == settings.scione_matching_input_character_limit
        assert observed["extraction_run_id"] == extraction.run_id
        assert provider_calls == []
    finally:
        workspace.library.store.close()

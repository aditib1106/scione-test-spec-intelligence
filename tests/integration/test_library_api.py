"""End-to-end SQL archive/comparison contracts, without TCP servers or model calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from scione.api.app import create_app
from scione.catalogs import FileLabStore, load_reference_catalog
from scione.catalogs.seed import default_csp301_lab
from scione.catalogs.sql_lab_store import SQLLabStore
from scione.config import Settings
from scione.library.store import LibraryStore, comparisons
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore

GLOSS = (
    "Specular gloss: ASTM D1627-16. Initial gloss 82-92 GU. "
    "Post-cycle gloss retention at least 90%."
)
THICKNESS = "Dry film thickness: ISO 60152:2025. Separate topcoat thickness 42-55 um."


class LocalClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture
def client(tmp_path, monkeypatch):
    def reject_model(*_args, **_kwargs):
        pytest.fail("Archive, comparison and worked-example actions must not call a model.")

    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model)
    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model)
    root = tmp_path / "workspace"
    settings = Settings(
        _env_file=None,
        scione_workspace_dir=root,
        scione_database_url=None,
        gemini_api_key=None,
        groq_api_key=None,
    )
    app = create_app(settings, frontend_dir=tmp_path / "no-frontend")
    yield LocalClient(app), root


def _pdf(revision="Issue A") -> bytes:
    with pymupdf.open() as doc:
        page = doc.new_page()
        text = f"Synthetic customer specification / {revision}\n{GLOSS}\n{THICKNESS}"
        page.insert_textbox(pymupdf.Rect(35, 35, 560, 750), text, fontsize=10)
        return doc.tobytes(no_new_id=True)


def _seed(root: Path, revision="Issue A"):
    specs = FileSpecStore(root)
    source = specs.import_pdf(f"customer-{revision}.pdf", _pdf(revision))
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "document_id": source.document_id,
            "started_at": datetime(2026, 11, 3, tzinfo=UTC),
            "schema_version": "0.1",
            "prompt_version": "offline_integration_fixture",
            "provider": "static",
            "model": "no_network",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "Untouched model-response fixture\n",
            "extraction": {
                "schema_version": "0.1",
                "document": {
                    "title": "Finish approval",
                    "identifier": "CSP-EN-301",
                    "revision": revision,
                },
                "requirements": [
                    {
                        "id": "req_gloss",
                        "original_test_name": "Specular gloss",
                        "requested_methods": [{"code": "ASTM D1627-16"}],
                        "acceptance_criteria": [
                            {"name": "Initial gloss", "raw_value": "Initial gloss 82-92 GU"},
                            {
                                "name": "Retention",
                                "raw_value": "Post-cycle gloss retention at least 90%",
                            },
                        ],
                        "evidence": [{"page": 1, "quote": GLOSS}],
                    },
                    {
                        "id": "req_thickness",
                        "original_test_name": "Dry film thickness",
                        "requested_methods": [{"code": "ISO 60152:2025"}],
                        "acceptance_criteria": [
                            {
                                "name": "Topcoat thickness",
                                "raw_value": "Separate topcoat thickness 42-55 um",
                            }
                        ],
                        "evidence": [{"page": 1, "quote": THICKNESS}],
                    },
                ],
            },
        }
    )
    specs.save_extraction(source.document_id, run)
    specs.save_reviews(
        source.document_id,
        run.run_id,
        ["req_gloss", "req_thickness"],
        "confirmed",
        "Checked against the offline source",
        expected_version=0,
    )
    labs = FileLabStore(root, load_reference_catalog(), seed_demo=False)
    lab = labs.load_or_create()
    if not lab.capabilities:
        lab.capabilities = default_csp301_lab().capabilities
        labs.save(lab)
    return source, run


def _metadata(document, **changes):
    return {
        "expected_version": document["version"],
        "company_name": "Synthetic Customer",
        "title": "Finish approval",
        "specification_id": "CSP-EN-301",
        "revision": document["revision"],
        **changes,
    }


def test_library_persists_atomic_fields_company_and_revision_relationships(client):
    http, root = client
    source, run = _seed(root)
    response = http.get(f"/api/library/{source.document_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["document"]["specification_id"] == "CSP-EN-301"
    assert data["document"]["revision"] == "Issue A"
    thickness = next(
        row for row in data["requirements"] if row["requirement_id"] == "req_thickness"
    )
    atom = next(row for row in thickness["claims"] if row["parameter_id"] == "topcoat_thickness")
    assert atom["minimum"] == "42"
    assert atom["maximum"] == "55"
    assert atom["unit"] in {"µm", "μm"}
    assert atom["source_page"] == 1
    assert atom["display_value"]
    saved = http.put(
        f"/api/library/{source.document_id}/metadata", json=_metadata(data["document"])
    )
    assert saved.status_code == 200
    original = saved.json()["document"]
    later, _ = _seed(root, "Issue B")
    next_doc = http.get(f"/api/library/{later.document_id}").json()["document"]
    linked = http.put(
        f"/api/library/{later.document_id}/metadata",
        json=_metadata(next_doc, family_id=original["family_id"], title="New upload title"),
    )
    assert linked.status_code == 200
    assert len(linked.json()["versions"]) == 2
    assert {item["revision"] for item in linked.json()["versions"]} == {"Issue A", "Issue B"}
    assert linked.json()["document"]["title"] == original["title"]
    assert len(http.get("/api/library").json()["documents"]) == 2
    assert FileSpecStore(root).load_extraction(source.document_id, run.run_id) == run
    assert (root / "library.sqlite3").is_file()
    stale = http.put(
        f"/api/library/{source.document_id}/metadata", json=_metadata(data["document"])
    )
    assert stale.status_code == 409


def test_comparison_exposes_explicit_limit_and_missing_layer_capability(client):
    http, root = client
    source, _ = _seed(root)

    response = http.get(f"/api/comparisons/{source.document_id}")

    assert response.status_code == 200
    rows = {row["requirement_id"]: row["comparison"] for row in response.json()["requirements"]}
    gloss = rows["req_gloss"]
    assert gloss["status"] == "partially_matched"
    assert any(
        check["status"] == "gap" and check["parameter_id"] == "gloss_retention"
        for check in gloss["checks"]
    )
    thickness = rows["req_thickness"]
    assert thickness["status"] == "needs_information"
    assert any(
        check["status"] == "unknown" and check["parameter_id"] == "topcoat_thickness"
        for check in thickness["checks"]
    )


def test_pending_method_identity_does_not_report_lab_values_as_missing(client, monkeypatch):
    from scione.api.workspace import Workspace

    http, root = client
    source, _ = _seed(root)
    draft = Workspace.draft

    def ambiguous_method(workspace, document_id):
        report = draft(workspace, document_id).model_copy(deep=True)
        line = report.lines[0]
        # The lab has a gloss declaration, but no declaration can be selected
        # until this test's ambiguous method identity is resolved.
        assert any(cap.canonical_test_id == "gloss" for cap in report.lab.capabilities)
        line.status = "needs_review"
        line.capability = None
        line.unknown_methods = ["Method identity needs confirmation"]
        line.requirement.special_instructions.append("Use the buyer's special sample holder.")
        return report

    monkeypatch.setattr(Workspace, "draft", ambiguous_method)
    response = http.get(f"/api/comparisons/{source.document_id}")

    assert response.status_code == 200, response.text
    result = response.json()["requirements"][0]["comparison"]
    assert result["checks"][0]["status"] == "unknown"
    parsed = [
        check for check in result["checks"] if check["parameter_id"] in {"gloss", "gloss_retention"}
    ]
    assert parsed
    assert all(check["declared"] == "Not compared: confirm test/method" for check in parsed)
    assert all("Confirm the requested test/method" in check["reason"] for check in parsed)
    unresolved = next(
        check for check in result["checks"] if "special sample holder" in check["requested"]
    )
    assert unresolved["declared"] is None
    assert "not been normalized reliably" in unresolved["reason"]


def test_lab_update_changes_new_comparison_without_reextracting_or_rewriting_history(client):
    http, root = client
    source, run = _seed(root)
    before = http.get(f"/api/comparisons/{source.document_id}").json()
    lab = http.get("/api/lab").json()
    capability = next(item for item in lab["capabilities"] if item["id"] == "cap_csp301_thickness")
    capability["notes"] = (
        "Reportable outputs: Dry-film thickness: 1–1000 µm; "
        "Separate topcoat thickness: 1–1000 µm.\nSource: Reviewed lab declaration."
    )
    assert http.put("/api/lab", json=lab).status_code == 200

    response = http.get(f"/api/comparisons/{source.document_id}")

    assert response.status_code == 200
    after = response.json()
    assert after["comparison_id"] != before["comparison_id"]
    old = next(row for row in before["requirements"] if row["requirement_id"] == "req_thickness")
    new = next(row for row in after["requirements"] if row["requirement_id"] == "req_thickness")
    assert old["comparison"]["status"] == "needs_information"
    assert new["comparison"]["status"] == "fully_matched"
    frozen = http.get(f"/api/comparison-snapshots/{before['comparison_id']}/json")
    assert frozen.status_code == 200
    assert frozen.json() == before
    assert FileSpecStore(root).load_extraction(source.document_id, run.run_id) == run
    assert len(FileSpecStore(root).list_extractions(source.document_id)) == 1


def test_saved_comparison_exports_use_same_frozen_json_and_pdf(client, monkeypatch):
    http, root = client
    source, _ = _seed(root)
    saved = http.get(f"/api/comparisons/{source.document_id}").json()
    comparison_id = saved["comparison_id"]
    endpoint = f"/api/comparison-snapshots/{comparison_id}"
    first = http.get(endpoint + "/pdf?download=true")
    assert first.status_code == 200
    assert first.headers["content-type"] == "application/pdf"
    assert "attachment" in first.headers["content-disposition"]
    assert first.content.startswith(b"%PDF-")
    info = http.get(endpoint)
    assert info.status_code == 200
    assert info.json()["comparison"] == saved
    assert 1 <= info.json()["page_count"] <= 6
    preview = http.get(endpoint + "/pages/1")
    assert preview.status_code == 200
    assert preview.content.startswith(b"\x89PNG")
    rendered_page = pymupdf.Pixmap(preview.content)
    assert 1000 <= rendered_page.width <= 2000
    assert 1000 <= rendered_page.height <= 2000
    assert http.get(endpoint + "/json").json() == saved

    def reject_rerender(_payload):
        pytest.fail("Downloading a saved PDF must reuse its frozen bytes.")

    monkeypatch.setattr("scione.api.app.render_comparison_pdf", reject_rerender)
    assert http.get(endpoint + "/pdf").content == first.content


def test_database_failure_is_sanitized_and_preserves_source_files(client, monkeypatch):
    http, root = client
    source, _ = _seed(root)
    pdf_before = FileSpecStore(root).read_pdf(source.document_id)

    def unavailable(*_args, **_kwargs):
        raise OperationalError("SECRET_CONNECTION_SENTINEL", {}, RuntimeError("password=hidden"))

    monkeypatch.setattr("scione.library.store.create_engine", unavailable)
    response = http.get("/api/library")

    assert response.status_code in {400, 503}
    assert "database" in response.json()["detail"]
    assert "SECRET_CONNECTION_SENTINEL" not in response.text
    assert "password" not in response.text
    assert FileSpecStore(root).read_pdf(source.document_id) == pdf_before


def test_human_scope_limitation_remains_a_gap_until_lab_revision_changes(client):
    http, root = client
    source, run = _seed(root)
    endpoint = f"/api/reports/{source.document_id}"
    draft = http.get(endpoint).json()["report"]
    response = http.post(
        endpoint + "/scope",
        json={
            "run_id": run.run_id,
            "input_fingerprint": draft["input_fingerprint"],
            "version": draft["workspace"]["version"],
            "requirement_ids": ["req_thickness"],
            "supported": False,
            "note": "The lab reviewed this request and cannot measure the separate topcoat.",
        },
    )
    assert response.status_code == 200, response.text
    result = http.get(f"/api/comparisons/{source.document_id}").json()
    line = next(
        item for item in result["requirements"] if item["requirement_id"] == "req_thickness"
    )
    assert any(
        check["parameter_id"] == "lab_scope_review" and check["status"] == "gap"
        for check in line["comparison"]["checks"]
    )
    assert line["comparison"]["status"] == "partially_matched"

    lab = http.get("/api/lab").json()
    lab["description"] = "A newly saved lab revision requires a fresh review."
    assert http.put("/api/lab", json=lab).status_code == 200
    refreshed = http.get(f"/api/comparisons/{source.document_id}").json()
    updated = next(
        item for item in refreshed["requirements"] if item["requirement_id"] == "req_thickness"
    )
    assert not any(
        check["parameter_id"] == "lab_scope_review" for check in updated["comparison"]["checks"]
    )


def test_csp301_sample_import_is_reproducible_without_a_model_or_saved_run(client):
    http, root = client

    response = http.post("/api/specifications/demo?example=csp301")

    assert response.status_code == 200
    document_id = response.json()["document_id"]
    assert FileSpecStore(root).load_latest_extraction(document_id) is None
    document = http.get(f"/api/library/{document_id}").json()
    assert document["document"]["original_name"] == "CSP-EN-301_demo_spec.pdf"
    assert document["requirements"] == []
    lab = http.get("/api/lab").json()
    example = http.post("/api/lab/example", json={"version": lab["version"], "example": "csp301"})
    assert example.status_code == 200
    assert len(example.json()["capabilities"]) == 4


def test_worked_example_is_explicit_manual_reference_and_never_overwrites_a_saved_run(client):
    http, root = client

    loaded = http.post("/api/specifications/worked-example")

    assert loaded.status_code == 200
    document_id = loaded.json()["document_id"]
    specs = FileSpecStore(root)
    reference = specs.load_latest_extraction(document_id)
    assert reference is not None
    assert reference.provider == "manual_reference"
    assert reference.raw_response_kind == "manual_reference"
    assert len(reference.extraction.requirements) == 4
    assert "No model was called" in reference.raw_response
    assert len(http.get(f"/api/library/{document_id}").json()["requirements"]) == 4
    lab = http.get("/api/lab").json()
    assert (
        http.post(
            "/api/lab/example", json={"version": lab["version"], "example": "csp301"}
        ).status_code
        == 200
    )
    comparison = http.get(f"/api/comparisons/{document_id}")
    assert comparison.status_code == 200, comparison.text
    assert len(comparison.json()["requirements"]) == 4

    # A later saved provider fixture remains the latest run when someone chooses
    # the worked example again. No network/model execution is involved here.
    newer = reference.model_copy(
        update={
            "run_id": str(uuid4()),
            "provider": "offline_provider_fixture",
            "model": "test_fixture",
            "raw_response_kind": "provider_text",
            "raw_response": "A later saved provider response fixture",
        }
    )
    specs.save_extraction(document_id, newer)
    assert http.post("/api/specifications/worked-example").status_code == 200
    assert specs.load_latest_extraction(document_id) == newer


@pytest.mark.parametrize("changed", ["extraction", "lab", "source_review", "metadata"])
def test_comparison_rejects_concurrent_input_changes_without_publishing_mixed_snapshot(
    client, monkeypatch, changed
):
    """An analysis/edit completing during comparison must not mix source versions."""
    import scione.library.workspace as library_workspace

    http, root = client
    source, initial = _seed(root)
    newer = initial.model_copy(update={"run_id": str(uuid4())}, deep=True)
    specs = FileSpecStore(root)
    original_normalize = library_workspace.normalize_requirement
    changed_once = False

    def change_while_normalizing(requirement):
        nonlocal changed_once
        if not changed_once:
            changed_once = True
            if changed == "extraction":
                specs.save_extraction(source.document_id, newer)
            elif changed == "lab":
                labs = SQLLabStore(LibraryStore(root), load_reference_catalog(), legacy_root=root)
                lab = labs.load_or_create()
                lab.description = "A concurrent lab edit."
                labs.save(lab)
            elif changed == "source_review":
                review = specs.load_review(source.document_id, initial.run_id)
                specs.save_reviews(
                    source.document_id,
                    initial.run_id,
                    ["req_gloss"],
                    "needs_review",
                    "A concurrent source review",
                    expected_version=review.version,
                )
            else:
                archive = LibraryStore(root)
                record = archive.document(source.document_id)
                archive.edit_metadata(
                    source.document_id,
                    _metadata(record, title="Concurrently edited specification title"),
                    record["version"],
                )
                archive.close()
        return original_normalize(requirement)

    monkeypatch.setattr(library_workspace, "normalize_requirement", change_while_normalizing)
    rejected = http.get(f"/api/comparisons/{source.document_id}")
    assert changed_once
    assert rejected.status_code == 409, rejected.text
    archive = LibraryStore(root)
    with archive._db().connect() as connection:
        assert connection.scalar(select(func.count()).select_from(comparisons)) == 0
    archive.close()

    # Retrying once inputs are stable produces one source-consistent snapshot.
    monkeypatch.setattr(library_workspace, "normalize_requirement", original_normalize)
    retried = http.get(f"/api/comparisons/{source.document_id}")
    assert retried.status_code == 200, retried.text
    saved = retried.json()
    expected_run = newer.run_id if changed == "extraction" else initial.run_id
    assert saved["document"]["run_id"] == saved["source_run_id"] == expected_run
    assert http.get(f"/api/comparison-snapshots/{saved['comparison_id']}/json").json() == saved

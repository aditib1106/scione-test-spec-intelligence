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


def _machine_snapshot(live):
    return {key: value for key, value in live.items() if key != "export_snapshot_id"}


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


def test_unretained_article_scope_remains_visible_in_saved_comparison(client, monkeypatch):
    """A correct measured limit does not establish full document coverage."""
    http, root = client
    original_pdf = _pdf

    def source_with_article(revision="Issue A"):
        with pymupdf.open(stream=original_pdf(revision), filetype="pdf") as document:
            document[0].insert_text(
                (35, 720), "Covered supply: Mild-steel witness panels", fontsize=10
            )
            return document.tobytes(no_new_id=True)

    monkeypatch.setitem(_seed.__globals__, "_pdf", source_with_article)
    source, original_run = _seed(root)
    response = http.get(f"/api/comparisons/{source.document_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "Mild-steel witness panels" in " ".join(data["source_coverage_issues"])
    assert all(hint in data["notes"] for hint in data["source_coverage_issues"])
    assert data["summary"]["partially_matched"] == 1
    assert FileSpecStore(root).load_latest_extraction(source.document_id) == original_run
    saved = http.get(f"/api/comparison-snapshots/{data['comparison_id']}/json")
    assert saved.status_code == 200
    assert saved.json()["source_coverage_issues"] == data["source_coverage_issues"]


def test_new_coverage_policy_creates_a_new_immutable_snapshot_with_same_inputs(client, monkeypatch):
    """New software hints must not cause a 409 or rewrite a previous report."""
    import scione.library.workspace as library_workspace

    http, root = client
    source, original_run = _seed(root)
    before_response = http.get(f"/api/comparisons/{source.document_id}")
    assert before_response.status_code == 200
    before = before_response.json()
    monkeypatch.setattr(
        library_workspace,
        "coverage_issues",
        lambda *_args: ["Check source context: an additional policy cue needs review."],
    )
    after_response = http.get(f"/api/comparisons/{source.document_id}")
    assert after_response.status_code == 200, after_response.text
    after = after_response.json()
    assert before["source_run_id"] == after["source_run_id"] == original_run.run_id
    assert before["report_input_fingerprint"] == after["report_input_fingerprint"]
    assert before["comparison_id"] != after["comparison_id"]
    assert before["requirements"] == after["requirements"]
    assert http.get(f"/api/comparisons/{source.document_id}").json() == after
    assert http.get(f"/api/comparison-snapshots/{before['comparison_id']}/json").json() == (
        _machine_snapshot(before)
    )
    assert FileSpecStore(root).load_latest_extraction(source.document_id) == original_run


def test_pending_method_identity_does_not_report_lab_values_as_missing(client, monkeypatch):
    from scione.api.workspace import Workspace

    http, root = client
    source, _ = _seed(root)
    draft = Workspace.draft_from_inputs

    def ambiguous_method(workspace, inputs):
        report = draft(workspace, inputs).model_copy(deep=True)
        line = report.lines[0]
        # The lab has a gloss declaration, but no declaration can be selected
        # until this test's ambiguous method identity is resolved.
        assert any(cap.canonical_test_id == "gloss" for cap in report.lab.capabilities)
        line.status = "needs_review"
        line.capability = None
        line.unknown_methods = ["Method identity needs confirmation"]
        line.requirement.special_instructions.append("Use the buyer's special sample holder.")
        # A report line is a detached presentation copy. Keep the captured
        # source consistent with the simulated input; derived comparisons now
        # normalize that captured source with its physical PDF evidence.
        captured = next(
            row
            for row in report.source_run.extraction.requirements
            if row.id == line.requirement.id
        )
        captured.special_instructions.append("Use the buyer's special sample holder.")
        return report

    monkeypatch.setattr(Workspace, "draft_from_inputs", ambiguous_method)
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
    assert frozen.json() == _machine_snapshot(before)
    assert FileSpecStore(root).load_extraction(source.document_id, run.run_id) == run
    assert len(FileSpecStore(root).list_extractions(source.document_id)) == 1


def test_saved_comparison_exports_use_same_frozen_json_and_pdf(client, monkeypatch):
    http, root = client
    source, _ = _seed(root)
    saved = http.get(f"/api/comparisons/{source.document_id}").json()
    comparison_id = saved["export_snapshot_id"]
    endpoint = f"/api/comparison-snapshots/{comparison_id}"
    first = http.get(endpoint + "/pdf?download=true")
    assert first.status_code == 200
    assert first.headers["content-type"] == "application/pdf"
    assert "attachment" in first.headers["content-disposition"]
    assert first.content.startswith(b"%PDF-")
    info = http.get(endpoint)
    assert info.status_code == 200
    exported = info.json()["comparison"]
    assert exported["machine_comparison_id"] == saved["comparison_id"]
    assert exported["requirements"] == saved["requirements"]
    assert exported["summary"] == saved["summary"]
    assert 1 <= info.json()["page_count"] <= 6
    preview = http.get(endpoint + "/pages/1")
    assert preview.status_code == 200
    assert preview.content.startswith(b"\x89PNG")
    rendered_page = pymupdf.Pixmap(preview.content)
    assert 1000 <= rendered_page.width <= 2000
    assert 1000 <= rendered_page.height <= 2000
    assert http.get(endpoint + "/json").json() == exported

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
    from scione.api.workspace import Workspace

    http, root = client
    source, initial = _seed(root)
    newer = initial.model_copy(update={"run_id": str(uuid4())}, deep=True)
    specs = FileSpecStore(root)
    original_source = Workspace.comparison_source
    changed_once = False

    def change_while_normalizing(workspace, document_id, run, **normalization_options):
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
        return original_source(workspace, document_id, run, **normalization_options)

    monkeypatch.setattr(Workspace, "comparison_source", change_while_normalizing)
    rejected = http.get(f"/api/comparisons/{source.document_id}")
    assert changed_once
    assert rejected.status_code == 409, rejected.text
    archive = LibraryStore(root)
    with archive._db().connect() as connection:
        assert connection.scalar(select(func.count()).select_from(comparisons)) == 0
    archive.close()

    # Retrying once inputs are stable produces one source-consistent snapshot.
    monkeypatch.setattr(Workspace, "comparison_source", original_source)
    retried = http.get(f"/api/comparisons/{source.document_id}")
    assert retried.status_code == 200, retried.text
    saved = retried.json()
    expected_run = newer.run_id if changed == "extraction" else initial.run_id
    assert saved["document"]["run_id"] == saved["source_run_id"] == expected_run
    assert http.get(f"/api/comparison-snapshots/{saved['comparison_id']}/json").json() == (
        _machine_snapshot(saved)
    )


def test_remove_library_version_hides_selectors_and_preserves_history(client):
    http, root = client
    source, run = _seed(root)
    before = http.get(f"/api/library/{source.document_id}").json()
    report = http.get(f"/api/comparisons/{source.document_id}").json()
    original = FileSpecStore(root).read_pdf(source.document_id)
    removal = http.request(
        "DELETE",
        f"/api/library/{source.document_id}",
        json={"version": before["document"]["version"]},
    )
    assert removal.status_code == 200, removal.text
    assert removal.json()["archived"] is True
    assert http.get("/api/library").json()["documents"] == []
    assert http.get("/api/workspace").json()["documents"] == []
    assert http.get("/api/specifications").json() == []
    assert FileSpecStore(root).read_pdf(source.document_id) == original
    assert FileSpecStore(root).load_extraction(source.document_id, run.run_id) == run
    saved = http.get(f"/api/comparison-snapshots/{report['comparison_id']}/json")
    assert saved.status_code == 200
    assert saved.json() == _machine_snapshot(report)
    stale = http.request(
        "DELETE",
        f"/api/library/{source.document_id}",
        json={"version": before["document"]["version"]},
    )
    assert stale.status_code == 409
    reopened = LibraryStore(root)
    assert reopened.archived_document_ids() == {source.document_id}
    reopened.close()
    restored = http.post(
        f"/api/library/{source.document_id}/restore", json={"version": removal.json()["version"]}
    )
    assert restored.status_code == 200
    assert http.get("/api/library").json()["documents"][0]["document_id"] == source.document_id


def test_removing_one_version_keeps_the_other_version_and_reupload_restores(client):
    http, root = client
    first, _ = _seed(root, "Issue A")
    second, _ = _seed(root, "Issue B")
    first_meta = http.get(f"/api/library/{first.document_id}").json()["document"]
    http.get(f"/api/library/{second.document_id}")
    removed = http.request(
        "DELETE", f"/api/library/{first.document_id}", json={"version": first_meta["version"]}
    )
    assert removed.status_code == 200
    assert {r["document_id"] for r in http.get("/api/library").json()["documents"]} == {
        second.document_id
    }
    pdf = FileSpecStore(root).read_pdf(first.document_id)
    imported = http.post(
        "/api/specifications", files={"file": ("customer-Issue-A.pdf", pdf, "application/pdf")}
    )
    assert imported.status_code == 200
    assert imported.json()["document_id"] == first.document_id
    assert len(http.get("/api/specifications").json()) == 2


def _assessment_review_body(assessment, requirement_id, **changes):
    return {
        "comparison_id": assessment["comparison_id"],
        "input_fingerprint": assessment["report_input_fingerprint"],
        "requirement_id": requirement_id,
        "expected_version": 0,
        "review_status": "accepted",
        "decision": "keep",
        "note": "",
        **changes,
    }


def test_assessment_review_persists_without_overwriting_machine_checks(client):
    http, root = client
    source, run = _seed(root)
    path = f"/api/comparisons/{source.document_id}"
    original = http.get(path).json()
    target = original["requirements"][0]
    before_source = http.get(f"/api/specifications/{source.document_id}").json()["run"]
    body = _assessment_review_body(original, target["requirement_id"])
    accepted = http.post(path + "/reviews", json=body)
    assert accepted.status_code == 200, accepted.text
    updated = accepted.json()
    assert updated["comparison_id"] == original["comparison_id"]
    assert updated["requirements"] == original["requirements"]
    assert updated["summary"] == original["summary"]
    review = updated["assessment_reviews"][target["requirement_id"]]
    assert review["review_status"] == "accepted"
    assert review["machine_status"] == target["comparison"]["status"]
    assert review["version"] == 1
    assert http.get(path).json()["assessment_reviews"] == updated["assessment_reviews"]
    assert http.get(f"/api/specifications/{source.document_id}").json()["run"] == before_source
    # Stored machine JSON remains immutable, including after later reviewer decisions.
    frozen = http.get(f"/api/comparison-snapshots/{original['comparison_id']}/json").json()
    assert frozen == _machine_snapshot(original)
    followup = http.post(
        path + "/reviews",
        json={
            **body,
            "expected_version": 1,
            "decision": "outsource",
            "note": "Ask a partner about the missing post-cycle output; price not confirmed.",
        },
    )
    assert followup.status_code == 200, followup.text
    assert followup.json()["requirements"] == original["requirements"]
    history = http.get(path + "/reviews?download=true")
    assert history.status_code == 200
    assert "attachment" in history.headers["content-disposition"]
    assert [row["version"] for row in history.json()["events"]] == [1, 2]
    assert history.json()["events"][-1]["decision"] == "outsource"
    assert http.post(path + "/reviews", json=body).status_code == 409
    assert len(http.get(path + "/reviews").json()["events"]) == 2


def test_assessment_review_invalidates_on_changed_lab_and_rejects_stale_acceptance(client):
    http, root = client
    source, _run = _seed(root)
    path = f"/api/comparisons/{source.document_id}"
    original = http.get(path).json()
    body = _assessment_review_body(original, original["requirements"][0]["requirement_id"])
    assert http.post(path + "/reviews", json=body).status_code == 200
    lab = http.get("/api/lab").json()
    lab["description"] = "Updated lab declaration invalidates the old assessment review."
    saved = http.put("/api/lab", json=lab)
    assert saved.status_code == 200, saved.text
    current = http.get(path).json()
    assert current["comparison_id"] != original["comparison_id"]
    assert not current.get("assessment_reviews")
    assert http.post(path + "/reviews", json={**body, "expected_version": 1}).status_code == 409


def test_assessment_followup_requires_note_and_valid_owned_test(client):
    http, root = client
    source, _run = _seed(root)
    path = f"/api/comparisons/{source.document_id}"
    original = http.get(path).json()
    body = _assessment_review_body(original, original["requirements"][0]["requirement_id"])
    assert http.post(path + "/reviews", json={**body, "decision": "decline"}).status_code == 422
    assert (
        http.post(path + "/reviews", json={**body, "requirement_id": "other-test"}).status_code
        == 400
    )
    assert http.get(path + "/reviews").json()["events"] == []

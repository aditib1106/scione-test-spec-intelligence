"""Report-only removal and restoration, without source review or AI mutation."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_workspace_api import LocalHTTPClient, client, edit_body, seed_source

from scione.api.app import create_app
from scione.config import Settings
from scione.reports.store import FileReportStore
from scione.specifications.store import FileSpecStore

__all__ = ["client"]


def remove_body(response, **changes):
    return edit_body(
        response,
        requirement_ids=["req_pencil"],
        excluded=True,
        note="Customer removed this item from the requested quotation.",
        **changes,
    )


def test_remove_and_restore_persist_audit_without_changing_source_or_raw_model(client):
    http, root = client
    stored, run, _ = seed_source(root)
    specs = FileSpecStore(root)
    original_run = specs.load_latest_extraction(stored.document_id).model_dump(mode="json")
    original_review = specs.load_review(stored.document_id, run.run_id).model_dump(mode="json")
    before = http.get(f"/api/reports/{stored.document_id}")
    original_status = before.json()["report"]["lines"][0]["status"]
    removed = http.post(
        f"/api/reports/{stored.document_id}/exclusions",
        json=remove_body(before),
    )
    assert removed.status_code == 200, removed.text
    current = http.get(f"/api/reports/{stored.document_id}").json()["report"]
    line = current["lines"][0]
    assert line["status"] == "excluded"
    assert line["quote"] is None
    assert (
        line["extraction_review_state"]
        == before.json()["report"]["lines"][0]["extraction_review_state"]
    )
    action = current["workspace"]["scope_actions"][0]
    assert action["excluded"] is True
    assert action["previous_excluded"] is False
    assert action["previous_status"] == original_status
    assert action["actor"] == "local_reviewer"
    assert action["occurred_at"]
    assert action["note"] == "Customer removed this item from the requested quotation."
    assert line["report_scope_exclusion"] == action
    saved = FileReportStore(root).load_workspace(stored.document_id, run.run_id)
    assert saved.active_exclusions["req_pencil"].action_id == action["action_id"]
    assert specs.load_latest_extraction(stored.document_id).model_dump(mode="json") == original_run
    assert (
        specs.load_review(stored.document_id, run.run_id).model_dump(mode="json") == original_review
    )
    assert current["source_run"] == original_run
    restored = http.post(
        f"/api/reports/{stored.document_id}/exclusions",
        json=edit_body({"report": current}, requirement_ids=["req_pencil"], excluded=False),
    )
    assert restored.status_code == 200, restored.text
    current = http.get(f"/api/reports/{stored.document_id}").json()["report"]
    assert current["lines"][0]["status"] == original_status
    assert current["lines"][0]["report_scope_exclusion"] is None
    history = current["workspace"]["scope_actions"]
    assert len(history) == 2
    assert history[1]["previous_status"] == "excluded"
    assert history[1]["previous_excluded"] is True
    assert history[1]["excluded"] is False
    assert history[1]["action_id"] != history[0]["action_id"]
    assert (
        FileReportStore(root).load_workspace(stored.document_id, run.run_id).active_exclusions == {}
    )


def test_stale_edit_rejected_and_repeated_current_state_is_a_noop(client):
    http, root = client
    stored, _, _ = seed_source(root)
    before = http.get(f"/api/reports/{stored.document_id}")
    body = remove_body(before)
    assert http.post(f"/api/reports/{stored.document_id}/exclusions", json=body).status_code == 200
    stale = http.post(f"/api/reports/{stored.document_id}/exclusions", json=body)
    assert stale.status_code == 409
    current = http.get(f"/api/reports/{stored.document_id}")
    current_version = current.json()["report"]["workspace"]["version"]
    same = http.post(
        f"/api/reports/{stored.document_id}/exclusions",
        json=remove_body(current),
    )
    assert same.status_code == 200
    workspace = http.get(f"/api/reports/{stored.document_id}").json()["report"]["workspace"]
    assert workspace["version"] == current_version
    assert len(workspace["scope_actions"]) == 1


@pytest.mark.parametrize("ids", [["req_pencil", "req_pencil"], ["req_pencil", "req_foreign"]])
def test_invalid_batch_is_rejected_without_partial_changes(client, ids):
    http, root = client
    stored, run, _ = seed_source(root)
    before = http.get(f"/api/reports/{stored.document_id}")
    response = http.post(
        f"/api/reports/{stored.document_id}/exclusions",
        json=edit_body(before, requirement_ids=ids, excluded=True),
    )
    assert response.status_code == 400
    workspace = FileReportStore(root).load_workspace(stored.document_id, run.run_id)
    assert workspace.scope_actions == []
    assert workspace.version == 0


def test_report_scope_exclusions_do_not_inherit_into_a_new_extraction_run(client):
    http, root = client
    stored, old, _ = seed_source(root)
    before = http.get(f"/api/reports/{stored.document_id}")
    assert (
        http.post(
            f"/api/reports/{stored.document_id}/exclusions",
            json=remove_body(before),
        ).status_code
        == 200
    )
    new = old.model_copy(
        deep=True, update={"run_id": str(uuid4()), "started_at": datetime.now(UTC)}
    )
    FileSpecStore(root).save_extraction(stored.document_id, new)
    assert new.run_id != old.run_id
    current = http.get(f"/api/reports/{stored.document_id}").json()["report"]
    assert current["workspace"]["scope_actions"] == []
    assert current["lines"][0]["status"] != "excluded"
    assert FileReportStore(root).load_workspace(stored.document_id, old.run_id).active_exclusions
    assert (
        http.post(
            f"/api/reports/{stored.document_id}/exclusions",
            json=remove_body(before),
        ).status_code
        == 409
    )


def test_report_json_and_pdf_snapshot_use_the_same_excluded_scope(client):
    import pymupdf

    http, root = client
    stored, _, _ = seed_source(root)
    before = http.get(f"/api/reports/{stored.document_id}")
    assert (
        http.post(
            f"/api/reports/{stored.document_id}/exclusions",
            json=remove_body(before),
        ).status_code
        == 200
    )
    current = http.get(f"/api/reports/{stored.document_id}").json()["report"]
    generated = http.post(
        f"/api/reports/{stored.document_id}/snapshots",
        json={
            "run_id": current["source_run"]["run_id"],
            "input_fingerprint": current["input_fingerprint"],
        },
    )
    assert generated.status_code == 200, generated.text
    identifier = generated.json()["report_id"]
    saved = http.get(f"/api/snapshots/{identifier}/json").json()
    assert saved["lines"][0]["status"] == "excluded"
    assert saved["lines"][0]["quote"] is None
    assert saved["workspace"]["scope_actions"] == current["workspace"]["scope_actions"]
    pdf = http.get(f"/api/snapshots/{identifier}/pdf").content
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        text = " ".join(page.get_text() for page in document)
    assert "Excluded from scope" in text
    assert "No active test requirements" in text
    assert "Pencil hardness" in text


def test_scope_decisions_survive_app_recreation(client, tmp_path):
    http, root = client
    stored, _, _ = seed_source(root)
    before = http.get(f"/api/reports/{stored.document_id}")
    assert (
        http.post(
            f"/api/reports/{stored.document_id}/exclusions",
            json=remove_body(before),
        ).status_code
        == 200
    )
    fresh = LocalHTTPClient(
        create_app(
            Settings(
                _env_file=None, scione_workspace_dir=root, groq_api_key=None, gemini_api_key=None
            ),
            frontend_dir=tmp_path / "no-frontend",
        )
    )
    report = fresh.get(f"/api/reports/{stored.document_id}").json()["report"]
    assert report["lines"][0]["status"] == "excluded"
    assert len(report["workspace"]["scope_actions"]) == 1


def test_actor_cannot_be_supplied_by_frontend(client):
    http, root = client
    stored, _, _ = seed_source(root)
    before = http.get(f"/api/reports/{stored.document_id}")
    response = http.post(
        f"/api/reports/{stored.document_id}/exclusions",
        json={**remove_body(before), "actor": "admin_from_other_lab"},
    )
    assert response.status_code == 422

"""Document-note checks persist independently of scientific human review."""

from uuid import uuid4

from test_authenticated_inference import (
    SOURCE,
    authenticated_api,
    login_and_upload,
    untrusted_analysis,
)
from test_workspace_api import make_pdf

__all__ = ["authenticated_api"]


def prepare_notes(client):
    _first, headers, _user = login_and_upload(client)
    uploaded = client.post(
        "/api/specifications", headers=headers,
        files={"file": (
            "covered-supply.pdf",
            make_pdf("Covered supply: Extruded EPDM rings and companion rubber sheets\n" + SOURCE),
            "application/pdf",
        )},
    )
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["document_id"]
    base = f"/api/specifications/{document_id}"
    analyzed = client.post(base + "/analysis", headers=headers, json=untrusted_analysis())
    assert analyzed.status_code == 200, analyzed.text
    source = analyzed.json()
    assert source["document_notes_review"]["pending_count"] >= 1
    return base, document_id, headers, source


def test_note_checked_does_not_change_source_confirmation_or_matching_inputs(authenticated_api):
    client, workspace, captured = authenticated_api
    base, document_id, headers, source = prepare_notes(client)
    run_id = source["run"]["run_id"]
    confirmed = client.post(base + "/review", headers=headers, json={
        "run_id": run_id, "version": source["review"]["version"],
        "requirement_ids": ["req_gloss"], "decision": "confirmed", "acknowledged": True,
        "facts_version": source["facts_version"],
    })
    assert confirmed.status_code == 200, confirmed.text
    before_source = client.get(base).json()
    assert before_source["assessments"]["req_gloss"]["state"] == "confirmed"
    before_report = client.get(f"/api/comparisons/{document_id}").json()
    before_matching = client.get(base + "/matching").json()
    original = workspace.specs.load_extraction(document_id, run_id)
    calls = len(captured["factory"])
    view = before_source["document_notes_review"]
    response = client.post(base + "/document-notes/review", headers=headers, json={
        "source_fingerprint": view["source_fingerprint"], "expected_version": view["version"],
        "note_ids": [note["id"] for note in view["notes"]], "reviewed": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["pending_count"] == 0
    after_source = client.get(base).json()
    after_report = client.get(f"/api/comparisons/{document_id}").json()
    assert after_source["document_notes_review"]["pending_count"] == 0
    assert after_report["document_notes_review"] == after_source["document_notes_review"]
    for key in ("review", "assessments", "normalized_requirements", "coverage_issues", "run"):
        assert after_source[key] == before_source[key]
    for key in ("comparison_id", "report_input_fingerprint", "matching_run_id", "summary",
                "requirements", "export_snapshot_id"):
        assert after_report[key] == before_report[key]
    assert client.get(base + "/matching").json() == before_matching
    assert len(captured["factory"]) == calls
    assert workspace.specs.load_extraction(document_id, run_id) == original


def test_stale_note_write_and_missing_csrf_do_not_clear_current_notes(authenticated_api):
    client, workspace, _captured = authenticated_api
    base, document_id, headers, source = prepare_notes(client)
    view = source["document_notes_review"]
    body = {"source_fingerprint": view["source_fingerprint"],
            "expected_version": view["version"],
            "note_ids": [note["id"] for note in view["notes"]], "reviewed": True}
    assert client.post(base + "/document-notes/review", json=body).status_code == 403
    old = workspace.specs.load_latest_extraction(document_id)
    workspace.specs.save_extraction(
        document_id, old.model_copy(deep=True, update={"run_id": str(uuid4())})
    )
    rejected = client.post(base + "/document-notes/review", headers=headers, json=body)
    assert rejected.status_code == 409
    after = client.get(base).json()["document_notes_review"]
    assert after["source_fingerprint"] != body["source_fingerprint"]
    assert after["pending_count"] == len(after["notes"])
    assert after["version"] == 0

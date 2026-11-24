"""Manual customer follow-up is scoped, append-only and never a machine verdict."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from unicodedata import normalize
from uuid import uuid4

import pymupdf
import pytest
from sqlalchemy import select
from test_library_api import _machine_snapshot, _seed
from test_library_api import client as client

from scione.api.contracts import CustomerClarification
from scione.library.clarifications import SQLCustomerClarifications, clarifications
from scione.library.store import LibraryConflict, LibraryStore
from scione.specifications.store import FileSpecStore


def _body(assessment, requirement_id="req_gloss", **changes):
    return {
        "comparison_id": assessment["comparison_id"],
        "input_fingerprint": assessment["report_input_fingerprint"],
        "requirement_id": requirement_id,
        "expected_version": 0,
        "client_request_id": str(uuid4()),
        "event_kind": "question",
        "text": "Must the lab report retention separately at every marked location?",
        "contact": "Customer engineering",
        **changes,
    }


def _query(assessment, requirement_id="req_gloss"):
    return {
        "comparison_id": assessment["comparison_id"],
        "input_fingerprint": assessment["report_input_fingerprint"],
        "requirement_id": requirement_id,
    }


def test_question_reply_exports_are_frozen_without_changing_machine_or_review(client):
    http, root = client
    source, run = _seed(root)
    path = f"/api/comparisons/{source.document_id}"
    original = http.get(path).json()
    before_source = http.get(f"/api/specifications/{source.document_id}").json()
    empty = http.get(path + "/clarifications", params=_query(original))
    assert empty.status_code == 200, empty.text
    assert empty.json()["events"] == [] and empty.json()["version"] == 0
    body = _body(original)
    posted = http.post(path + "/clarifications", json=body)
    assert posted.status_code == 200, posted.text
    question = posted.json()["events"][0]
    assert question["question_id"] == question["event_id"]
    assert question["event_kind"] == "question"
    assert question["actor"] == "local_reviewer"
    assert question["actor_label"] == "Local reviewer"
    assert question["source_run_id"] == run.run_id
    assert question["document_id"] == source.document_id
    assert datetime.fromisoformat(question["created_at"]).tzinfo is not None
    question_snapshot = posted.json()["comparison"]["export_snapshot_id"]
    reply_body = _body(
        original,
        expected_version=1,
        event_kind="customer_reply",
        question_id=question["event_id"],
        text="Yes. Keep <site A> & <site B> readings separate.",
        contact="Alex, customer team",
    )
    replied = http.post(path + "/clarifications", json=reply_body)
    assert replied.status_code == 200, replied.text
    result = replied.json()
    assert result["version"] == 2
    assert [event["event_kind"] for event in result["events"]] == ["question", "customer_reply"]
    assert result["events"][0] == question
    assert result["events"][1]["question_id"] == question["event_id"]
    updated = result["comparison"]
    assert updated["comparison_id"] == original["comparison_id"]
    assert updated["requirements"] == original["requirements"]
    assert updated["summary"] == original["summary"]
    assert updated.get("assessment_reviews") == original.get("assessment_reviews")
    assert http.get(f"/api/specifications/{source.document_id}").json() == before_source
    machine = http.get(f"/api/comparison-snapshots/{original['comparison_id']}/json").json()
    assert machine == _machine_snapshot(original)
    assert "customer_clarifications" not in machine
    assert updated["export_snapshot_id"] != question_snapshot != original["export_snapshot_id"]
    snapshots = [original["export_snapshot_id"], question_snapshot, updated["export_snapshot_id"]]
    for snapshot_id, count in zip(snapshots, (0, 1, 2), strict=True):
        frozen = http.get(f"/api/comparison-snapshots/{snapshot_id}/json").json()
        assert frozen["machine_comparison_id"] == original["comparison_id"]
        assert frozen["requirements"] == original["requirements"]
        events = frozen["customer_clarifications"].get("req_gloss", {}).get("events", [])
        assert len(events) == count
    pdf = http.get(f"/api/comparison-snapshots/{updated['export_snapshot_id']}/pdf")
    assert pdf.status_code == 200, pdf.text[:200] if pdf.status_code != 200 else ""
    with pymupdf.open(stream=pdf.content, filetype="pdf") as document:
        text = normalize("NFKC", " ".join(page.get_text() for page in document))
    for expected in (
        "Customer clarifications",
        "Question 1",
        "Customer reply 1",
        "<site A>",
        "<site B>",
        "Local reviewer",
        "Alex, customer team",
    ):
        assert expected in text
    assert "local_reviewer" not in text


def test_clarification_requests_are_idempotent_and_validate_question_scope(client):
    http, root = client
    source, _run = _seed(root)
    path = f"/api/comparisons/{source.document_id}"
    assessment = http.get(path).json()
    body = _body(assessment)
    first = http.post(path + "/clarifications", json=body)
    assert first.status_code == 200, first.text
    retry = http.post(path + "/clarifications", json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["events"] == first.json()["events"]
    assert retry.json()["version"] == 1
    assert (
        http.post(path + "/clarifications", json={**body, "text": "Changed question"}).status_code
        == 409
    )
    assert http.post(path + "/clarifications", json=_body(assessment)).status_code == 409
    assert (
        http.post(
            path + "/clarifications", json=_body(assessment, requirement_id="req_other")
        ).status_code
        == 400
    )
    assert (
        http.post(path + "/clarifications", json=_body(assessment, text="   ")).status_code == 422
    )
    assert (
        http.post(path + "/clarifications", json=_body(assessment, text="x" * 4001)).status_code
        == 422
    )
    assert (
        http.post(
            path + "/clarifications", json=_body(assessment, event_kind="customer_reply")
        ).status_code
        == 422
    )
    assert (
        http.post(
            path + "/clarifications", json=_body(assessment, question_id=str(uuid4()))
        ).status_code
        == 422
    )
    question_id = first.json()["events"][0]["event_id"]
    foreign_reply = _body(
        assessment,
        requirement_id="req_thickness",
        event_kind="customer_reply",
        question_id=question_id,
        text="Reply to another test's question.",
    )
    assert http.post(path + "/clarifications", json=foreign_reply).status_code == 409
    missing_reply = _body(
        assessment,
        expected_version=1,
        event_kind="customer_reply",
        question_id=str(uuid4()),
        text="This question does not exist.",
    )
    assert http.post(path + "/clarifications", json=missing_reply).status_code == 409
    history = http.get(path + "/clarifications", params=_query(assessment)).json()
    assert history["version"] == 1 and len(history["events"]) == 1
    reply = http.post(
        path + "/clarifications",
        json=_body(
            assessment,
            expected_version=1,
            event_kind="customer_reply",
            question_id=question_id,
            text="The separate site readings are required.",
        ),
    )
    assert reply.status_code == 200, reply.text
    # Retrying the original already-saved request remains safe after another entry.
    repeated = http.post(path + "/clarifications", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["version"] == 2
    assert len(repeated.json()["events"]) == 2


def test_thread_survives_recomparison_and_scope_change_but_not_reextraction(client):
    http, root = client
    source, run = _seed(root)
    path = f"/api/comparisons/{source.document_id}"
    original = http.get(path).json()
    question = http.post(path + "/clarifications", json=_body(original)).json()["events"][0]
    lab = http.get("/api/lab").json()
    lab["description"] = "Updated saved capability creates another assessment."
    assert http.put("/api/lab", json=lab).status_code == 200
    current = http.get(path).json()
    assert current["comparison_id"] != original["comparison_id"]
    assert http.get(path + "/clarifications", params=_query(original)).status_code == 409
    assert (
        http.post(path + "/clarifications", json=_body(original, expected_version=1)).status_code
        == 409
    )
    same_thread = http.get(path + "/clarifications", params=_query(current))
    assert same_thread.status_code == 200, same_thread.text
    assert same_thread.json()["events"] == [question]
    assert same_thread.json()["events"][0]["comparison_id"] == original["comparison_id"]
    reply = http.post(
        path + "/clarifications",
        json=_body(
            current,
            expected_version=1,
            event_kind="customer_reply",
            question_id=question["event_id"],
            text="Please retain all five individual sites.",
        ),
    )
    assert reply.status_code == 200, reply.text
    after_reply = reply.json()["comparison"]
    excluded = http.post(
        f"/api/reports/{source.document_id}/exclusions",
        json={
            "run_id": run.run_id,
            "input_fingerprint": after_reply["report_input_fingerprint"],
            "version": after_reply["report_workspace_version"],
            "requirement_ids": ["req_gloss"],
            "excluded": True,
            "note": "Temporarily outside this quotation; keep the manual discussion.",
        },
    )
    assert excluded.status_code == 200, excluded.text
    scope_report = excluded.json()
    scope_history = http.get(path + "/clarifications", params=_query(scope_report))
    assert scope_history.status_code == 200, scope_history.text
    assert scope_history.json()["version"] == 2
    # Even identical-looking IDs in a fresh extraction cannot inherit an old reply.
    replacement = run.model_copy(update={"run_id": str(uuid4()), "started_at": datetime.now(UTC)})
    FileSpecStore(root).save_extraction(source.document_id, replacement)
    refreshed = http.get(path)
    assert refreshed.status_code == 200, refreshed.text
    new_report = refreshed.json()
    assert new_report["source_run_id"] == replacement.run_id
    new_thread = http.get(path + "/clarifications", params=_query(new_report)).json()
    assert new_thread["version"] == 0 and new_thread["events"] == []
    store = LibraryStore(root)
    with store.engine.connect() as connection:
        retained = list(
            connection.execute(
                select(clarifications.c.payload).where(
                    clarifications.c.document_id == source.document_id,
                    clarifications.c.source_run_id == run.run_id,
                    clarifications.c.requirement_id == "req_gloss",
                )
            ).scalars()
        )
    assert len(retained) == 2
    store.close()


def test_sql_threads_isolate_tenant_document_source_and_request_ids(client):
    http, root = client
    source, _run = _seed(root)
    assessment = http.get(f"/api/comparisons/{source.document_id}").json()
    store = LibraryStore(root)
    a, b = (
        SQLCustomerClarifications(store, "tenant-a"),
        SQLCustomerClarifications(store, "tenant-b"),
    )
    requirement = next(
        row for row in assessment["requirements"] if row["requirement_id"] == "req_gloss"
    )
    body = CustomerClarification.model_validate(_body(assessment))
    question = a.save(assessment, requirement, body)
    assert b.history(source.document_id, assessment["source_run_id"], "req_gloss") == []
    # A distinct tenant can use its own same UUID without sharing the earlier event.
    own_b = b.save(assessment, requirement, body)
    assert own_b["event_id"] != question["event_id"]
    assert len(a.history(source.document_id, assessment["source_run_id"], "req_gloss")) == 1
    assert len(b.history(source.document_id, assessment["source_run_id"], "req_gloss")) == 1
    reply = CustomerClarification.model_validate(
        _body(
            assessment,
            expected_version=1,
            event_kind="customer_reply",
            question_id=question["event_id"],
            text="A reply must stay with its own question.",
        )
    )
    with pytest.raises(LibraryConflict, match="Choose a question"):
        b.save(assessment, requirement, reply)
    for field in ("document_id", "source_run_id"):
        foreign = deepcopy(assessment)
        if field == "document_id":
            foreign["document"][field] = "other-document"
        else:
            foreign[field] = str(uuid4())
        wrong_context = reply.model_copy(update={"expected_version": 0})
        with pytest.raises(LibraryConflict, match="Choose a question"):
            a.save(foreign, requirement, wrong_context)
    store.close()

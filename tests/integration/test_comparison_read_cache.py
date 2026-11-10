"""Reuse pure report derivations while preserving live edits and immutable exports."""

from copy import deepcopy

import pymupdf
import pytest
from fastapi import HTTPException
from test_library_api import _seed

from scione.api.contracts import AssessmentReview
from scione.api.workspace import Workspace
from scione.config import Settings
from scione.structured.pdf import render_comparison_pdf


@pytest.fixture
def cached_report(tmp_path, monkeypatch):
    workspace = Workspace(Settings(
        _env_file=None, scione_workspace_dir=tmp_path,
        scione_database_url=None, scione_auth_database_url=None,
    ))
    source, run = _seed(tmp_path)
    counts = {"draft": 0, "compile": 0}
    draft, compiled = workspace.draft_from_inputs, workspace.matching.compiled

    def measured_draft(inputs):
        counts["draft"] += 1
        return draft(inputs)

    def measured_compile(report):
        counts["compile"] += 1
        return compiled(report)

    monkeypatch.setattr(workspace, "draft_from_inputs", measured_draft)
    monkeypatch.setattr(workspace.matching, "compiled", measured_compile)
    yield workspace, source, run, counts
    workspace.library.store.close()


def review_body(report, **changes):
    return AssessmentReview(
        comparison_id=report["comparison_id"],
        input_fingerprint=report["report_input_fingerprint"],
        requirement_id=report["requirements"][0]["requirement_id"],
        expected_version=changes.pop("expected_version", 0),
        review_status="accepted", decision=changes.pop("decision", "clarify_customer"),
        note=changes.pop("note", "Please clarify the post-cycle reporting requirement."),
        **changes,
    )


def test_repeated_report_and_review_save_do_not_rederive_or_recompile(cached_report):
    workspace, source, _run, counts = cached_report
    first = workspace.library.comparison(source.document_id)
    expected = deepcopy(first["requirements"])
    first["requirements"].clear()  # A caller cannot mutate the cached derivation.
    second = workspace.library.comparison(source.document_id)
    assert second["requirements"] == expected
    reviewed = workspace.library.review_assessment(source.document_id, review_body(second))
    assert reviewed["requirements"] == expected
    assert reviewed["comparison_id"] == second["comparison_id"]
    assert reviewed["export_snapshot_id"] != second["export_snapshot_id"]
    assert counts == {"draft": 1, "compile": 1}
    with pytest.raises(Exception, match="review changed"):
        workspace.library.review_assessment(source.document_id, review_body(second))


def test_live_lab_and_source_review_invalidate_cached_report(cached_report):
    workspace, source, run, counts = cached_report
    first = workspace.library.comparison(source.document_id)
    lab = workspace.labs.load_or_create()
    lab.description = "A newly edited capability declaration."
    workspace.labs.save(lab)
    changed = workspace.library.comparison(source.document_id)
    assert changed["comparison_id"] != first["comparison_id"]
    workspace.specs.save_reviews(
        source.document_id, run.run_id, ["req_gloss"], "needs_review",
        "Check this quoted source.",
        expected_version=workspace.specs.load_review(source.document_id, run.run_id).version,
    )
    reviewed = workspace.library.comparison(source.document_id)
    assert reviewed["comparison_id"] != changed["comparison_id"]
    assert counts == {"draft": 3, "compile": 3}


def test_warm_cache_rechecks_inputs_before_publishing(cached_report, monkeypatch):
    workspace, source, _run, _counts = cached_report
    workspace.library.comparison(source.document_id)
    capture = workspace.capture_draft_inputs
    called = False

    def concurrent_edit(document_id):
        nonlocal called
        inputs = capture(document_id)
        if not called:
            called = True
            lab = workspace.labs.load_or_create()
            lab.description = "Edited after the captured cache key."
            workspace.labs.save(lab)
        return inputs

    monkeypatch.setattr(workspace, "capture_draft_inputs", concurrent_edit)
    with pytest.raises(HTTPException) as caught:
        workspace.library.comparison(source.document_id)
    assert caught.value.status_code == 409


def test_metadata_date_preserves_decision_and_exports_new_frozen_snapshot(cached_report):
    workspace, source, _run, _counts = cached_report
    original = workspace.library.comparison(source.document_id)
    reviewed = workspace.library.review_assessment(source.document_id, review_body(original))
    old_export = workspace.library.store.comparison(reviewed["export_snapshot_id"])
    old_pdf = workspace.library.store.comparison_pdf(
        reviewed["export_snapshot_id"], render_comparison_pdf
    )
    record = workspace.library.store.document(source.document_id)
    workspace.library.store.edit_metadata(source.document_id, {
        "company_name": record["company_name"], "title": record["title"],
        "specification_id": record["specification_id"], "revision": record["revision"],
        "specification_date": "2026-10-20",
    }, record["version"])
    dated = workspace.library.comparison(source.document_id)
    identifier = dated["requirements"][0]["requirement_id"]
    assert dated["report_input_fingerprint"] == reviewed["report_input_fingerprint"]
    assert dated["comparison_id"] != reviewed["comparison_id"]
    assert dated["assessment_reviews"][identifier]["decision"] == "clarify_customer"
    assert dated["assessment_reviews"][identifier]["carried_from_comparison_id"] == (
        reviewed["comparison_id"]
    )
    exported = workspace.library.store.comparison(dated["export_snapshot_id"])
    assert exported["document"]["specification_date"] == "2026-10-20"
    assert exported["assessment_reviews"][identifier]["decision"] == "clarify_customer"
    new_pdf = workspace.library.store.comparison_pdf(
        dated["export_snapshot_id"], render_comparison_pdf
    )
    with pymupdf.open(stream=new_pdf, filetype="pdf") as document:
        text = "\n".join(page.get_text() for page in document)
    assert "clarify" in text.casefold() and "post-cycle reporting requirement" in text
    assert workspace.library.store.comparison(reviewed["export_snapshot_id"]) == old_export
    assert workspace.library.store.comparison_pdf(
        reviewed["export_snapshot_id"], render_comparison_pdf
    ) == old_pdf
    changed = workspace.library.review_assessment(
        source.document_id, review_body(dated, expected_version=1, decision="outsource",
                                        note="Ask a partner laboratory about this requirement.")
    )
    assert changed["assessment_reviews"][identifier]["version"] == 2
    assert changed["export_snapshot_id"] != dated["export_snapshot_id"]

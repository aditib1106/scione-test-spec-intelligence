"""Document-note checks persist separately from source facts and test decisions."""

from copy import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from sqlalchemy import select

from scione.api.workspace import Workspace
from scione.config import Settings
from scione.library.store import LibraryConflict
from scione.specifications.document_notes import (
    _document_information_note,
    document_notes_view,
    note_reviews,
    save_document_notes_review,
)
from scione.specifications.models import CustomerExtractionRun
from scione.structured import normalize_requirement

SOURCE = (
    "Covered supply: Extruded EPDM door-gasket rings and companion rubber sheets\n"
    "Compression set ASTM D1025-19: test at 100 degrees C for 22 hours.\n"
    "Record each of the three readings.\n"
    "Release requires written approval from Team Q.\n"
    "Background reference: ISO 15184."
)


@pytest.fixture
def sample(tmp_path):
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path,
        scione_database_url=None, scione_auth_database_url=None,
    )
    workspace = Workspace(settings)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((36, 50), SOURCE, fontsize=10)
        data = pdf.tobytes()
    stored = workspace.specs.import_pdf("source.pdf", data)
    run = CustomerExtractionRun.model_validate({
        "run_id": str(uuid4()), "document_id": stored.document_id,
        "started_at": datetime.now(UTC), "schema_version": "0.1",
        "prompt_version": "offline_fixture", "provider": "static", "model": "fixture",
        "latency_ms": 1, "usage": {}, "raw_response": "Original fixture response.",
        "extraction": {
            "schema_version": "0.1", "document": {"title": "Fixture specification"},
            "requirements": [{
                "id": "req_a", "original_test_name": "Compression set",
                "requested_methods": [{"code": "ASTM D1025-19"}],
                "conditions": [{"name": "Material", "raw_value": "Flat EPDM coupons"}],
                "evidence": [{"page": 1, "quote": "Record each of the three readings."}],
            }],
        },
    })
    workspace.specs.save_extraction(stored.document_id, run)
    yield workspace, stored.document_id, run, settings
    workspace.library.store.close()


def check(workspace, document_id, view=None, **changes):
    view = view or document_notes_view(workspace, document_id)
    return save_document_notes_review(
        workspace, document_id,
        source_fingerprint=changes.pop("source_fingerprint", view["source_fingerprint"]),
        expected_version=changes.pop("expected_version", view["version"]),
        note_ids=changes.pop("note_ids", [note["id"] for note in view["notes"]]),
        reviewed=changes.pop("reviewed", True), actor="offline_reviewer", **changes,
    )


def test_readable_physical_notes_do_not_claim_omission_or_approve_any_test(sample):
    workspace, doc, run, _settings = sample
    original = workspace.specs.load_extraction(doc, run.run_id).model_dump(mode="json")
    reviews = workspace.specs.load_review(doc, run.run_id).model_dump(mode="json")
    normalized = normalize_requirement(run.extraction.requirements[0]).model_dump(mode="json")
    view = document_notes_view(workspace, doc)
    context = next(note for note in view["notes"] if note["title"] == "Covered supply")
    assert context["kind"] == "context" and context["page"] == 1
    assert context["quote"] == "Extruded EPDM door-gasket rings and companion rubber sheets"
    assert "different wording" in context["detail"]
    references = next(note for note in view["notes"] if note["kind"] == "reference")
    assert "ISO 15184" in references["detail"]
    assert "requested_methods" not in references["detail"]
    assert view["pending_count"] == len(view["notes"]) >= 3
    saved = check(workspace, doc, view)
    assert saved["pending_count"] == 0
    assert workspace.specs.load_extraction(doc, run.run_id).model_dump(mode="json") == original
    assert workspace.specs.load_review(doc, run.run_id).model_dump(mode="json") == reviews
    after = normalize_requirement(run.extraction.requirements[0]).model_dump(mode="json")
    assert after == normalized


def test_checks_persist_across_workspace_reload_and_can_be_unchecked(sample):
    workspace, doc, _run, settings = sample
    saved = check(workspace, doc)
    workspace.library.store.close()
    reopened = Workspace(settings)
    try:
        assert document_notes_view(reopened, doc) == saved
        identifier = saved["notes"][0]["id"]
        undone = check(reopened, doc, saved, note_ids=[identifier], reviewed=False)
        assert undone["version"] == 2 and undone["pending_count"] == 1
        selected = next(note for note in undone["notes"] if note["id"] == identifier)
        assert selected["reviewed"] is False
        with reopened.library.store.engine.connect() as connection:
            events = connection.execute(select(note_reviews.c.payload).order_by(
                note_reviews.c.version
            )).scalars().all()
        assert len(events) == 2 and events[0]["reviewed"] and not events[1]["reviewed"]
        assert events[0]["actor"] == "offline_reviewer"
        snapshot = next(note for note in events[0]["notes"] if note["title"] == "Covered supply")
        assert snapshot["page"] == 1 and "EPDM door-gasket" in snapshot["quote"]
        assert snapshot["kind"] == "context" and snapshot["id"]
    finally:
        reopened.library.store.close()


def test_stale_versions_source_fingerprints_and_unknown_or_empty_notes_are_rejected(sample):
    workspace, doc, _run, _settings = sample
    view = document_notes_view(workspace, doc)
    check(workspace, doc, view)
    for arguments in [
        {"expected_version": 0}, {"source_fingerprint": "0" * 64},
        {"note_ids": ["not-a-current-note"]}, {"note_ids": []},
    ]:
        with pytest.raises(LibraryConflict):
            check(workspace, doc, **arguments)
    assert document_notes_view(workspace, doc)["version"] == 1


def test_row_confirm_lab_and_display_metadata_do_not_reset_note_acknowledgement(sample):
    workspace, doc, run, _settings = sample
    saved = check(workspace, doc)
    workspace.specs.save_reviews(
        doc, run.run_id, ["req_a"], "confirmed", expected_version=0,
    )
    lab = workspace.labs.load_or_create()
    workspace.labs.save(lab.model_copy(update={"description": "Changed lab description"}))
    workspace.library.index(doc)
    row = workspace.library.store.document(doc)
    workspace.library.store.edit_metadata(
        doc, {"company_name": "A different display company", "title": "New display title"},
        row["version"],
    )
    assert document_notes_view(workspace, doc) == saved


def test_new_extraction_and_reviewed_fact_changes_require_new_checks(sample):
    workspace, doc, run, _settings = sample
    original_view = document_notes_view(workspace, doc)
    check(workspace, doc, original_view)
    newer = run.model_copy(deep=True, update={"run_id": str(uuid4())})
    workspace.specs.save_extraction(doc, newer)
    fresh = document_notes_view(workspace, doc)
    assert fresh["source_fingerprint"] != original_view["source_fingerprint"]
    assert fresh["version"] == 0 and fresh["pending_count"] == len(fresh["notes"])
    # Historical views retain their own acknowledgements; POST always addresses current source.
    assert document_notes_view(workspace, doc, run=run)["pending_count"] == 0
    with pytest.raises(LibraryConflict):
        check(workspace, doc, original_view)
    check(workspace, doc, fresh)
    changed = [row.model_copy(update={"original_test_name": "Compression set (checked title)"})
               for row in newer.extraction.requirements]
    workspace.facts.save(newer, changed, "Checked this source title.", expected_version=0)
    corrected = document_notes_view(workspace, doc)
    assert corrected["source_fingerprint"] != fresh["source_fingerprint"]
    assert corrected["version"] == 0 and corrected["pending_count"] == len(corrected["notes"])


def test_reviewed_checks_are_scoped_even_with_identical_source_and_shared_sql_pool(sample):
    workspace, doc, _run, _settings = sample
    saved = check(workspace, doc)
    other = copy(workspace)
    other.labs = SimpleNamespace(workspace_id="another_workspace")
    untouched = document_notes_view(other, doc)
    assert untouched["source_fingerprint"] == saved["source_fingerprint"]
    assert untouched["version"] == 0 and untouched["pending_count"] == len(saved["notes"])
    check(other, doc, untouched)
    assert document_notes_view(workspace, doc) == saved


def test_transient_normalized_comparison_view_does_not_change_the_ack_target(sample):
    workspace, doc, run, _settings = sample
    original = document_notes_view(workspace, doc)
    derived = run.model_copy(deep=True)
    derived.extraction.requirements[0].conditions[0].raw_value = (
        "Extruded EPDM door-gasket rings and companion rubber sheets"
    )
    # The physical display may contain the clause; it cannot rewrite the saved facts.
    assert document_notes_view(workspace, doc, run=derived) == original
    check(workspace, doc, original)
    assert document_notes_view(workspace, doc, run=derived)["pending_count"] == 0


def test_current_noop_preserves_event_timestamp_and_stale_noop_still_conflicts(sample):
    workspace, doc, _run, _settings = sample
    original = document_notes_view(workspace, doc)
    assert check(workspace, doc, original, reviewed=False) == original
    saved = check(workspace, doc, original)
    assert check(workspace, doc, saved) == saved
    with pytest.raises(LibraryConflict):
        check(workspace, doc, original)
    with workspace.library.store.engine.connect() as connection:
        events = connection.execute(select(note_reviews.c.payload)).scalars().all()
    assert len(events) == 1 and events[0]["version"] == 1


def test_known_document_bookkeeping_is_readable_and_keeps_original_audit():
    issue = (
        "Source-window extractions returned differing document metadata; "
        "the first window's metadata was retained and must be checked against the source."
    )
    note = _document_information_note(issue)
    assert note["title"] == "Document details" and note["original_issue"] == issue
    assert "source-window" not in note["detail"].lower()
    assert "title, document number, and revision" in note["detail"]
    approval = "Page 13 approval-package table summarizes tests described elsewhere."
    note = _document_information_note(approval)
    assert note["title"] == "Approval packages" and note["original_issue"] == approval
    scientific = "Approval-package summaries require testing at 23 °C, not 40 °C."
    note = _document_information_note(scientific)
    assert note["detail"] == scientific

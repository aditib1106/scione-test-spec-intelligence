"""Export storage may share machine evidence, never change a frozen public view."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects import mysql, sqlite

from scione.library.store import (
    LibraryConflict,
    LibraryStore,
    LibraryStoreError,
    comparisons,
    digest,
)

REFERENCE = "_scione_comparison_export_reference"


@pytest.fixture
def archive(tmp_path):
    store = LibraryStore(tmp_path / "archive")
    yield store
    store.close()


def document(store, identifier="a" * 16):
    store.ensure_document(
        SimpleNamespace(
            document_id=identifier,
            original_name=f"{identifier}.pdf",
            created_at=datetime(2026, 11, 24, tzinfo=UTC),
        )
    )
    return store.document(identifier)


def machine(store, identifier="a" * 16):
    payload = {
        "document": document(store, identifier),
        "lab_name": "Example laboratory",
        "report_input_fingerprint": "f" * 64,
        "requirements": [
            {
                "requirement_id": f"req_{index}",
                "test_name": f"Test {index}",
                "source_review_state": "ready" if index % 2 else "needs_attention",
                "comparison": {"status": "fully_matched"},
                "source_evidence": "An exact source-backed parameter and context. " * 60,
            }
            for index in range(25)
        ],
        "summary": {"fully_matched": 25, "needs_information": 0},
        "notes": ["An immutable machine assessment."],
    }
    return store.save_comparison(digest(["machine", payload]), payload)


def export(store, base, version=1, **changes):
    body = {
        **deepcopy(base),
        "machine_comparison_id": base["comparison_id"],
        "assessment_reviews": {
            "req_0": {
                "version": version,
                "review_status": "accepted",
                "decision": "clarify_customer",
                "note": f"Saved customer question {version}.",
            },
        },
        "customer_clarifications": {
            "req_0": {
                "version": version,
                "events": [{"event_id": f"event-{version}", "text": f"Reply {version}."}],
            },
        },
        "export_format_version": "lab_gap_pdf_v2",
        **changes,
    }
    fingerprint = digest(["export", body])
    return fingerprint, body, store.save_comparison(fingerprint, body)


def raw(store, identifier):
    with store.engine.connect() as connection:
        return connection.execute(
            select(comparisons.c.payload).where(comparisons.c.comparison_id == identifier)
        ).scalar_one()


def row_count(store):
    with store.engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(comparisons)).scalar_one()


def test_repeated_review_exports_grow_by_frozen_overlays_not_copied_machine_rows(archive):
    base = machine(archive)
    snapshots = [export(archive, base, version)[2] for version in range(1, 13)]
    stored_exports = [raw(archive, saved["comparison_id"]) for saved in snapshots]
    assert row_count(archive) == 13
    assert raw(archive, base["comparison_id"]) == base
    assert REFERENCE not in base
    for stored, saved in zip(stored_exports, snapshots, strict=True):
        assert stored[REFERENCE]["version"] == 1
        assert "source_evidence" not in json.dumps(stored)
        assert archive.comparison(saved["comparison_id"]) == saved
    full_bytes = sum(len(json.dumps(saved).encode()) for saved in snapshots)
    stored_bytes = sum(len(json.dumps(saved).encode()) for saved in stored_exports)
    assert stored_bytes < full_bytes // 10
    with archive.engine.connect() as connection:
        assert (
            connection.execute(
                select(func.count()).select_from(comparisons).where(comparisons.c.pdf.is_not(None))
            ).scalar_one()
            == 0
        )


def test_monitoring_json_projections_keep_the_same_summary_and_source_review_counts(archive):
    base = machine(archive)
    snapshot = export(archive, base)[2]
    query = select(
        comparisons.c.payload["summary"],
        comparisons.c.payload["requirements"],
    ).where(comparisons.c.comparison_id == snapshot["comparison_id"])
    with archive.engine.connect() as connection:
        summary, rows = connection.execute(query).one()
    assert summary == snapshot["summary"]
    assert len(rows) == len(snapshot["requirements"])
    assert sum(row.get("source_review_state") == "needs_attention" for row in rows) == sum(
        row.get("source_review_state") == "needs_attention" for row in snapshot["requirements"]
    )
    for dialect in (sqlite.dialect(), mysql.dialect()):
        assert "JSON_EXTRACT" in str(query.compile(dialect=dialect))


def test_compact_snapshot_keeps_identity_conflicts_manual_threads_and_first_pdf(archive):
    base = machine(archive)
    fingerprint, body, saved = export(archive, base)
    identifier = saved["comparison_id"]
    expected = deepcopy(saved)
    body["assessment_reviews"]["req_0"]["note"] = "Changed caller draft"
    saved["customer_clarifications"]["req_0"]["events"][0]["text"] = "Changed caller reply"
    assert archive.comparison(identifier) == expected
    rendered_payloads = []

    def render(payload):
        rendered_payloads.append(deepcopy(payload))
        return b"%PDF-1.7\nFrozen reviewed assessment and customer reply"

    original_pdf = archive.comparison_pdf(identifier, render)
    assert rendered_payloads == [expected]
    second = export(archive, base, 2)[2]
    assert second["comparison_id"] != identifier
    assert archive.comparison(identifier) == expected
    assert archive.comparison_pdf(identifier, render) == original_pdf
    assert rendered_payloads == [expected]
    assert archive.save_comparison(fingerprint, expected) == expected
    with pytest.raises(LibraryConflict, match="inputs changed"):
        archive.save_comparison(fingerprint, body)
    loaded = archive.comparison(identifier)
    loaded["requirements"].clear()
    assert archive.comparison(identifier) == expected
    archive.close()
    assert archive.comparison(identifier) == expected
    assert archive.comparison_pdf(identifier, render) == original_pdf


def test_old_full_export_without_available_machine_reference_still_reads_and_exports(archive):
    base = machine(archive)
    legacy = {
        **deepcopy(base),
        "comparison_id": "b" * 32,
        "created_at": "2026-11-10T00:00:00+00:00",
        "machine_comparison_id": "not-a-reference-required-by-the-legacy-format",
        "assessment_reviews": {"req_0": {"note": "A historical saved note."}},
    }
    fingerprint = digest(["legacy-full-export", legacy])
    with archive.engine.begin() as connection:
        connection.execute(
            insert(comparisons).values(
                comparison_id=legacy["comparison_id"],
                document_id=legacy["document"]["document_id"],
                input_fingerprint=fingerprint,
                created_at=legacy["created_at"],
                payload=legacy,
            )
        )
    assert archive.comparison(legacy["comparison_id"]) == legacy
    assert archive.save_comparison(fingerprint, deepcopy(legacy)) == legacy
    seen = []
    assert archive.comparison_pdf(
        legacy["comparison_id"],
        lambda body: seen.append(body) or b"%PDF-1.7\nLegacy snapshot",
    ).startswith(b"%PDF-")
    assert seen == [legacy]
    assert raw(archive, legacy["comparison_id"]) == legacy


@pytest.mark.parametrize("changed_field", ["requirements", "new_business_field", "numeric_type"])
def test_unexpected_export_body_changes_fall_back_to_full_storage(archive, changed_field):
    base = machine(archive)
    if changed_field == "requirements":
        changes = {
            "requirements": [*deepcopy(base["requirements"]), {"test_name": "Additional content"}],
        }
    elif changed_field == "numeric_type":
        # Python considers 25 == 25.0, but the public JSON representation differs.
        changes = {"summary": {"fully_matched": 25.0, "needs_information": 0}}
    else:
        changes = {"new_business_field": {"keep": "exactly"}}
    saved = export(archive, base, **changes)[2]
    assert REFERENCE not in raw(archive, saved["comparison_id"])
    assert archive.comparison(saved["comparison_id"]) == saved


@pytest.mark.parametrize("bad_base", ["missing", "other_document", "export_snapshot"])
def test_new_export_cannot_reference_missing_foreign_or_export_evidence(archive, bad_base):
    base = machine(archive)
    if bad_base == "missing":
        base_id = "f" * 32
    elif bad_base == "other_document":
        base_id = machine(archive, "c" * 16)["comparison_id"]
    else:
        base_id = export(archive, base)[2]["comparison_id"]
    before = row_count(archive)
    with pytest.raises(LibraryConflict, match="machine comparison"):
        export(archive, base, machine_comparison_id=base_id)
    assert row_count(archive) == before


@pytest.mark.parametrize("corruption", ["missing_base", "changed_base", "other_document", "cycle"])
def test_compact_reads_and_cached_pdf_fail_closed_on_invalid_base(archive, corruption):
    base = machine(archive)
    saved = export(archive, base)[2]
    identifier = saved["comparison_id"]
    archive.comparison_pdf(identifier, lambda _: b"%PDF-1.7\nAlready frozen")
    foreign = machine(archive, "d" * 16) if corruption == "other_document" else None
    with archive.engine.begin() as connection:
        if corruption == "missing_base":
            connection.execute(
                delete(comparisons).where(
                    comparisons.c.comparison_id == base["comparison_id"],
                )
            )
        elif corruption == "changed_base":
            changed = {**base, "lab_name": "Changed machine evidence"}
            connection.execute(
                update(comparisons)
                .where(
                    comparisons.c.comparison_id == base["comparison_id"],
                )
                .values(payload=changed)
            )
        else:
            stored = raw(archive, identifier)
            reference = stored[REFERENCE]
            target = identifier if corruption == "cycle" else foreign["comparison_id"]
            reference["machine_comparison_id"] = target
            reference["overlays"]["machine_comparison_id"] = target
            if foreign:
                reference["machine_digest"] = digest(foreign)
            connection.execute(
                update(comparisons)
                .where(
                    comparisons.c.comparison_id == identifier,
                )
                .values(payload=stored)
            )
    with pytest.raises(LibraryStoreError, match="machine evidence"):
        archive.comparison(identifier)
    with pytest.raises(LibraryStoreError, match="machine evidence"):
        archive.comparison_pdf(
            identifier, lambda _: pytest.fail("Invalid evidence must not render")
        )


@pytest.mark.parametrize("corruption", ["version", "overlay", "projection", "identity", "fields"])
def test_compact_metadata_corruption_never_leaks_an_internal_or_modified_snapshot(
    archive, corruption
):
    base = machine(archive)
    fingerprint, _body, saved = export(archive, base)
    identifier = saved["comparison_id"]
    stored = raw(archive, identifier)
    reference = stored[REFERENCE]
    if corruption == "version":
        reference["version"] = 2
    elif corruption == "overlay":
        reference["overlays"]["assessment_reviews"]["req_0"]["note"] = "Changed note"
    elif corruption == "projection":
        stored["summary"]["fully_matched"] = 999
    elif corruption == "identity":
        reference["overlays"]["comparison_id"] = "e" * 32
    else:
        reference["overlays"]["requirements"] = []
    with archive.engine.begin() as connection:
        connection.execute(
            update(comparisons)
            .where(
                comparisons.c.comparison_id == identifier,
            )
            .values(payload=stored)
        )
    with pytest.raises(LibraryStoreError):
        archive.comparison(identifier)
    with pytest.raises(LibraryStoreError):
        archive.save_comparison(fingerprint, saved)


def test_public_save_cannot_inject_an_internal_reference(archive):
    base = machine(archive)
    before = row_count(archive)
    with pytest.raises(LibraryConflict, match="storage metadata"):
        archive.save_comparison("a" * 64, {**base, REFERENCE: {"version": 1}})
    assert row_count(archive) == before

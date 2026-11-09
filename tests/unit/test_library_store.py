"""The SQL archive preserves identities, reviewed details and immutable exports."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable

from scione.catalogs.seed import default_csp301_lab
from scione.library.store import (
    LibraryConflict,
    LibraryStore,
    LibraryStoreError,
    companies,
    digest,
    metadata,
)
from scione.providers import TokenUsage
from scione.specifications.models import CustomerDocumentMetadata, CustomerExtractionRun
from scione.structured.models import AtomicClaim, NormalizedRequirement


@pytest.fixture
def store(tmp_path):
    archive = LibraryStore(tmp_path / "workspace")
    yield archive
    archive.close()


def _document(store: LibraryStore, document_id: str = "a" * 16, source=None):
    stored = SimpleNamespace(
        document_id=document_id,
        original_name=f"{document_id}.pdf",
        created_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    store.ensure_document(stored, source)
    return stored


def _edit(**changes) -> dict:
    return {
        "company_name": "Example Vehicle Group",
        "specification_id": "CSP-EN-301",
        "title": "Exterior finish approval",
        "revision": "Issue A",
        **changes,
    }


def _run(document_id: str = "a" * 16, run_id: str = "run_one") -> CustomerExtractionRun:
    return CustomerExtractionRun(
        run_id=run_id,
        document_id=document_id,
        started_at=datetime(2026, 11, 3, tzinfo=UTC),
        schema_version="0.1",
        prompt_version="test_fixture",
        provider="static",
        model="offline",
        latency_ms=0,
        usage=TokenUsage(),
        raw_response="untouched provider text",
        extraction={
            "schema_version": "0.1",
            "document": {"identifier": "CSP-EN-301"},
            "requirements": [
                {
                    "id": f"req_{number}",
                    "original_test_name": name,
                    "requested_methods": [{"code": code}],
                    "conditions": [{"name": "Temperature", "raw_value": f"{number} °C"}],
                    "evidence": [{"page": number, "quote": f"Source temperature {number} °C"}],
                }
                for number, name, code in [
                    (1, "Cross-cut adhesion", "ISO 60038:2021"),
                    (2, "Specular gloss", "ASTM D1627-16"),
                ]
            ],
        },
    )


def _normalized(run: CustomerExtractionRun) -> list[NormalizedRequirement]:
    return [
        NormalizedRequirement(
            requirement_id=requirement.id,
            test_name=requirement.original_test_name,
            claims=[
                AtomicClaim(
                    id=f"{requirement.id}_temperature",
                    parameter_id="temperature",
                    label="Temperature",
                    kind="condition",
                    stage="conditioning",
                    operator="=",
                    value=number,
                    unit="°C",
                    raw_text=f"{number} °C",
                    source_pages=[number],
                )
            ],
        )
        for number, requirement in enumerate(run.extraction.requirements, 1)
    ]


def _comparison(store: LibraryStore) -> dict:
    return {
        "document": store.document("a" * 16),
        "lab_name": "Example Lab",
        "requirements": [{"test_name": "Original test", "result": "needs_information"}],
    }


def test_database_is_lazy_and_persists_document_metadata_across_reopen(tmp_path):
    root = tmp_path / "archive"
    first = LibraryStore(root)
    assert not root.exists()
    _document(first)
    first.edit_metadata("a" * 16, _edit(), 0)
    before = first.document("a" * 16)
    first.close()

    second = LibraryStore(root)
    try:
        assert second.document("a" * 16) == before
        assert second.backend == "sqlite"
        with second._db().connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        second.close()


def test_index_enriches_first_source_and_preserves_later_manual_clears(store):
    stored = _document(store)
    source = CustomerDocumentMetadata(
        title="From source", identifier="CSP-EN-301", revision="Issue A"
    )
    store.ensure_document(stored, source)
    indexed = store.document(stored.document_id)
    assert indexed["title"] == "From source"
    assert indexed["revision"] == "Issue A"
    run = _run(stored.document_id)
    store.save_requirements(run, _normalized(run))
    store.edit_metadata(
        stored.document_id,
        _edit(title="Reviewed title", revision=None),
        indexed["version"],
    )

    store.ensure_document(stored, source)

    assert store.document(stored.document_id)["title"] == "Reviewed title"
    assert store.document(stored.document_id)["revision"] is None


def test_company_only_upload_is_enriched_after_analysis_and_rejects_stale_editor(store):
    stored = _document(store)
    store.edit_metadata(
        stored.document_id,
        _edit(title=None, specification_id=None, revision=None),
        0,
    )
    before = store.document(stored.document_id)

    source = CustomerDocumentMetadata(
        title="Extracted title",
        identifier="EXTRACTED-301",
        revision="Issue A",
    )
    store.ensure_document(stored, source)

    saved = store.document(stored.document_id)
    assert saved["company_name"] == before["company_name"]
    assert saved["title"] == "Extracted title"
    assert saved["specification_id"] == "EXTRACTED-301"
    assert saved["revision"] == "Issue A"
    assert saved["version"] > before["version"]
    with pytest.raises(LibraryConflict, match="changed"):
        store.edit_metadata(stored.document_id, _edit(), before["version"])
    store.ensure_document(stored, source)
    assert store.document(stored.document_id) == saved


def test_source_family_enrichment_updates_sibling_versions_without_changing_revision(store):
    first = _document(store)
    store.edit_metadata(first.document_id, _edit(title=None, specification_id=None), 0)
    family = store.document(first.document_id)
    second = _document(store, "b" * 16)
    store.edit_metadata(
        second.document_id,
        _edit(
            family_id=family["family_id"],
            title=None,
            specification_id=None,
            revision="Issue B",
        ),
        0,
    )
    before = store.document(second.document_id)

    store.ensure_document(
        first,
        CustomerDocumentMetadata(
            title="Source family title",
            identifier="SOURCE-301",
            revision="Issue A",
        ),
    )

    sibling = store.document(second.document_id)
    assert sibling["title"] == "Source family title"
    assert sibling["specification_id"] == "SOURCE-301"
    assert sibling["revision"] == "Issue B"
    assert sibling["version"] > before["version"]


def test_later_version_does_not_refill_shared_fields_cleared_after_analysis(store):
    first = _document(store)
    store.edit_metadata(first.document_id, _edit(), 0)
    run = _run(first.document_id)
    store.save_requirements(run, _normalized(run))
    version = store.document(first.document_id)["version"]
    store.edit_metadata(first.document_id, _edit(title=None, specification_id=None), version)
    family = store.document(first.document_id)
    second = _document(store, "b" * 16)
    store.edit_metadata(
        second.document_id,
        _edit(
            family_id=family["family_id"],
            title=None,
            specification_id=None,
            revision=None,
        ),
        0,
    )

    store.ensure_document(
        second,
        CustomerDocumentMetadata(
            title="Unreviewed title",
            identifier="OTHER-301",
            revision="Issue B",
        ),
    )

    saved = store.document(second.document_id)
    assert saved["title"] is None
    assert saved["specification_id"] is None
    assert saved["revision"] == "Issue B"


def test_linking_a_revision_preserves_existing_family_identity(store):
    _document(store)
    store.edit_metadata("a" * 16, _edit(), 0)
    original = store.document("a" * 16)
    _document(store, "b" * 16)

    store.edit_metadata(
        "b" * 16,
        _edit(family_id=original["family_id"], revision="Issue B", title="Different upload title"),
        0,
    )

    revised = store.document("b" * 16)
    assert revised["family_id"] == original["family_id"]
    assert revised["company_id"] == original["company_id"]
    assert revised["title"] == "Exterior finish approval"
    assert revised["revision"] == "Issue B"
    assert store.document("a" * 16) == original


def test_cross_company_or_specification_link_is_rejected_atomically(store):
    _document(store)
    store.edit_metadata("a" * 16, _edit(), 0)
    original = store.document("a" * 16)
    _document(store, "b" * 16)
    with pytest.raises(LibraryConflict, match="same customer"):
        store.edit_metadata(
            "b" * 16, _edit(company_name="Different Company", family_id=original["family_id"]), 0
        )
    with store._db().connect() as connection:
        assert len(connection.execute(select(companies)).all()) == 1
    with pytest.raises(LibraryConflict, match="different specification"):
        store.edit_metadata(
            "b" * 16, _edit(specification_id="UNRELATED-SPEC", family_id=original["family_id"]), 0
        )
    assert store.document("b" * 16)["version"] == 0


def test_shared_metadata_change_invalidates_other_version_editors(store):
    _document(store)
    store.edit_metadata("a" * 16, _edit(), 0)
    family_id = store.document("a" * 16)["family_id"]
    _document(store, "b" * 16)
    store.edit_metadata("b" * 16, _edit(family_id=family_id, revision="Issue B"), 0)
    stale = store.document("b" * 16)["version"]

    store.edit_metadata("a" * 16, _edit(title="Corrected family title"), 1)

    assert store.document("b" * 16)["title"] == "Corrected family title"
    with pytest.raises(LibraryConflict, match="changed"):
        store.edit_metadata("b" * 16, _edit(family_id=family_id), stale)


def test_metadata_stale_writes_are_rejected(store):
    _document(store)
    store.edit_metadata("a" * 16, _edit(), 0)
    with pytest.raises(LibraryConflict, match="changed"):
        store.edit_metadata("a" * 16, _edit(title="Stale overwrite"), 0)
    assert store.document("a" * 16)["title"] == "Exterior finish approval"


def test_atomic_requirements_reload_by_identity_and_original_source_order(store):
    _document(store)
    run = _run()
    store.save_requirements(run, list(reversed(_normalized(run))))

    rows = store.load_requirements(run.run_id)

    assert [row["requirement_id"] for row in rows] == ["req_1", "req_2"]
    assert rows[0]["claims"][0]["value"] == "1"
    assert rows[1]["claims"][0]["value"] == "2"
    assert rows[1]["claims"][0]["source_pages"] == [2]
    assert rows[1]["methods"] == ["ASTM D1627-16"]
    store.save_requirements(run, _normalized(run))
    assert store.load_requirements(run.run_id) == rows


def test_run_identity_cannot_be_reused_for_different_source(store):
    _document(store)
    run = _run()
    store.save_requirements(run, _normalized(run))
    original = store.load_requirements(run.run_id)
    changed = run.model_copy(deep=True)
    changed.extraction.requirements[0].original_test_name = "Overwritten original"

    with pytest.raises(LibraryConflict, match="different source"):
        store.save_requirements(changed, _normalized(changed))

    assert store.load_requirements(run.run_id) == original


def test_mismatched_or_duplicate_normalized_requirement_ids_are_rejected(store):
    _document(store)
    run = _run()
    normalized = _normalized(run)
    with pytest.raises(LibraryConflict, match="do not match"):
        store.save_requirements(run, normalized[:1])
    with pytest.raises(LibraryConflict, match="do not match"):
        store.save_requirements(run, [normalized[0], normalized[0]])
    assert store.load_requirements(run.run_id) == []


def test_capability_snapshot_preserves_its_original_lab_revision(store):
    lab = default_csp301_lab()
    fingerprint = digest(lab.model_dump(mode="json"))
    store.save_capability_revision(lab, fingerprint)
    store.save_capability_revision(lab, fingerprint)
    changed = lab.model_copy(update={"name": "Different name"})
    with pytest.raises(LibraryConflict, match="different data"):
        store.save_capability_revision(changed, fingerprint)


def test_comparison_json_and_first_pdf_remain_immutable(store):
    _document(store)
    payload = _comparison(store)
    saved = store.save_comparison("c" * 64, payload)
    payload["requirements"][0]["test_name"] = "Changed after saving"
    comparison_id = saved["comparison_id"]
    assert store.comparison(comparison_id)["requirements"][0]["test_name"] == "Original test"
    original_pdf = b"%PDF-1.7\nOriginal saved artifact"
    assert store.comparison_pdf(comparison_id, lambda _: original_pdf) == original_pdf

    def different_renderer(_payload):
        pytest.fail("A saved PDF must not be rendered again.")

    assert store.comparison_pdf(comparison_id, different_renderer) == original_pdf
    assert store.save_comparison("c" * 64, _comparison(store)) == saved
    with pytest.raises(LibraryConflict, match="inputs changed"):
        store.save_comparison("c" * 64, payload)


def test_two_concurrent_pdf_exports_return_the_single_winning_artifact(store):
    _document(store)
    comparison_id = store.save_comparison("c" * 64, _comparison(store))["comparison_id"]
    barrier = Barrier(2)

    def export(number):
        def render(_payload):
            barrier.wait(timeout=5)
            return f"%PDF-1.7\nRenderer {number}".encode()

        return store.comparison_pdf(comparison_id, render)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(export, [1, 2]))

    assert results[0] == results[1]


def test_schema_compiles_for_mysql_and_sqlite_without_a_database_server():
    for dialect in [mysql.dialect(), sqlite.dialect()]:
        for table in metadata.sorted_tables:
            assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=dialect))
    mysql_pdf = str(
        CreateTable(metadata.tables["comparison_runs"]).compile(dialect=mysql.dialect())
    )
    assert "LONGBLOB" in mysql_pdf


def test_embedded_database_rejects_symlink_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    archive = LibraryStore(alias)
    with pytest.raises(LibraryStoreError, match="symlink"):
        _document(archive)
    assert not (target / "library.sqlite3").exists()


def test_clear_removes_archive_records_but_keeps_schema_usable(store):
    _document(store)
    store.save_requirements(_run(), _normalized(_run()))
    store.save_comparison("c" * 64, _comparison(store))

    store.clear()

    assert store.load_requirements("run_one") == []
    _document(store, "b" * 16)
    assert store.document("b" * 16)["document_id"] == "b" * 16

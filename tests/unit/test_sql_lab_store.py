"""SQL lab data survives process reloads, legacy import and concurrent editors."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from scione.catalogs import FileLabStore, LabStoreError, LabVersionConflict, load_reference_catalog
from scione.catalogs.sql_lab_store import (
    SQLLabStore,
    lab_claims,
    lab_entries,
    lab_metadata,
    lab_revisions,
    upgrade_lab_schema,
)
from scione.library.store import LibraryStore, companies
from scione.matching.store import SQLMatchingStore
from scione.structured.models import AtomicClaim


def sql_store(tmp_path, *, workspace_id="local_lab", legacy=True):
    return SQLLabStore(
        LibraryStore(tmp_path),
        load_reference_catalog(),
        workspace_id=workspace_id,
        legacy_root=tmp_path if legacy else None,
    )


def test_existing_archive_is_preserved_by_idempotent_additive_migration(tmp_path):
    library = LibraryStore(tmp_path)
    with library.engine.begin() as connection:
        connection.execute(insert(companies).values(id="existing", lookup_key="old", name="Old Co"))
    upgrade_lab_schema(library)
    upgrade_lab_schema(library)
    with library.engine.connect() as connection:
        assert connection.execute(select(companies.c.name)).scalar_one() == "Old Co"
    assert set(lab_metadata.tables) <= set(
        __import__("sqlalchemy").inspect(library.engine).get_table_names()
    )


def test_legacy_profile_imports_once_and_original_file_remains_unchanged(tmp_path):
    legacy = FileLabStore(tmp_path)
    lab = legacy.load_or_create()
    lab.name = "Previously entered lab"
    lab.capabilities[0].price = Decimal("50.123456789")
    legacy.save(lab)
    original = legacy.path.read_bytes()
    store = sql_store(tmp_path)
    imported = store.load_or_create()
    assert imported == legacy.load_or_create()
    imported.name = "SQL update"
    store.save(imported)
    store.library.close()
    assert sql_store(tmp_path).load_or_create().name == "SQL update"
    assert legacy.path.read_bytes() == original
    assert sql_store(tmp_path).load_or_create().capabilities[0].price == Decimal("50.123456789")


def test_corrupt_legacy_is_preserved_and_never_reseeded(tmp_path):
    (tmp_path / "lab.json").write_text("{bad data")
    with pytest.raises(LabStoreError):
        sql_store(tmp_path).load_or_create()
    assert (tmp_path / "lab.json").read_text() == "{bad data"


def test_no_legacy_starts_empty_and_does_not_create_json(tmp_path):
    store = sql_store(tmp_path)
    assert store.load_or_create().capabilities == []
    assert not (tmp_path / "lab.json").exists()
    assert store.records()["claim_count"] == 0


def test_claims_are_separate_queryable_rows_and_revisions_are_immutable(tmp_path):
    source = FileLabStore(tmp_path)
    original = source.load_or_create()
    original.capabilities = original.capabilities[:1]
    original.capabilities[0].atomic_claims = [
        AtomicClaim(
            id="water_temperature",
            parameter_id="temperature",
            label="Water temperature",
            stage="water_jet",
            operator="range",
            minimum=20,
            maximum=60,
            unit="°C",
            raw_text="Water temperature 20 to 60 °C",
            source_context="water jet operation",
        )
    ]
    source.save(original)
    store = sql_store(tmp_path)
    imported = store.load_or_create()
    records = store.records()
    assert records["storage"] == "sql"
    assert any(row["id"] == "water_temperature" for row in records["claims"])
    with store.library.engine.connect() as connection:
        first_snapshot = connection.execute(select(lab_revisions.c.payload)).scalar_one()
        assert connection.execute(select(lab_entries.c.capability_id)).scalar_one()
        assert connection.execute(select(lab_claims.c.parameter_id)).first()
    imported.capabilities[0].atomic_claims[0].maximum = Decimal(80)
    store.save(imported)
    reloaded = sql_store(tmp_path).load_or_create()
    assert reloaded.capabilities[0].atomic_claims[0].maximum == Decimal(80)
    with store.library.engine.connect() as connection:
        preserved = connection.execute(
            select(lab_revisions.c.payload).where(
                lab_revisions.c.version == first_snapshot["version"]
            )
        ).scalar_one()
    assert preserved == first_snapshot
    assert len(store.records()["revisions"]) == 2


def test_stale_update_from_another_store_does_not_overwrite(tmp_path):
    first, second = sql_store(tmp_path), sql_store(tmp_path)
    current, stale = first.load_or_create(), second.load_or_create()
    current.name = "First editor"
    first.save(current)
    stale.name = "Stale editor"
    with pytest.raises(LabVersionConflict, match="Reload"):
        second.save(stale)
    assert second.load_or_create().name == "First editor"
    assert len(second.records()["revisions"]) == 2


def test_capability_write_failure_rolls_back_profile_and_revision(tmp_path, monkeypatch):
    store = sql_store(tmp_path)
    original = store.load_or_create()
    changed = original.model_copy(update={"name": "Should not be saved"})

    def fail(*args):
        raise ValueError("simulated claim validation failure")

    monkeypatch.setattr(store, "_write_entries", fail)
    with pytest.raises(LabStoreError):
        store.save(changed)
    assert store.load_or_create() == original
    assert len(store.records()["revisions"]) == 1


def test_workspace_binding_isolates_profiles_and_claims_in_shared_database(tmp_path):
    first = sql_store(tmp_path, workspace_id="lab_a", legacy=False)
    second = sql_store(tmp_path, workspace_id="lab_b", legacy=False)
    lab = first.load_or_create()
    second.load_or_create()
    lab.name = "Only lab A"
    lab.capabilities = FileLabStore(tmp_path / "fixture").load_or_create().capabilities[:1]
    first.save(lab)
    assert first.records()["claim_count"] > 0
    assert second.load_or_create().name == "My Lab"
    assert second.records()["claims"] == []
    assert len(second.records()["revisions"]) == 1


def test_new_tables_compile_for_mysql_with_scoped_foreign_keys():
    for table in lab_metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=mysql.dialect()))


def test_concurrent_first_use_serializes_additive_schema_creation(tmp_path):
    library = LibraryStore(tmp_path)
    labs = SQLLabStore(library, load_reference_catalog())
    matching = SQLMatchingStore(library, "local_lab")

    def initialize(index):
        return labs.load_or_create().version if index % 2 else matching.listing("document")

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(initialize, range(16)))
    assert results == [[] if index % 2 == 0 else 1 for index in range(16)]
    assert len(labs.records()["revisions"]) == 1

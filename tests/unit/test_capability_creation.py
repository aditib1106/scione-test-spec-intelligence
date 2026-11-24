"""Capability list chronology must never alter scientific declarations or history."""

from copy import deepcopy

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from scione.catalogs import FileLabStore, LabStoreError, LabVersionConflict, load_reference_catalog
from scione.catalogs.capability_creation import capability_creation, metadata
from scione.catalogs.models import LabProfile
from scione.catalogs.seed import default_demo_lab
from scione.catalogs.sql_lab_store import (
    SQLLabStore,
    lab_claims,
    lab_entries,
    lab_metadata,
    lab_revisions,
    lab_workspaces,
    upgrade_lab_schema,
)
from scione.library.store import LibraryStore, digest, schema_versions
from scione.reports.matching import lab_fingerprint


def store_at(root, *, workspace_id="local_lab", legacy=False):
    return SQLLabStore(
        LibraryStore(root), load_reference_catalog(), workspace_id=workspace_id,
        legacy_root=root if legacy else None,
    )


def capability(identifier):
    return default_demo_lab().capabilities[0].model_copy(update={"id": identifier}, deep=True)


def add(store, identifier):
    lab = store.load_or_create()
    lab.capabilities.append(capability(identifier))
    store.save(lab)
    return store.load_or_create()


def rows(store):
    with store.library.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(capability_creation)).mappings()]


def scientific_rows(store):
    with store.library.engine.connect() as connection:
        return {
            table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
            for table in (lab_workspaces, lab_entries, lab_claims, lab_revisions)
        }


def test_literal_v2_database_upgrades_without_backfill_or_changed_declarations(tmp_path):
    store = store_at(tmp_path)
    engine = store.library.engine
    # Build the actual old v2 table layout, with no new component or sidecar.
    lab_metadata.create_all(engine)
    original = LabProfile(
        id="demo_lab", name="Existing lab", version=7,
        capabilities=[capability("legacy_a"), capability("legacy_b")],
    )
    with engine.begin() as connection:
        connection.execute(insert(schema_versions).values(component="lab_storage", version=2))
        connection.execute(insert(lab_workspaces).values(
            workspace_id=store.workspace_id, profile_id=original.id, name=original.name,
            description=original.description, version=original.version,
            created_at="2026-10-01T12:00:00+00:00", updated_at="2026-10-02T12:00:00+00:00",
            legacy_imported=1,
        ))
        store._write_entries(connection, original)
        store._revision(connection, original)
    before = scientific_rows(store)
    fingerprint = lab_fingerprint(original)
    upgrade_lab_schema(store.library)
    upgrade_lab_schema(store.library)
    saved = store.load_or_create()
    assert saved.model_dump(mode="json") == original.model_dump(mode="json")
    assert lab_fingerprint(saved) == fingerprint
    assert scientific_rows(store) == before
    assert rows(store) == []
    assert store.creation_metadata(saved) == {
        "lab_id": original.id, "lab_version": 7, "created_at": {},
    }
    with engine.connect() as connection:
        components = {
            row.component: row.version for row in connection.execute(select(schema_versions))
        }
    assert components["lab_storage"] == 2
    assert components["lab_capability_creation"] == 1


def test_initial_file_import_retains_unknown_dates_and_untouched_source(tmp_path):
    source = FileLabStore(tmp_path)
    original = source.load_or_create()
    source_bytes = source.path.read_bytes()
    store = store_at(tmp_path, legacy=True)
    assert store.load_or_create() == original
    assert rows(store) == []
    assert store.creation_metadata(original)["created_at"] == {}
    assert source.path.read_bytes() == source_bytes
    assert store.load_or_create().capabilities == original.capabilities


def test_new_dates_persist_and_edits_never_change_creation_or_stored_order(tmp_path, monkeypatch):
    from scione.catalogs import capability_creation as module

    times = iter(["2026-11-24T12:00:00+00:00", "2026-11-24T12:01:00+00:00"])
    monkeypatch.setattr(module, "now", lambda: next(times))
    store = store_at(tmp_path)
    first = add(store, "created_a")
    second = add(store, "created_b")
    dates = store.creation_metadata(second)["created_at"]
    assert dates == {
        "created_a": "2026-11-24T12:00:00+00:00",
        "created_b": "2026-11-24T12:01:00+00:00",
    }
    assert store.creation_metadata(first) is None
    unchanged = deepcopy(second.model_dump(mode="json"))
    prior_rows = scientific_rows(store)
    prior_fingerprint = lab_fingerprint(second)
    assert store.creation_metadata(second)["created_at"] == dates
    assert second.model_dump(mode="json") == unchanged
    assert scientific_rows(store) == prior_rows
    assert lab_fingerprint(second) == prior_fingerprint
    second.capabilities[0].notes = "Edited declaration; still created first."
    store.save(second)
    edited = store.load_or_create()
    edited.name = "Edited profile name"
    store.save(edited)
    current = store.load_or_create()
    assert store.creation_metadata(current)["created_at"] == dates
    assert [entry.id for entry in current.capabilities] == ["created_a", "created_b"]
    with store.library.engine.connect() as connection:
        snapshots = connection.execute(select(lab_revisions.c.payload)).scalars().all()
    assert all("capability_creation" not in row for row in snapshots)
    assert all("created_at" not in entry for row in snapshots for entry in row["capabilities"])
    store.library.close()
    restarted = store_at(tmp_path)
    reloaded = restarted.load_or_create()
    assert reloaded == current
    assert restarted.creation_metadata(reloaded)["created_at"] == dates


def test_stale_save_cannot_create_dates_or_change_current_profile(tmp_path):
    store = store_at(tmp_path)
    stale = store.load_or_create()
    current = add(store, "winner")
    before = rows(store)
    stale.capabilities.append(capability("stale_only"))
    with pytest.raises(LabVersionConflict):
        store.save(stale)
    assert store.load_or_create() == current
    assert rows(store) == before


def test_late_transaction_failure_rolls_back_timestamp_and_profile_together(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    original = store.load_or_create()
    candidate = original.model_copy(deep=True)
    candidate.capabilities = [capability("rolled_back")]

    def fail(*_args):
        raise ValueError("Fixture failure after recording chronology")

    monkeypatch.setattr(store, "_revision", fail)
    with pytest.raises(LabStoreError):
        store.save(candidate)
    assert store.load_or_create() == original
    assert rows(store) == []
    with store.library.engine.connect() as connection:
        snapshots = connection.execute(select(lab_revisions.c.payload)).scalars().all()
    assert snapshots == [original.model_dump(mode="json")]


def test_same_ids_are_scoped_to_workspace_even_when_profile_ids_match(tmp_path, monkeypatch):
    from scione.catalogs import capability_creation as module

    times = iter(["2026-11-24T12:00:00+00:00", "2026-11-24T13:00:00+00:00"])
    monkeypatch.setattr(module, "now", lambda: next(times))
    first = store_at(tmp_path, workspace_id="lab_one")
    second = store_at(tmp_path, workspace_id="lab_two")
    first_lab = add(first, "same_identifier")
    assert second.creation_metadata(second.load_or_create())["created_at"] == {}
    second_lab = add(second, "same_identifier")
    assert first_lab.id == second_lab.id
    assert first.creation_metadata(first_lab)["created_at"] == {
        "same_identifier": "2026-11-24T12:00:00+00:00",
    }
    assert second.creation_metadata(second_lab)["created_at"] == {
        "same_identifier": "2026-11-24T13:00:00+00:00",
    }


def test_delete_and_restore_preserve_first_date_and_hide_deleted_ids(tmp_path, monkeypatch):
    from scione.catalogs import capability_creation as module

    monkeypatch.setattr(module, "now", lambda: "2026-11-24T12:00:00+00:00")
    store = store_at(tmp_path)
    lab = add(store, "restored")
    first_date = store.creation_metadata(lab)["created_at"]["restored"]
    saved_capability = lab.capabilities.pop()
    store.save(lab)
    removed = store.load_or_create()
    assert store.creation_metadata(removed)["created_at"] == {}
    assert rows(store)[0]["created_at"] == first_date
    monkeypatch.setattr(module, "now", lambda: pytest.fail("Restoration cannot reset creation"))
    removed.capabilities.append(saved_capability)
    store.save(removed)
    assert store.creation_metadata(store.load_or_create())["created_at"] == {
        "restored": first_date,
    }


def test_legacy_delete_and_restore_keep_unknown_original_date(tmp_path, monkeypatch):
    from scione.catalogs import capability_creation as module

    source = FileLabStore(tmp_path)
    source.load_or_create()
    store = store_at(tmp_path, legacy=True)
    lab = store.load_or_create()
    removed = lab.capabilities.pop(0)
    monkeypatch.setattr(module, "now", lambda: pytest.fail("Legacy dates must stay unknown"))
    store.save(lab)
    lab = store.load_or_create()
    lab.capabilities.append(removed)
    store.save(lab)
    assert store.creation_metadata(store.load_or_create())["created_at"] == {}
    assert rows(store) == [{
        "workspace_id": store.workspace_id, "capability_id": removed.id, "created_at": None,
    }]


def test_metadata_rejects_wrong_version_and_profile_without_changing_lab(tmp_path):
    store = store_at(tmp_path)
    current = add(store, "current")
    before = scientific_rows(store)
    stale = current.model_copy(update={"version": current.version - 1})
    assert store.creation_metadata(stale) is None
    assert store.creation_metadata(current.model_copy(update={"id": "different_lab"})) is None
    assert scientific_rows(store) == before


def test_incompatible_metadata_schema_fails_without_rewriting_original_profile(tmp_path):
    store = store_at(tmp_path)
    add(store, "old")
    before = scientific_rows(store)
    with store.library.engine.begin() as connection:
        connection.execute(delete(schema_versions).where(
            schema_versions.c.component == "lab_capability_creation"
        ))
        connection.execute(insert(schema_versions).values(
            component="lab_capability_creation", version=2
        ))
    from scione.library.store import LibraryStoreError

    with pytest.raises(LibraryStoreError, match="compatible"):
        upgrade_lab_schema(store.library)
    assert scientific_rows(store) == before


def test_creation_table_compiles_for_mysql_without_altering_lab_model():
    for table in metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=mysql.dialect()))
    lab = LabProfile(id="example", name="Example", capabilities=[capability("original")])
    assert digest(lab.model_dump(mode="json")) == digest(LabProfile.model_validate(
        lab.model_dump(mode="json")
    ).model_dump(mode="json"))
    assert "created_at" not in lab.model_dump(mode="json")["capabilities"][0]

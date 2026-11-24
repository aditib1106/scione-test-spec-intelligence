"""Small lab edits preserve history without rewriting every current declaration."""

from decimal import Decimal

import pytest
from sqlalchemy import event, select

from scione.catalogs import FileLabStore, LabStoreError, load_reference_catalog
from scione.catalogs.sql_lab_store import SQLLabStore, lab_claims, lab_entries, lab_revisions
from scione.library.store import LibraryStore


def seeded(tmp_path):
    legacy = FileLabStore(tmp_path)
    lab = legacy.load_or_create()
    assert len(lab.capabilities) > 1
    store = SQLLabStore(LibraryStore(tmp_path), load_reference_catalog(), legacy_root=tmp_path)
    return store, store.load_or_create()


def current_rows(store):
    with store.library.engine.connect() as connection:
        entries = [dict(row) for row in connection.execute(select(lab_entries)).mappings()]
        claims = [dict(row) for row in connection.execute(select(lab_claims)).mappings()]
    return entries, claims


def capture_current_writes(store):
    writes = []

    def before(_connection, _cursor, statement, _parameters, _context, _many):
        sql = statement.lower()
        if sql.lstrip().startswith(("update ", "insert ", "delete ")) and any(
            name in sql for name in ("lab_capability_entries", "lab_capability_claims")
        ):
            writes.append(sql)

    event.listen(store.library.engine, "before_cursor_execute", before)
    return writes, before


def test_price_only_save_updates_one_entry_and_keeps_all_claim_rows(tmp_path):
    store, lab = seeded(tmp_path)
    entries, claims = current_rows(store)
    writes, listener = capture_current_writes(store)
    try:
        lab.capabilities[0].price = Decimal("144.25")
        store.save(lab)
    finally:
        event.remove(store.library.engine, "before_cursor_execute", listener)
    assert len(writes) == 1
    assert writes[0].startswith("update lab_capability_entries set price=")
    after_entries, after_claims = current_rows(store)
    assert after_claims == claims
    for before, after in zip(entries, after_entries, strict=True):
        assert {k: v for k, v in before.items() if k != "price"} == {
            k: v for k, v in after.items() if k != "price"
        }
    assert store.load_or_create().capabilities[0].price == Decimal("144.25")
    assert len(store.records()["revisions"]) == 2


def test_profile_metadata_save_writes_no_current_entries_and_preserves_history(tmp_path):
    store, lab = seeded(tmp_path)
    before = current_rows(store)
    with store.library.engine.connect() as connection:
        original = connection.execute(select(lab_revisions.c.payload)).scalar_one()
    writes, listener = capture_current_writes(store)
    try:
        lab.name = "New display name"
        store.save(lab)
    finally:
        event.remove(store.library.engine, "before_cursor_execute", listener)
    assert writes == []
    assert current_rows(store) == before
    assert store.load_or_create().name == "New display name"
    with store.library.engine.connect() as connection:
        assert (
            connection.execute(
                select(lab_revisions.c.payload).where(
                    lab_revisions.c.version == original["version"]
                )
            ).scalar_one()
            == original
        )
    assert len(store.records()["revisions"]) == 2


def test_reordering_and_removal_preserve_surviving_claims(tmp_path):
    store, lab = seeded(tmp_path)
    _, before_claims = current_rows(store)
    lab.capabilities.reverse()
    writes, listener = capture_current_writes(store)
    try:
        store.save(lab)
    finally:
        event.remove(store.library.engine, "before_cursor_execute", listener)
    assert all(sql.startswith("update lab_capability_entries set position=") for sql in writes)
    assert current_rows(store)[1] == before_claims
    saved = store.load_or_create()
    assert [cap.id for cap in saved.capabilities] == [cap.id for cap in lab.capabilities]
    removed = saved.capabilities.pop(0)
    store.save(saved)
    assert current_rows(store)[1] == [
        row for row in before_claims if row["capability_id"] != removed.id
    ]
    assert len(store.records()["revisions"]) == 3


def test_invalid_index_rolls_back_partial_incremental_changes(tmp_path, monkeypatch):
    store, lab = seeded(tmp_path)
    before = current_rows(store)
    original = store.load_or_create()
    from scione.catalogs import sql_lab_store as module

    normalize = module.normalize_capability

    def invalid(capability):
        values = normalize(capability)
        assert values
        return [values[0], values[0]]

    monkeypatch.setattr(module, "normalize_capability", invalid)
    lab.name = "Should roll back"
    lab.capabilities[0].price = Decimal("99.99")
    with pytest.raises(LabStoreError, match="unique"):
        store.save(lab)
    assert store.load_or_create() == original
    assert current_rows(store) == before
    assert len(store.records()["revisions"]) == 1

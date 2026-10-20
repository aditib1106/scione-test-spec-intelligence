"""Local edits survive reloads without silently replacing corrupt or stale data."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from scione.catalogs import FileLabStore, LabStoreError, LabVersionConflict


def test_constructor_has_no_io_and_explicit_load_creates_seed(tmp_path: Path) -> None:
    root = tmp_path / "prototype"
    store = FileLabStore(root)
    assert not root.exists()
    lab = store.load_or_create()
    assert lab.version == 1
    assert (root / "lab.json").is_file()
    assert store.load_or_create() == lab
    assert not (root / ".lab.lock").exists()


def test_empty_first_use_constructor_has_no_io_and_never_seeds_capabilities(tmp_path: Path) -> None:
    root = tmp_path / "prototype"
    store = FileLabStore(root, seed_demo=False)

    assert not root.exists()
    lab = store.load_or_create()

    assert lab.name == "My Lab"
    assert lab.capabilities == []
    assert lab.version == 1
    assert FileLabStore(root).load_or_create() == lab
    assert FileLabStore(root, seed_demo=False).load_or_create() == lab
    assert not (root / ".lab.lock").exists()


def test_empty_first_use_mode_preserves_existing_lab_exactly(tmp_path: Path) -> None:
    seeded = FileLabStore(tmp_path)
    lab = seeded.load_or_create()
    lab.name = "Previously entered lab"
    lab.capabilities[0].price = Decimal("50.123456789")
    seeded.save(lab)
    before = seeded.path.read_bytes()

    reloaded = FileLabStore(tmp_path, seed_demo=False).load_or_create()

    assert reloaded.name == "Previously entered lab"
    assert reloaded.capabilities[0].price == Decimal("50.123456789")
    assert reloaded.capabilities
    assert reloaded.version == 2
    assert seeded.path.read_bytes() == before


def test_save_reload_preserves_ids_decimal_price_and_increments_version(tmp_path: Path) -> None:
    store = FileLabStore(tmp_path)
    lab = store.load_or_create()
    original_ids = [item.id for item in lab.capabilities]
    lab.name = "Updated lab"
    lab.capabilities[0].price = Decimal("0.00")
    store.save(lab)
    reloaded = FileLabStore(tmp_path).load_or_create()
    assert reloaded.name == "Updated lab"
    assert reloaded.version == 2
    assert lab.version == 1  # save does not secretly mutate the editor's object.
    assert [item.id for item in reloaded.capabilities] == original_ids
    assert reloaded.capabilities[0].price == Decimal("0.00")
    payload = json.loads(store.path.read_text())
    assert payload["capabilities"][0]["price"] == "0.00"


@pytest.mark.parametrize(
    "update",
    [
        {"canonical_test_id": "unknown"},
        {"method_ids": ["unknown"]},
        {"method_ids": ["iso_15184"]},
        {"price": Decimal("1"), "pricing_basis": None},
    ],
)
def test_invalid_edits_do_not_overwrite_saved_data(tmp_path: Path, update: dict) -> None:
    store = FileLabStore(tmp_path)
    lab = store.load_or_create()
    before = store.path.read_bytes()
    lab.capabilities[0] = lab.capabilities[0].model_copy(update=update)
    with pytest.raises(LabStoreError):
        store.save(lab)
    assert store.path.read_bytes() == before
    assert not (tmp_path / ".lab.lock").exists()


@pytest.mark.parametrize("corruption", ["{not JSON", '{"id": "demo_lab"}'])
def test_corrupt_data_is_not_reseeded_or_overwritten(tmp_path: Path, corruption: str) -> None:
    store = FileLabStore(tmp_path)
    lab = store.load_or_create()
    store.path.write_text(corruption)
    with pytest.raises(LabStoreError):
        store.load_or_create()
    with pytest.raises(LabStoreError):
        store.save(lab)
    assert store.path.read_text() == corruption


def test_semantically_corrupt_references_are_not_silently_fixed(tmp_path: Path) -> None:
    store = FileLabStore(tmp_path)
    store.load_or_create()
    payload = json.loads(store.path.read_text())
    payload["capabilities"][0]["canonical_test_id"] = "not_in_catalog"
    corrupted = json.dumps(payload)
    store.path.write_text(corrupted)
    with pytest.raises(LabStoreError, match="Unknown"):
        store.load_or_create()
    assert store.path.read_text() == corrupted


def test_stale_version_is_rejected(tmp_path: Path) -> None:
    first_store = FileLabStore(tmp_path)
    first = first_store.load_or_create()
    second_store = FileLabStore(tmp_path)
    second = second_store.load_or_create()
    first.name = "First update"
    first_store.save(first)
    second.name = "Stale update"
    with pytest.raises(LabVersionConflict, match="Reload"):
        second_store.save(second)
    assert first_store.load_or_create().name == "First update"


def test_lab_identity_cannot_be_replaced(tmp_path: Path) -> None:
    store = FileLabStore(tmp_path)
    lab = store.load_or_create()
    lab.id = "different_lab"
    with pytest.raises(LabStoreError, match="ID cannot be changed"):
        store.save(lab)


def test_symlinked_root_or_file_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(LabStoreError, match="symlink"):
        FileLabStore(linked_root).load_or_create()
    outside_file = tmp_path / "outside.json"
    outside_file.write_text("must not be overwritten")
    (real_root / "lab.json").symlink_to(outside_file)
    with pytest.raises(LabStoreError, match="symlink"):
        FileLabStore(real_root).load_or_create()
    assert outside_file.read_text() == "must not be overwritten"


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LabStoreError, match="traversal"):
        FileLabStore(tmp_path / "nested" / ".." / "lab").load_or_create()


def test_existing_lock_blocks_writes_without_resetting_data(tmp_path: Path) -> None:
    store = FileLabStore(tmp_path)
    store.load_or_create()
    before = store.path.read_bytes()
    lock = tmp_path / ".lab.lock"
    lock.write_text("another writer")
    with pytest.raises(LabStoreError, match="Another"):
        store.load_or_create()
    assert store.path.read_bytes() == before
    assert lock.read_text() == "another writer"


def test_failed_atomic_replace_preserves_previous_lab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileLabStore(tmp_path)
    lab = store.load_or_create()
    before = store.path.read_bytes()
    lab.name = "Unsaved edit"

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("scione.catalogs.lab_store.os.replace", fail_replace)
    with pytest.raises(LabStoreError, match="simulated replace failure"):
        store.save(lab)
    assert store.path.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["lab.json"]

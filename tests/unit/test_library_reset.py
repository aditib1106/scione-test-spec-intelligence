"""A fresh demo must preserve the SQL archive together with its source artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scione.config import Settings
from scione.library.reset import reset_library_workspace
from scione.library.store import LibraryStore
from scione.specifications.models import CustomerDocumentMetadata
from scione.workspace_reset import DemoResetError, reset_demo_workspace


def _settings(root: Path, database_url: str | None = None) -> Settings:
    return Settings(_env_file=None, scione_workspace_dir=root, scione_database_url=database_url)


def test_embedded_reset_preserves_queryable_archive_and_corresponding_files(tmp_path):
    root = tmp_path / "workspace"
    archive = LibraryStore(root)
    source = SimpleNamespace(
        document_id="a" * 16,
        original_name="customer.pdf",
        created_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    archive.ensure_document(source, CustomerDocumentMetadata(identifier="CSP-EN-301", revision="A"))
    original = archive.document(source.document_id)
    (root / "lab.json").write_bytes(b"saved lab")
    (root / "specifications").mkdir()
    (root / "specifications" / "original.pdf").write_bytes(b"original document bytes")
    (root / ".env").write_bytes(b"credentials stay in place")

    result = reset_library_workspace(_settings(root), archive)

    assert result.backup_path is not None
    assert "library.sqlite3" in result.moved_names
    assert not (root / "library.sqlite3").exists()
    assert not (root / "lab.json").exists()
    assert (root / ".env").read_bytes() == b"credentials stay in place"
    assert (
        result.backup_path / "specifications" / "original.pdf"
    ).read_bytes() == b"original document bytes"
    recovered = LibraryStore(result.backup_path)
    try:
        assert recovered.document(source.document_id) == original
    finally:
        recovered.close()
        archive.close()


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://demo:DO_NOT_PRINT@127.0.0.1/example",
        "sqlite:////some/other/location/archive.sqlite3",
    ],
)
def test_external_database_reset_is_rejected_before_any_file_or_connection_change(tmp_path, url):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "lab.json").write_bytes(b"saved lab")
    (root / "library.sqlite3").write_bytes(b"preserved local archive")

    with pytest.raises(DemoResetError, match="external database") as error:
        reset_library_workspace(_settings(root, url))

    assert "DO_NOT_PRINT" not in str(error.value)
    assert (root / "lab.json").read_bytes() == b"saved lab"
    assert (root / "library.sqlite3").read_bytes() == b"preserved local archive"
    assert not (tmp_path / ".demo-backups").exists()


def test_embedded_database_sidecars_are_preserved_with_main_database(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    originals = {
        "library.sqlite3": b"main database",
        "library.sqlite3-wal": b"write ahead log",
        "library.sqlite3-shm": b"shared memory",
        "library.sqlite3-journal": b"rollback journal",
    }
    for name, data in originals.items():
        (root / name).write_bytes(data)

    result = reset_demo_workspace(root)

    assert set(result.moved_names) == set(originals)
    assert result.backup_path is not None
    assert not list(root.iterdir())
    assert {name: (result.backup_path / name).read_bytes() for name in originals} == originals


def test_embedded_archive_move_failure_rolls_back_other_demo_artifacts(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "lab.json").write_bytes(b"lab state")
    (root / "library.sqlite3").write_bytes(b"archive state")
    rename = Path.rename

    def reject_database_move(source, target):
        if source.name == "library.sqlite3":
            raise OSError("Injected archive move failure")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", reject_database_move)
    with pytest.raises(DemoResetError, match="no files were deleted"):
        reset_library_workspace(_settings(root))
    assert (root / "lab.json").read_bytes() == b"lab state"
    assert (root / "library.sqlite3").read_bytes() == b"archive state"

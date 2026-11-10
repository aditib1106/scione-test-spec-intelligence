"""Offline migration acceptance: isolated databases, unchanged data and artifacts."""

import pytest
from sqlalchemy import create_engine, insert, inspect, select, text
from sqlalchemy.exc import OperationalError

from scione.config import Settings
from scione.database import (
    MigrationError,
    baseline,
    configured_engine,
    ensure_current,
    state,
    upgrade,
)
from scione.library.store import LibraryStore, LibraryStoreError


def test_empty_upgrade_and_restart(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with engine.connect() as connection:
        assert state(connection) == "empty"
    assert upgrade(engine) == "0001"
    assert upgrade(engine) == "0001"
    engine.dispose()
    restarted = create_engine(engine.url)
    ensure_current(restarted)
    with restarted.connect() as connection:
        assert state(connection) == "0001"
        assert len(inspect(connection).get_table_names()) == 10
    restarted.dispose()


def test_legacy_adoption_preserves_every_table_and_files(tmp_path):
    metadata = baseline()
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    metadata.create_all(engine)
    values = {
        "schema_versions": dict(component="library", version=1),
        "companies": dict(id="c", lookup_key="c", name="Company"),
        "specification_families": dict(id="f", company_id="c", title="title"),
        "specification_versions": dict(
            document_id="d",
            family_id="f",
            original_name="a.pdf",
            created_at="t",
            updated_at="t",
            version=7,
        ),
        "extraction_records": dict(
            run_id="r",
            document_id="d",
            normalizer_version="v1",
            created_at="t",
            source={"old": [1, None]},
        ),
        "requirements": dict(
            key="q",
            run_id="r",
            requirement_id="1",
            position=0,
            test_name="test",
            methods=["a"],
            raw={"evidence": "原文"},
        ),
        "requirement_claims": dict(
            key="k", requirement_key="q", position=0, kind="range", payload={"min": 1}
        ),
        "capability_revisions": dict(
            fingerprint="h",
            lab_id="lab",
            lab_version=9,
            created_at="t",
            payload={"historical": True},
        ),
        "comparison_runs": dict(
            comparison_id="x",
            document_id="d",
            input_fingerprint="fp",
            created_at="t",
            payload={"old": [False, None]},
            pdf=b"%PDF historical\x00\xff",
        ),
    }
    artifact = tmp_path / "original.pdf"
    artifact.write_bytes(b"%PDF original\x00\xff")
    historical = tmp_path / "comparison.json"
    historical.write_bytes(b'{"old": [false, null]}')
    before_files = {p.name: p.read_bytes() for p in (artifact, historical)}
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            connection.execute(insert(table).values(**values[table.name]))
        before = {t.name: connection.execute(select(t)).all() for t in metadata.sorted_tables}
    with pytest.raises(MigrationError, match="scione db upgrade"):
        ensure_current(engine)
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as connection:
        after = {t.name: connection.execute(select(t)).all() for t in metadata.sorted_tables}
    assert after == before
    assert {p.name: p.read_bytes() for p in (artifact, historical)} == before_files
    engine.dispose()


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE companies ADD COLUMN extra TEXT",
        "CREATE UNIQUE INDEX extra_unique ON companies (name)",
        "UPDATE schema_versions SET version=2",
        "DROP TABLE requirement_claims",
    ],
)
def test_legacy_drift_refused_without_stamp(change):
    engine = create_engine("sqlite://")
    frozen = baseline()
    frozen.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(frozen.tables["schema_versions"]).values(component="library", version=1)
        )
        connection.execute(text(change))
    with pytest.raises(MigrationError):
        upgrade(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()


@pytest.mark.parametrize("revision", ["9999", "unrecognized", None])
def test_unknown_revision_rejected(revision):
    engine = create_engine("sqlite://")
    upgrade(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        if revision:
            connection.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": revision})
    with pytest.raises(MigrationError):
        ensure_current(engine)
    with pytest.raises(MigrationError):
        upgrade(engine)


def test_status_does_not_create_workspace(tmp_path):
    root = tmp_path / "absent"
    settings = Settings(_env_file=None, scione_workspace_dir=root)
    assert configured_engine(settings, readonly=True) is None
    assert not root.exists()


def test_readonly_status_and_lazy_store(tmp_path):
    store = LibraryStore(tmp_path)
    assert not list(tmp_path.iterdir())
    store._db()
    store.close()
    settings = Settings(_env_file=None, scione_workspace_dir=tmp_path)
    engine = configured_engine(settings, readonly=True)
    with engine.connect() as connection:
        assert state(connection) == "0001"
        with pytest.raises(OperationalError):
            connection.execute(text("DELETE FROM schema_versions"))
    engine.dispose()


def test_connection_failure_hides_credentials(tmp_path):
    store = LibraryStore(tmp_path, "sqlite:////nonexistent-parent-scione/db.sqlite")
    with pytest.raises(LibraryStoreError, match="unavailable"):
        store._db()
    assert store._engine is None


@pytest.mark.parametrize("drift", ["type", "nullable", "primary", "foreign", "unique"])
def test_constraint_and_type_drift_refused(drift):
    from sqlalchemy import MetaData, PrimaryKeyConstraint, String, UniqueConstraint

    frozen = baseline()
    changed = MetaData()
    for table in frozen.sorted_tables:
        table.to_metadata(changed)
    table = changed.tables["specification_versions"]
    if drift == "type":
        table.c.version.type = String(40)
    elif drift == "nullable":
        table.c.version.nullable = True
    elif drift == "primary":
        table.constraints.remove(table.primary_key)
        table.c.document_id.primary_key = False
        table.append_constraint(PrimaryKeyConstraint("family_id"))
    elif drift == "foreign":
        for constraint in list(table.foreign_key_constraints):
            table.constraints.remove(constraint)
    elif drift == "unique":
        company = changed.tables["companies"]
        for constraint in list(company.constraints):
            if isinstance(constraint, UniqueConstraint):
                company.constraints.remove(constraint)
    engine = create_engine("sqlite://")
    changed.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(changed.tables["schema_versions"]).values(component="library", version=1)
        )
    with pytest.raises(MigrationError):
        upgrade(engine)
    assert "alembic_version" not in inspect(engine).get_table_names()


def test_db_cli_uses_settings_and_sanitizes_failure(tmp_path, monkeypatch, capsys):
    from scione.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCIONE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    assert main(["db", "status"]) == 0
    assert not (tmp_path / "workspace").exists()
    assert main(["db", "upgrade"]) == 0
    assert main(["db", "status"]) == 0
    assert "0001" in capsys.readouterr().out
    monkeypatch.setenv("SCIONE_DATABASE_URL", "invalid://user:secret-value@host/db")
    assert main(["db", "status"]) == 1
    assert "secret-value" not in capsys.readouterr().out


def test_sqlite_backup_restore_in_independent_workspace(tmp_path):
    import shutil
    import sqlite3

    original = tmp_path / "original"
    original.mkdir()
    path = original / "library.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    frozen = baseline()
    frozen.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(frozen.tables["schema_versions"]).values(component="library", version=1)
        )
        connection.execute(
            insert(frozen.tables["companies"]).values(
                id="historical-id", lookup_key="historical-key", name="Historical company"
            )
        )
    engine.dispose()
    (original / "history.pdf").write_bytes(b"%PDF saved evidence\x00\xff")
    (original / "history.json").write_bytes(b'{"version": 7, "old": null}')
    restored = tmp_path / "restored"
    restored.mkdir()
    with sqlite3.connect(path) as source, sqlite3.connect(restored / path.name) as destination:
        source.backup(destination)
    for name in ("history.pdf", "history.json"):
        shutil.copyfile(original / name, restored / name)
    copy = create_engine(f"sqlite:///{restored / path.name}")
    upgrade(copy)
    with copy.connect() as connection:
        assert state(connection) == "0001"
        assert connection.execute(select(frozen.tables["companies"])).one() == (
            "historical-id",
            "historical-key",
            "Historical company",
        )
    with engine.connect() as connection:
        assert state(connection) == "legacy-v1"
    for name in ("history.pdf", "history.json"):
        assert (restored / name).read_bytes() == (original / name).read_bytes()
    copy.dispose()
    engine.dispose()


def test_status_supports_existing_sqlite_uri(tmp_path):
    from pydantic import SecretStr

    path = tmp_path / "existing.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    upgrade(engine)
    engine.dispose()
    settings = Settings(
        _env_file=None,
        scione_database_url=SecretStr(f"sqlite:///file:{path}?uri=true"),
    )
    readonly = configured_engine(settings, readonly=True)
    with readonly.connect() as connection:
        assert state(connection) == "0001"
    readonly.dispose()

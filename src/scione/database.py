"""Explicit, credential-safe schema lifecycle for the library archive."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import quote, unquote

from alembic import command
from alembic.config import Config
from sqlalchemy import UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

HEAD = "0001"
MIGRATIONS = Path(__file__).with_name("migrations")


class MigrationError(RuntimeError):
    """A schema cannot safely be used or adopted."""


def baseline():
    spec = importlib.util.spec_from_file_location(
        "scione_baseline", MIGRATIONS / "versions" / "0001_baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.metadata


def compatible(connection):
    """Fail closed on structural drift, before stamping any legacy database."""
    expected = baseline()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    if tables != set(expected.tables) or inspector.get_view_names():
        raise MigrationError("Database tables differ from v1; restore or review before upgrading.")
    for name, table in expected.tables.items():
        columns = {c["name"]: c for c in inspector.get_columns(name)}
        if set(columns) != set(table.columns.keys()):
            raise MigrationError(f"Incompatible columns in {name}.")
        for column in table.columns:
            actual = columns[column.name]
            wanted = column.type.dialect_impl(connection.dialect)
            observed = actual["type"]
            if (
                observed.compile(dialect=connection.dialect).upper()
                != wanted.compile(dialect=connection.dialect).upper()
                or actual["nullable"] != column.nullable
                or actual.get("default") is not None
            ):
                raise MigrationError(f"Incompatible column definition in {name}.")
        pk = inspector.get_pk_constraint(name)["constrained_columns"]
        if pk != [c.name for c in table.primary_key.columns]:
            raise MigrationError(f"Incompatible primary key in {name}.")
        reflected_foreign = inspector.get_foreign_keys(name)
        if any(
            any(
                value not in (None, "RESTRICT", "NO ACTION")
                for value in f.get("options", {}).values()
            )
            or f.get("referred_schema") not in (None, inspector.default_schema_name)
            for f in reflected_foreign
        ) or inspector.get_check_constraints(name):
            raise MigrationError(f"Incompatible constraint behavior in {name}.")
        foreign = {
            (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
            for f in reflected_foreign
        }
        wanted_foreign = {
            (
                tuple(c.name for c in f.columns),
                f.referred_table.name,
                tuple(e.column.name for e in f.elements),
            )
            for f in table.foreign_key_constraints
        }
        unique = {tuple(u["column_names"]) for u in inspector.get_unique_constraints(name)}
        unique_indexes = [index for index in inspector.get_indexes(name) if index["unique"]]
        if any(index.get("dialect_options") for index in unique_indexes):
            raise MigrationError(f"Incompatible unique index in {name}.")
        unique.update(tuple(index["column_names"]) for index in unique_indexes)
        wanted_unique = {
            tuple(c.name for c in u.columns)
            for u in table.constraints
            if isinstance(u, UniqueConstraint)
        }
        if foreign != wanted_foreign or unique != wanted_unique:
            raise MigrationError(f"Incompatible constraints in {name}.")
    marker = expected.tables["schema_versions"]
    if connection.execute(select(marker)).all() != [("library", 1)]:
        raise MigrationError("Incompatible library version marker.")


def state(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not tables and not inspector.get_view_names():
        return "empty"
    if "alembic_version" not in tables:
        compatible(connection)
        return "legacy-v1"
    revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if revisions != [HEAD]:
        raise MigrationError(
            "Unknown or incomplete database revision; refuse to use this database."
        )
    compatible(connection)
    return HEAD


def upgrade(engine):
    with engine.begin() as connection:
        current = state(connection)
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS).replace("%", "%%"))
        config.attributes["connection"] = connection
        if current == "legacy-v1":
            command.stamp(config, HEAD)
        elif current == "empty":
            command.upgrade(config, "head")
        compatible(connection)
    return HEAD


def ensure_current(engine):
    with engine.connect() as connection:
        current = state(connection)
    if current == "empty":
        upgrade(engine)
    elif current != HEAD:
        raise MigrationError(
            "Existing database needs migration. Stop writers; run scione db upgrade."
        )


def configured_engine(settings, *, readonly=False):
    """Status never creates a missing SQLite file or workspace directory."""
    url = (
        settings.scione_database_url.get_secret_value()
        if settings.scione_database_url
        else f"sqlite:///{settings.scione_workspace_dir.absolute() / 'library.sqlite3'}"
    )
    parsed = make_url(url)
    sqlite_uri = str(parsed.query.get("uri", "false")).lower() in {"true", "1"}
    database = parsed.database
    if sqlite_uri and database and database.startswith("file:"):
        database = unquote(database[5:])
    memory = database in (None, "", ":memory:") or parsed.query.get("mode") == "memory"
    if parsed.get_backend_name() == "sqlite" and not memory:
        path = Path(database).absolute()
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise MigrationError("Database storage must not traverse a symlink.")
        if readonly:
            if not path.exists():
                return None
            url = parsed.set(
                database=f"file:{quote(path.as_posix(), safe='/')}",
                query={**parsed.query, "mode": "ro", "uri": "true"},
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
    options = (
        {} if parsed.get_backend_name() == "sqlite" else {"connect_args": {"connect_timeout": 5}}
    )
    return create_engine(url, **options)

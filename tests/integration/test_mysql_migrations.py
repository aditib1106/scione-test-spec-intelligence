"""Opt-in MySQL acceptance. URL MUST identify an empty, disposable test database."""

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

TEST_URL = os.environ.get("SCIONE_MIGRATION_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="No isolated MySQL migration test URL")


@pytest.mark.parametrize("scenario", ["empty", "legacy", "drift", "unknown"])
def test_mysql_migration_acceptance(scenario, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "migration_acceptance", Path(__file__).parents[1] / "unit/test_database_migrations.py"
    )
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    engine = create_engine(TEST_URL)
    assert engine.dialect.name == "mysql"
    assert not inspect(engine).get_table_names(), "Test database must be empty"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT VERSION()")).scalar().startswith("8.4.")
    monkeypatch.setattr(acceptance, "create_engine", lambda *a, **kw: create_engine(TEST_URL))
    try:
        if scenario == "empty":
            acceptance.test_empty_upgrade_and_restart(tmp_path)
        elif scenario == "legacy":
            acceptance.test_legacy_adoption_preserves_every_table_and_files(tmp_path)
        elif scenario == "drift":
            acceptance.test_legacy_drift_refused_without_stamp(
                "UPDATE schema_versions SET version=2"
            )
        else:
            acceptance.test_unknown_revision_rejected("9999")
    finally:
        # Only reached after the empty-database guard above. Never use a business URL.
        with engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in inspect(connection).get_table_names():
                connection.exec_driver_sql(
                    f"DROP TABLE {connection.dialect.identifier_preparer.quote(table)}"
                )
            connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        engine.dispose()

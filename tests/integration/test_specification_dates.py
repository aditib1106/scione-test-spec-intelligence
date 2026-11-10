"""Customer-document dates identify saved versions without replacing provenance."""

import pytest
from sqlalchemy import create_engine, inspect, select, text
from test_library_api import _metadata, _seed, client  # noqa: F401

from scione.library.store import LibraryStore, schema_versions

__all__ = ["client"]


def test_library_v1_migration_adds_unknown_date_without_changing_legacy_rows(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "library.sqlite3"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE schema_versions (component VARCHAR(64) PRIMARY KEY, "
                "version INTEGER NOT NULL)"
            )
        )
        connection.execute(text("INSERT INTO schema_versions VALUES ('library', 1)"))
        connection.execute(
            text(
                "CREATE TABLE specification_families (id VARCHAR(32) PRIMARY KEY, "
                "company_id VARCHAR(32), specification_id VARCHAR(300), title VARCHAR(500))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO specification_families "
                "VALUES ('family', NULL, 'LEGACY-001', 'Legacy spec')"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE specification_versions (document_id VARCHAR(16) PRIMARY KEY, "
                "family_id VARCHAR(32) NOT NULL, original_name VARCHAR(500) NOT NULL, "
                "revision VARCHAR(300), created_at VARCHAR(40) NOT NULL, "
                "updated_at VARCHAR(40) NOT NULL, version INTEGER NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO specification_versions VALUES ('0123456789abcdef', 'family', "
                "'legacy.pdf', 'Issue B', '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z', 7)"
            )
        )
    engine.dispose()
    for _ in range(2):
        store = LibraryStore(tmp_path)
        try:
            row = store.document("0123456789abcdef")
            assert row["specification_date"] is None
            assert row["revision"] == "Issue B" and row["version"] == 7
            with store.engine.connect() as connection:
                version = connection.execute(
                    select(schema_versions.c.version).where(
                        schema_versions.c.component == "library"
                    )
                ).scalar_one()
            assert version == 2
            columns = inspect(store.engine).get_columns("specification_versions")
            assert sum(column["name"] == "specification_date" for column in columns) == 1
        finally:
            store.close()


def test_two_dated_versions_keep_their_own_dates_and_original_revisions(client):
    http, root = client
    first, _ = _seed(root, "Issue A")
    second, _ = _seed(root, "Issue B")
    first_path, second_path = (
        f"/api/library/{first.document_id}",
        f"/api/library/{second.document_id}",
    )
    original = http.get(first_path).json()["document"]
    assert original["specification_date"] is None
    saved = http.put(
        first_path + "/metadata",
        json=_metadata(
            original,
            specification_date="2026-10-29",
        ),
    )
    assert saved.status_code == 200, saved.text
    first_document = saved.json()["document"]
    second_document = http.get(second_path).json()["document"]
    linked = http.put(
        second_path + "/metadata",
        json=_metadata(
            second_document,
            family_id=first_document["family_id"],
            specification_date="2026-11-04",
        ),
    )
    assert linked.status_code == 200, linked.text
    versions = {row["document_id"]: row for row in linked.json()["versions"]}
    assert len(versions) == 2
    assert versions[first.document_id]["specification_date"] == "2026-10-29"
    assert versions[second.document_id]["specification_date"] == "2026-11-04"
    assert versions[first.document_id]["revision"] == "Issue A"
    assert versions[second.document_id]["revision"] == "Issue B"
    assert versions[first.document_id]["family_id"] == versions[second.document_id]["family_id"]


def test_old_metadata_clients_preserve_date_explicit_null_clears_and_stale_edit_conflicts(client):
    http, root = client
    source, _ = _seed(root)
    path = f"/api/library/{source.document_id}"
    initial = http.get(path).json()["document"]
    saved = http.put(
        path + "/metadata",
        json=_metadata(
            initial,
            specification_date="2026-11-01",
        ),
    )
    assert saved.status_code == 200
    current = saved.json()["document"]
    old_client = http.put(path + "/metadata", json=_metadata(current, title="Renamed only"))
    assert old_client.status_code == 200, old_client.text
    assert old_client.json()["document"]["specification_date"] == "2026-11-01"
    stale = http.put(path + "/metadata", json=_metadata(current, specification_date="2026-11-02"))
    assert stale.status_code == 409
    fresh = old_client.json()["document"]
    cleared = http.put(path + "/metadata", json=_metadata(fresh, specification_date=None))
    assert cleared.status_code == 200
    assert cleared.json()["document"]["specification_date"] is None
    assert cleared.json()["document"]["revision"] == "Issue A"


@pytest.mark.parametrize("value", ["Issue A", "2026-02-30", "2026-11-01T00:00:00Z", "11/01/2026"])
def test_invalid_specification_dates_are_rejected_without_touching_metadata(client, value):
    http, root = client
    source, _ = _seed(root)
    path = f"/api/library/{source.document_id}"
    initial = http.get(path).json()["document"]
    response = http.put(path + "/metadata", json=_metadata(initial, specification_date=value))
    assert response.status_code == 422
    current = http.get(path).json()["document"]
    assert current["version"] == initial["version"] and current["specification_date"] is None

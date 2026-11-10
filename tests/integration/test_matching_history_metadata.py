"""History and cache lookup must not sort whole model artifacts in SQL."""

import re

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from test_semantic_matching_api import body_for, setup  # noqa: F401

__all__ = ["setup"]


def test_large_artifacts_are_not_selected_in_sorted_history_or_cache_queries(setup):
    client, workspace, doc, provider, _settings = setup
    result = client.post(f"/api/specifications/{doc}/matching", json=body_for(client, doc))
    assert result.status_code == 200, result.text
    run_id = result.json()["run_id"]
    engine = workspace.library.store.engine
    sorted_queries = []

    def mysql_large_json_sort_limit(_conn, _cursor, statement, parameters, _context, _many):
        sql = statement.casefold()
        if "semantic_matching_runs" not in sql or "order by" not in sql:
            return
        sorted_queries.append(statement)
        projection = re.split(r"\bfrom\b", sql, maxsplit=1)[0]
        if "semantic_matching_runs.payload" in projection:
            # MySQL1038 was reproduced against the real existing lab database.
            # SQLite has no equivalent per-sort JSON buffer limit.
            raise OperationalError(statement, parameters, RuntimeError("1038: sort memory"))

    event.listen(engine, "before_cursor_execute", mysql_large_json_sort_limit)
    try:
        # Exercise the application's own store as well as the HTTP app's
        # connection pool, which is separate in this integration fixture.
        history = workspace.matching.store.listing(doc)
        cached = workspace.matching.store.latest_completed(
            doc, workspace.matching.store.load(doc, run_id).input_fingerprint
        )
        assert cached is not None and cached.run_id == run_id
        assert history[0]["run_id"] == run_id
        assert "payload" not in history[0]
        assert sorted_queries
        assert provider.calls == 1
    finally:
        event.remove(engine, "before_cursor_execute", mysql_large_json_sort_limit)


def test_query_limit_error_has_safe_diagnostics_without_claiming_database_is_down(
    setup, monkeypatch, caplog
):
    from scione.matching.workspace import MatchingWorkspace

    client, _workspace, doc, provider, _settings = setup

    class DriverError(Exception):
        pass

    def fail(_self, _document):
        raise OperationalError(
            "SELECT private_statement",
            {"customer": "private_parameter"},
            DriverError(1038, "Out of sort memory"),
        )

    monkeypatch.setattr(MatchingWorkspace, "overview", fail)
    response = client.get(f"/api/specifications/{doc}/matching")
    assert response.status_code == 503
    assert "history could not be loaded" in response.json()["detail"]
    assert "Check the database service" not in response.json()["detail"]
    reference = response.json()["error_reference"]
    assert reference in caplog.text and "driver_code=1038" in caplog.text
    for private in ("private_statement", "private_parameter"):
        assert private not in response.text and private not in caplog.text
    assert provider.calls == 0

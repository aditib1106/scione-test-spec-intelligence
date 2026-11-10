"""PDF resource failures remain controlled without blocking saved web/JSON reports."""

import importlib

from test_workspace_api import client as workspace_client
from test_workspace_api import seed_source

from scione.structured.pdf import ComparisonPDFError

client = workspace_client


def test_pdf_limit_is_controlled_and_does_not_break_saved_comparison(client, monkeypatch):
    http, root = client
    source, _run, _lab = seed_source(root)
    before = http.get(f"/api/comparisons/{source.document_id}")
    assert before.status_code == 200
    comparison_id = before.json()["comparison_id"]
    app = importlib.import_module("scione.api.app")

    def limit(_payload):
        raise ComparisonPDFError(
            "Unable to create the comparison PDF within the 20-second rendering limit. "
            "The web report and JSON remain available; no partial PDF was saved."
        )

    monkeypatch.setattr(app, "render_comparison_pdf", limit)
    failed = http.get(f"/api/comparison-snapshots/{comparison_id}/pdf")
    assert failed.status_code == 400
    assert "20-second rendering limit" in failed.json()["detail"]
    assert failed.headers["content-type"].startswith("application/json")
    after = http.get(f"/api/comparisons/{source.document_id}")
    assert after.status_code == 200 and after.json() == before.json()
    assert http.get("/api/library").status_code == 200

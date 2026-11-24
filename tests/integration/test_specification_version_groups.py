"""Related dated PDF versions share an identity, not extraction or review state."""

from __future__ import annotations

from test_library_api import _metadata, _seed
from test_library_api import client as client


def test_related_versions_preserve_older_source_reviews_exports_and_company_boundaries(client):
    http, root = client
    first, first_run = _seed(root, "Issue A")
    first_path = f"/api/library/{first.document_id}"
    first_detail = http.get(first_path).json()
    assigned = http.put(
        first_path + "/metadata",
        json=_metadata(
            first_detail["document"],
            specification_date="2026-11-01",
        ),
    )
    assert assigned.status_code == 200, assigned.text
    first_record = assigned.json()["document"]
    before_source = http.get(f"/api/specifications/{first.document_id}").json()
    original_pdf = http.get(f"/api/specifications/{first.document_id}/pdf").content
    report = http.get(f"/api/comparisons/{first.document_id}").json()
    reviewed = http.post(
        f"/api/comparisons/{first.document_id}/reviews",
        json={
            "comparison_id": report["comparison_id"],
            "input_fingerprint": report["report_input_fingerprint"],
            "requirement_id": "req_gloss",
            "expected_version": 0,
            "review_status": "accepted",
            "decision": "keep",
            "note": "Reviewed the earlier customer version.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    frozen_id = reviewed.json()["export_snapshot_id"]
    export_path = f"/api/comparison-snapshots/{frozen_id}"
    frozen_json = http.get(export_path + "/json").json()
    frozen_pdf = http.get(export_path + "/pdf").content
    original_history = http.get(f"/api/comparisons/{first.document_id}/reviews").json()

    second, second_run = _seed(root, "Issue B")
    second_path = f"/api/library/{second.document_id}"
    second_detail = http.get(second_path).json()
    linked = http.put(
        second_path + "/metadata",
        json=_metadata(
            second_detail["document"],
            family_id=first_record["family_id"],
            specification_date="2026-11-24",
        ),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["document"]["family_id"] == first_record["family_id"]
    versions = linked.json()["versions"]
    assert {row["document_id"] for row in versions} == {first.document_id, second.document_id}
    assert {row["specification_date"] for row in versions} == {"2026-11-01", "2026-11-24"}
    assert first_run.run_id != second_run.run_id
    assert first.document_id != second.document_id
    assert http.get(first_path).json()["document"] == first_record
    assert http.get(f"/api/specifications/{first.document_id}").json() == before_source
    assert http.get(f"/api/specifications/{first.document_id}/pdf").content == original_pdf
    assert http.get(f"/api/comparisons/{first.document_id}/reviews").json() == original_history
    assert http.get(export_path + "/json").json() == frozen_json
    assert http.get(export_path + "/pdf").content == frozen_pdf

    # A family cannot absorb another customer's version, even with a known ID.
    third, _third_run = _seed(root, "Issue C")
    third_path = f"/api/library/{third.document_id}"
    third_record = http.get(third_path).json()["document"]
    foreign = http.put(
        third_path + "/metadata",
        json=_metadata(
            third_record,
            company_name="Different customer",
            family_id=first_record["family_id"],
        ),
    )
    assert foreign.status_code == 409
    assert http.get(third_path).json()["document"] == third_record
    stale = http.put(
        first_path + "/metadata",
        json=_metadata(
            first_record,
            expected_version=first_record["version"] - 1,
            title="A stale editor must not rename the family.",
        ),
    )
    assert stale.status_code == 409
    assert http.get(first_path).json()["document"] == first_record

    removed = http.request(
        "DELETE",
        second_path,
        json={
            "version": linked.json()["document"]["version"],
        },
    )
    assert removed.status_code == 200, removed.text
    listing = http.get("/api/library").json()["documents"]
    family = [row for row in listing if row["family_id"] == first_record["family_id"]]
    assert [row["document_id"] for row in family] == [first.document_id]
    assert http.get(f"/api/specifications/{first.document_id}").json() == before_source
    assert http.get(export_path + "/json").json() == frozen_json
    assert http.get(export_path + "/pdf").content == frozen_pdf
    assert http.get(f"/api/specifications/{second.document_id}/pdf").status_code == 200

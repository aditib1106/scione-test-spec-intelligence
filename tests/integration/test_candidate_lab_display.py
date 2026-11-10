"""Rejected semantic links can show recorded candidates without declaring support."""

from test_semantic_matching_api import body_for, setup  # noqa: F401

__all__ = ["setup"]


def test_scope_mismatch_shows_captured_lab_candidate_but_keeps_unknown_status(setup):
    client, workspace, document_id, provider, _settings = setup
    lab = workspace.labs.load_or_create()
    lab.capabilities[0].atomic_claims[0].source_context = "Nitrogen atmosphere"
    workspace.labs.save(lab)
    response = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert response.status_code == 200, response.text
    result = client.get(f"/api/comparisons/{document_id}").json()
    checks = result["requirements"][0]["comparison"]["checks"]
    temperature = next(check for check in checks if check["parameter_id"] == "temperature")
    assert temperature["status"] == "unknown"
    assert temperature["declared"] is None
    entries = temperature["candidate_lab_entries"]
    assert len(entries) == 1
    assert entries[0]["claim_id"] == "lab_temp"
    assert entries[0]["context"] == "Nitrogen atmosphere"
    assert entries[0]["confirmed"] is False
    assert entries[0]["value"] == "20–30 °C"
    assert temperature["capability_claim_ids"] == []
    assert provider.calls == 1

    # A later lab edit invalidates the attempt and cannot leak its old candidates
    # into the rules-only comparison, even though the document is unchanged.
    changed = workspace.labs.load_or_create()
    changed.capabilities[0].atomic_claims[0].source_context = None
    workspace.labs.save(changed)
    refreshed = client.get(f"/api/comparisons/{document_id}").json()
    assert refreshed["comparison_engine"] == "deterministic_rules"
    assert not any(
        check.get("candidate_lab_entries")
        for row in refreshed["requirements"]
        for check in row["comparison"]["checks"]
    )

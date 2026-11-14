"""Source-backed omissions can be recovered without altering original model artifacts."""

from copy import deepcopy
from uuid import uuid4

from test_semantic_matching_api import body_for, setup  # noqa: F401
from test_workspace_api import LocalHTTPClient

from scione.api.app import create_app

__all__ = ["setup"]


def addition_body(client, document_id):
    data = client.get(f"/api/specifications/{document_id}").json()
    requirement = deepcopy(data["run"]["extraction"]["requirements"][0])
    requirement["id"] = "req_manual_draft"
    requirement["original_test_name"] = "Recovered source test"
    return {
        "run_id": data["run"]["run_id"],
        "expected_version": data["facts_version"],
        "expected_review_version": data["review"]["version"],
        "requirement": requirement,
        "note": "Recovered a missed item after inspecting its source.",
    }


def test_addition_is_audited_reviewable_and_flows_into_matching_and_archive(setup):
    client, workspace, document_id, _provider, settings = setup
    original = workspace.specs.load_latest_extraction(document_id).model_dump(mode="json")
    body = addition_body(client, document_id)
    response = client.post(f"/api/specifications/{document_id}/requirements", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    added = data["run"]["extraction"]["requirements"][-1]
    identifier = added["id"]
    assert identifier.startswith("req_human_") and identifier != "req_manual_draft"
    assert data["facts_version"] == 1
    assert identifier in data["facts_revision"]["changed_requirement_ids"]
    assert added["atomic_claims"] == []
    assert workspace.specs.load_latest_extraction(document_id).model_dump(mode="json") == original
    confirmed = client.post(
        f"/api/specifications/{document_id}/review",
        json={
            "run_id": body["run_id"],
            "version": data["review"]["version"],
            "facts_version": data["facts_version"],
            "requirement_ids": [identifier],
            "decision": "confirmed",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["assessments"][identifier]["state"] == "confirmed"
    assert any(
        item["requirement_id"] == identifier
        for item in workspace.library.detail(document_id)["requirements"]
    )
    assert len(workspace.draft(document_id).source_run.extraction.requirements) == 2
    assert workspace.overview()["documents"][0]["requirement_count"] == 2
    matched = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert matched.status_code == 200, matched.text
    artifact = workspace.matching.artifacts(document_id, matched.json()["run_id"])
    assert len(artifact["effective_requirements_at_matching"]) == 2
    assert len(artifact["extraction_machine"]["extraction"]["requirements"]) == 1
    reloaded = LocalHTTPClient(create_app(settings))
    assert (
        reloaded.get(f"/api/specifications/{document_id}").json()["assessments"][identifier][
            "state"
        ]
        == "confirmed"
    )


def test_addition_rejects_stale_versions_duplicate_or_missing_source_without_mutation(setup):
    client, workspace, document_id, _provider, _settings = setup
    body = addition_body(client, document_id)
    body["requirement"]["evidence"][0]["quote"] = "An invented request, absent from the PDF."
    assert (
        client.post(f"/api/specifications/{document_id}/requirements", json=body).status_code == 400
    )
    assert workspace.facts.latest(document_id, body["run_id"]) is None
    body = addition_body(client, document_id)
    body["expected_review_version"] -= 1
    assert (
        client.post(f"/api/specifications/{document_id}/requirements", json=body).status_code == 409
    )
    body = addition_body(client, document_id)
    assert (
        client.post(f"/api/specifications/{document_id}/requirements", json=body).status_code == 200
    )
    assert (
        client.post(f"/api/specifications/{document_id}/requirements", json=body).status_code == 409
    )
    body["expected_version"] += 1
    assert (
        client.post(f"/api/specifications/{document_id}/requirements", json=body).status_code == 400
    )
    assert workspace.facts.latest(document_id, body["run_id"]).version == 1


def test_unknown_human_identifier_cannot_be_confirmed(setup):
    client, _workspace, document_id, _provider, _settings = setup
    data = client.get(f"/api/specifications/{document_id}").json()
    response = client.post(
        f"/api/specifications/{document_id}/review",
        json={
            "run_id": data["run"]["run_id"],
            "version": data["review"]["version"],
            "requirement_ids": ["req_human_not_saved"],
            "decision": "confirmed",
        },
    )
    assert response.status_code == 400


def test_empty_model_result_can_receive_a_manual_source_test(setup):
    client, workspace, document_id, _provider, _settings = setup
    body = addition_body(client, document_id)
    machine = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    machine.run_id = str(uuid4())
    machine.extraction.requirements = []
    workspace.specs.save_extraction(document_id, machine)
    body.update(run_id=machine.run_id, expected_version=0, expected_review_version=0)
    result = client.post(f"/api/specifications/{document_id}/requirements", json=body)
    assert result.status_code == 200, result.text
    assert len(workspace.draft(document_id).source_run.extraction.requirements) == 1
    assert workspace.specs.load_latest_extraction(document_id).extraction.requirements == []


def test_field_highlights_stay_with_effective_source_item_and_validate_field_selector(setup):
    client, _workspace, document_id, _provider, _settings = setup
    path = f"/api/specifications/{document_id}/pages/1?requirement_id=req_pencil"
    selected = client.get(path + "&field=method")
    assert selected.status_code == 200
    assert selected.headers["content-type"] == "image/png"
    assert selected.headers["x-highlight-scope"] == "field"
    assert 0 <= float(selected.headers["x-highlight-focus"]) <= 1
    assert selected.content.startswith(b"\x89PNG")
    assert client.get(path + "&field=criterion").headers["x-highlight-scope"] == "item"
    assert client.get(path + "&field=untrusted_field").status_code == 422
    assert (
        client.get(path.replace("req_pencil", "req_unknown") + "&field=method").status_code == 404
    )

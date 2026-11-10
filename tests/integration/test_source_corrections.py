"""Reviewed source facts drive matching without rewriting machine evidence."""

from copy import deepcopy
from uuid import uuid4

from test_semantic_matching_api import body_for, setup  # noqa: F401
from test_workspace_api import LocalHTTPClient

__all__ = ["setup"]

from scione.api.app import create_app
from scione.api.workspace import Workspace
from scione.library.store import digest
from scione.reports.matching import lab_fingerprint
from scione.reports.models import MappingChoice
from scione.structured import normalize_requirement
from scione.structured.models import AtomicClaim


def edit_body(client, document_id, name="Pencil hardness, customer-defined scope"):
    data = client.get(f"/api/specifications/{document_id}").json()
    requirements = deepcopy(data["run"]["extraction"]["requirements"])
    requirements[0]["original_test_name"] = name
    return {
        "run_id": data["run"]["run_id"],
        "expected_version": data["facts_version"],
        "expected_review_version": data["review"]["version"],
        "requirements": requirements,
        "note": "Checked the original PDF and corrected the test label.",
    }


def test_corrected_facts_are_used_by_library_report_and_matching_while_raw_stays_immutable(setup):
    client, workspace, document_id, provider, settings = setup
    first = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    ).json()
    machine = workspace.specs.load_latest_extraction(document_id).model_dump(mode="json")
    body = edit_body(client, document_id)
    result = client.post(f"/api/specifications/{document_id}/facts", json=body)
    assert result.status_code == 200, result.text
    corrected = result.json()
    assert corrected["facts_version"] == 1
    assert "req_pencil" not in corrected["review"]["decisions"]
    assert workspace.specs.load_latest_extraction(document_id).model_dump(mode="json") == machine
    detail = workspace.library.detail(document_id)
    assert detail["requirements"][0]["test_name"] == body["requirements"][0]["original_test_name"]
    assert (
        workspace.draft(document_id).source_facts_revision_id
        == corrected["facts_revision"]["revision_id"]
    )
    assert (
        client.get(f"/api/specifications/{document_id}/matching").json()["current_run_id"] is None
    )
    second = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert second.status_code == 200, second.text
    artifacts = workspace.matching.artifacts(document_id, second.json()["run_id"])
    assert artifacts["manifest"]["human_corrections_included"] is True
    assert (
        artifacts["extraction_machine"]["extraction"]["requirements"][0]["original_test_name"]
        == "Pencil hardness"
    )
    assert (
        artifacts["effective_requirements_at_matching"][0]["original_test_name"]
        == body["requirements"][0]["original_test_name"]
    )
    assert (
        workspace.matching.artifacts(document_id, first["run_id"])["manifest"][
            "human_corrections_included"
        ]
        is False
    )
    reloaded = LocalHTTPClient(create_app(settings))
    assert reloaded.get(f"/api/specifications/{document_id}").json()["facts_version"] == 1
    assert provider.calls == 2


def test_correction_and_confirmation_both_require_current_facts_and_review_versions(setup):
    client, _workspace, document_id, _provider, _settings = setup
    body = edit_body(client, document_id)
    assert client.post(f"/api/specifications/{document_id}/facts", json=body).status_code == 200
    assert client.post(f"/api/specifications/{document_id}/facts", json=body).status_code == 409
    review = {
        "run_id": body["run_id"],
        "version": body["expected_review_version"],
        "requirement_ids": ["req_pencil"],
        "decision": "confirmed",
        "note": "Checked corrected source",
    }
    assert client.post(f"/api/specifications/{document_id}/review", json=review).status_code == 409
    reviewed = client.post(
        f"/api/specifications/{document_id}/review", json={**review, "facts_version": 1}
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["assessments"]["req_pencil"]["state"] == "confirmed"
    stale = edit_body(client, document_id, name="Another correction")
    stale["expected_review_version"] -= 1
    assert client.post(f"/api/specifications/{document_id}/facts", json=stale).status_code == 409


def test_invalid_quotation_or_changed_ids_do_not_create_a_revision(setup):
    client, workspace, document_id, _provider, _settings = setup
    body = edit_body(client, document_id)
    body["requirements"][0]["evidence"][0]["quote"] = "This quotation was never in the PDF."
    assert client.post(f"/api/specifications/{document_id}/facts", json=body).status_code == 400
    body = edit_body(client, document_id)
    body["requirements"][0]["id"] = "req_other"
    assert client.post(f"/api/specifications/{document_id}/facts", json=body).status_code == 400
    assert workspace.facts.latest(document_id, body["run_id"]) is None


def test_reanalysis_does_not_inherit_old_corrections_or_approvals(setup):
    client, workspace, document_id, _provider, settings = setup
    assert (
        client.post(
            f"/api/specifications/{document_id}/facts", json=edit_body(client, document_id)
        ).status_code
        == 200
    )
    run = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    run.run_id = str(uuid4())
    workspace.specs.save_extraction(document_id, run)
    response = client.get(f"/api/specifications/{document_id}").json()
    assert response["facts_version"] == 0
    assert response["review"]["decisions"] == {}
    assert (
        response["run"]["extraction"]["requirements"][0]["original_test_name"] == "Pencil hardness"
    )
    # Saved older facts remain addressable by their source run in the same DB.
    second = Workspace(settings)
    assert second.facts.latest(document_id, run.run_id) is None
    second.library.store.close()


def test_numeric_source_correction_is_reparsed_and_stale_mapping_is_inactive(setup):
    client, workspace, document_id, _provider, _settings = setup
    run = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].conditions[0].raw_value = "230"
    workspace.specs.save_extraction(document_id, run)
    report = workspace.draft(document_id)
    saved = report.workspace.model_copy(deep=True)
    saved.mappings["req_pencil"] = MappingChoice(
        canonical_test_id="pencil_hardness",
        capability_id="cap_pencil",
        selected_method_codes=["ISO 15184"],
        capability_verdict="supported",
        lab_fingerprint=lab_fingerprint(report.lab),
        source_requirement_fingerprint=digest(
            run.extraction.requirements[0].model_dump(mode="json")
        ),
        note="Earlier scope decision, before source correction.",
    )
    workspace.reports.save_workspace(saved, expected_version=0)
    body = edit_body(client, document_id, name="Pencil hardness")
    body["requirements"][0]["conditions"][0]["raw_value"] = "23"
    updated = client.post(f"/api/specifications/{document_id}/facts", json=body)
    assert updated.status_code == 200, updated.text
    effective = workspace.draft(document_id)
    assert "req_pencil" not in effective.workspace.mappings
    assert "req_pencil" in workspace.reports.load_workspace(document_id, run.run_id).mappings
    corrected = normalize_requirement(effective.source_run.extraction.requirements[0])
    temperature = next(claim for claim in corrected.claims if claim.parameter_id == "temperature")
    assert str(temperature.value) == "23"
    assert (
        workspace.specs.load_extraction(document_id, run.run_id)
        .extraction.requirements[0]
        .conditions[0]
        .raw_value
        == "230"
    )


def test_changing_one_row_keeps_another_rows_source_confirmation(setup):
    client, workspace, document_id, _provider, _settings = setup
    run = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    run.run_id = str(uuid4())
    second = run.extraction.requirements[0].model_copy(deep=True)
    second.id = "req_other"
    run.extraction.requirements.append(second)
    workspace.specs.save_extraction(document_id, run)
    workspace.specs.save_reviews(document_id, run.run_id, ["req_pencil", "req_other"], "confirmed")
    body = edit_body(client, document_id)
    response = client.post(f"/api/specifications/{document_id}/facts", json=body)
    assert response.status_code == 200, response.text
    assert set(response.json()["review"]["decisions"]) == {"req_other"}


def test_library_detail_captures_one_run_during_concurrent_reanalysis(setup, monkeypatch):
    _client, workspace, document_id, _provider, _settings = setup
    first = workspace.specs.load_latest_extraction(document_id)
    second = first.model_copy(deep=True)
    second.run_id = str(uuid4())
    workspace.specs.save_extraction(document_id, second)
    requirements = [row.model_copy(deep=True) for row in second.extraction.requirements]
    requirements[0].original_test_name = "Corrected second run"
    workspace.facts.save(
        second, requirements, "Correction belongs only to second run", expected_version=0
    )
    reads = 0

    def changing_latest(_document_id):
        nonlocal reads
        reads += 1
        return first if reads == 1 else second

    monkeypatch.setattr(workspace.specs, "load_latest_extraction", changing_latest)
    detail = workspace.library.detail(document_id)
    assert detail["document"]["run_id"] == first.run_id
    assert detail["facts_revision_id"] is None
    assert detail["requirements"][0]["test_name"] == "Pencil hardness"


def test_explicit_parameter_rebuild_keeps_reviewed_text_and_original_model_atoms(setup):
    client, workspace, document_id, provider, _settings = setup
    machine = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    machine.run_id = str(uuid4())
    machine.extraction.requirements[0].atomic_claims = [AtomicClaim(
        id="bad_model_identity", parameter_id="specimen_length", label="Temperature",
        operator="=", value=23, unit="°C", raw_text="operating temperature 23 degrees C",
        source_pages=[1],
    )]
    workspace.specs.save_extraction(document_id, machine)
    before = client.get(f"/api/specifications/{document_id}").json()
    reviewed = deepcopy(before["run"]["extraction"]["requirements"])
    reviewed[0]["atomic_claims"] = []
    response = client.post(f"/api/specifications/{document_id}/facts", json={
        "run_id": machine.run_id,
        "expected_version": before["facts_version"],
        "expected_review_version": before["review"]["version"],
        "requirements": reviewed,
        "note": "Rebuild detailed parameters from the checked source text.",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    effective = data["run"]["extraction"]["requirements"][0]
    assert effective["atomic_claims"] == []
    assert effective["conditions"] == before["run"]["extraction"]["requirements"][0]["conditions"]
    effective_run, _, _ = workspace.effective_source(document_id)
    normalized = normalize_requirement(effective_run.extraction.requirements[0])
    assert any(
        claim.parameter_id == "temperature" and str(claim.value) == "23"
        for claim in normalized.claims
    )
    assert not any(claim.parameter_id == "specimen_length" for claim in normalized.claims)
    assert workspace.specs.load_extraction(document_id, machine.run_id) == machine
    assert data["facts_revision"]["changed_requirement_ids"] == ["req_pencil"]
    assert provider.calls == 0

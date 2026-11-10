"""Real HTTP/domain/SQL flow with deterministic model fixtures and zero network."""

import importlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from test_workspace_api import LocalHTTPClient, make_pdf

from scione.api.app import create_app
from scione.api.workspace import Workspace
from scione.catalogs.models import LabCapability
from scione.config import Settings
from scione.library.store import digest
from scione.matching.models import MatchingInput
from scione.parameters import REGISTRY_VERSION
from scione.providers.base import StructuredGenerationResponse
from scione.providers.static import StaticModelProvider
from scione.specifications.models import CustomerExtractionRun
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim

SOURCE = "Pencil hardness ISO 15184: operating temperature 23 degrees C."


class MatchingProvider(StaticModelProvider):
    def __init__(self):
        super().__init__({}, model_name="fixture-alias")
        self.calls = 0
        self.invalid = False

    def generate_structured(self, request):
        self.calls += 1
        snapshot = MatchingInput.model_validate_json(request.user_prompt)
        result = {
            "schema_version": "0.1",
            "review_issues": [],
            "items": [
                {
                    "requirement_id": row.requirement.id,
                    "identity_status": "aligned",
                    "canonical_test_id": "pencil_hardness",
                    "capability_id": "cap_pencil",
                    "reason": "The catalog method and lab declaration identify this test.",
                    "claim_alignments": [
                        {
                            "requirement_claim_id": claim.id,
                            "capability_claim_ids": [
                                "outside_candidate" if self.invalid else "lab_temp"
                            ],
                            "relation": "same_parameter",
                            "reason": "Both claims describe temperature.",
                        }
                        for claim in row.normalized.claims
                    ],
                }
                for row in snapshot.requirements
            ],
        }
        return StructuredGenerationResponse(
            provider="static",
            model="fixture-resolved-revision",
            raw_text=json.dumps(result),
            parsed=result,
            latency_ms=1,
            usage={"input_tokens": 100, "output_tokens": 50},
        )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path, groq_api_key=None, gemini_api_key=None
    )
    workspace = Workspace(settings)
    source = workspace.specs.import_pdf("synthetic.pdf", make_pdf(SOURCE))
    extraction = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": source.document_id,
            "prompt_version": "offline_fixture",
            "schema_version": "0.1",
            "provider": "static",
            "model": "fixture",
            "latency_ms": 1,
            "usage": {},
            "raw_response": "original extraction fixture",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Synthetic spec"},
                "requirements": [
                    {
                        "id": "req_pencil",
                        "original_test_name": "Pencil hardness",
                        "requested_methods": [{"code": "ISO 15184"}],
                        "conditions": [{"name": "Temperature", "raw_value": "23", "unit": "°C"}],
                        "evidence": [{"page": 1, "quote": SOURCE}],
                    }
                ],
            },
        }
    )
    workspace.specs.save_extraction(source.document_id, extraction)
    review = workspace.specs.load_review(source.document_id, extraction.run_id)
    workspace.specs.save_reviews(
        source.document_id,
        extraction.run_id,
        ["req_pencil"],
        "confirmed",
        "Checked original source",
        expected_version=review.version,
    )
    lab = workspace.labs.load_or_create()
    lab.capabilities = [
        LabCapability(
            id="cap_pencil",
            canonical_test_id="pencil_hardness",
            method_ids=["iso_15184"],
            atomic_claims=[
                AtomicClaim(
                    id="lab_temp",
                    parameter_id="lab_operating_temperature",
                    label="Operating temperature",
                    operator="range",
                    minimum=20,
                    maximum=30,
                    unit="°C",
                    raw_text="The lab declares operating temperature from 20 to 30 degrees C.",
                )
            ],
        )
    ]
    workspace.labs.save(lab)
    provider = MatchingProvider()
    api = importlib.import_module("scione.api.app")
    monkeypatch.setattr(api, "create_configured_provider", lambda *args, **kwargs: provider)
    client = LocalHTTPClient(create_app(settings, frontend_dir=tmp_path / "no_frontend"))
    yield client, workspace, source.document_id, provider, settings
    workspace.library.store.close()


def body_for(client, document_id, **changes):
    state = client.get(f"/api/specifications/{document_id}/matching").json()
    return {
        "run_id": state["source_run_id"],
        "input_fingerprint": state["report_input_fingerprint"],
        "provider": "gemini",
        "model": "fixture-alias",
        "output_limit": 8000,
        "permission": True,
        **changes,
    }


def test_explicit_ai_alignment_drives_saved_comparison_and_machine_artifacts(setup):
    client, workspace, document_id, provider, _settings = setup
    before = client.get(f"/api/comparisons/{document_id}").json()
    assert before["comparison_engine"] == "deterministic_rules"
    assert before["summary"]["needs_information"] == 1
    response = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert response.status_code == 200, response.text
    assert response.json()["cached"] is False
    assert provider.calls == 1
    after = client.get(f"/api/comparisons/{document_id}").json()
    assert after["comparison_engine"] == "ai_semantic_matching"
    assert after["matching_run_id"] == response.json()["run_id"]
    assert after["requirements"][0]["comparison_engine"] == "ai_semantic_matching"
    assert after["summary"]["fully_matched"] == 1, json.dumps(
        after["requirements"], ensure_ascii=False
    )
    check = next(
        row
        for row in after["requirements"][0]["comparison"]["checks"]
        if row["kind"] == "condition"
    )
    assert check["requested"] == "23 °C"
    assert check["declared"] == "20–30 °C"
    assert check["capability_claim_ids"] == ["lab_temp"]
    artifacts = client.get(
        f"/api/specifications/{document_id}/matching/{after['matching_run_id']}/artifacts"
    )
    assert artifacts.status_code == 200
    artifact = artifacts.json()
    assert artifact["extraction_machine"]["raw_response"] == "original extraction fixture"
    assert artifact["matching_machine"]["raw_response"]
    assert artifact["matching_machine"]["model"] == "fixture-resolved-revision"
    assert artifact["matching_machine"]["requested_model"] == "fixture-alias"
    assert provider.calls == 1  # Browsing and exporting must never call a model.
    assert workspace.labs.records()["claims"][0]["parameter_id"] == "lab_operating_temperature"


@pytest.mark.parametrize("maximum, summary", [("30", "fully_matched"), ("22", "partially_matched")])
def test_decisive_exact_rules_survive_ai_deferral_and_keep_machine_proposal(
    setup, monkeypatch, maximum, summary
):
    client, workspace, document_id, provider, _settings = setup
    lab = workspace.labs.load_or_create()
    lab.capabilities[0].atomic_claims[0].parameter_id = "temperature"
    lab.capabilities[0].atomic_claims[0].maximum = Decimal(maximum)
    lab.capabilities[0].atomic_claims[0].configuration = "Rig A"
    workspace.labs.save(lab)
    before = client.get(f"/api/comparisons/{document_id}").json()
    assert before["summary"][summary] == 1, before["requirements"]

    original_generate = provider.generate_structured

    def defer_everything(request):
        result = original_generate(request)
        payload = result.parsed
        for item in payload["items"]:
            item.update(identity_status="unknown", canonical_test_id=None, capability_id=None)
            for link in item["claim_alignments"]:
                link.update(
                    relation="unknown",
                    capability_claim_ids=[],
                    reason="The model deferred this comparison.",
                )
        return result.model_copy(update={"parsed": payload, "raw_text": json.dumps(payload)})

    monkeypatch.setattr(provider, "generate_structured", defer_everything)
    matched = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert matched.status_code == 200, matched.text
    after = client.get(f"/api/comparisons/{document_id}").json()
    assert after["comparison_engine"] == "ai_semantic_matching"
    assert after["summary"][summary] == 1, after["requirements"]
    assert after["requirements"][0]["comparison_engine"] == "deterministic_rules"
    assert after["requirements"][0]["capability_id"] == "cap_pencil"
    assert after["requirements"][0]["comparison"] == before["requirements"][0]["comparison"]
    artifact = workspace.matching.artifacts(document_id, matched.json()["run_id"])
    assert artifact["matching_machine"]["output"]["items"][0]["identity_status"] == "unknown"
    assert artifact["comparison_validated"][0]["status"] == "needs_information"


def test_partial_rule_item_backfills_only_decisive_exact_field_when_ai_defers(setup, monkeypatch):
    client, workspace, document_id, provider, _settings = setup
    lab = workspace.labs.load_or_create()
    lab.capabilities[0].atomic_claims[0].parameter_id = "temperature"
    workspace.labs.save(lab)
    run = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].conditions.append(
        run.extraction.requirements[0].conditions[0].model_copy(
            update={"name": "Duration", "raw_value": "1", "unit": "h"}
        )
    )
    workspace.specs.save_extraction(document_id, run)
    workspace.specs.save_reviews(document_id, run.run_id, ["req_pencil"], "confirmed")
    before = client.get(f"/api/comparisons/{document_id}").json()
    assert before["summary"]["needs_information"] == 1
    assert any(
        check["parameter_id"] == "temperature" and check["status"] == "supported"
        for check in before["requirements"][0]["comparison"]["checks"]
    )
    original_generate = provider.generate_structured

    def defer_claims(request):
        result = original_generate(request)
        payload = result.parsed
        for item in payload["items"]:
            for link in item["claim_alignments"]:
                link.update(
                    relation="unknown",
                    capability_claim_ids=[],
                    reason="No accepted correspondence.",
                )
        return result.model_copy(update={"parsed": payload, "raw_text": json.dumps(payload)})

    monkeypatch.setattr(provider, "generate_structured", defer_claims)
    response = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert response.status_code == 200, response.text
    after = client.get(f"/api/comparisons/{document_id}").json()
    row = after["requirements"][0]
    assert row["comparison_engine"] == "ai_semantic_matching"
    temperature = next(
        check for check in row["comparison"]["checks"] if check["parameter_id"] == "temperature"
    )
    assert temperature["status"] == "supported"
    assert temperature["comparison_basis"] == "deterministic_exact_fields"
    assert temperature["capability_claim_ids"]
    unknown = [check for check in row["comparison"]["checks"] if check["status"] == "unknown"]
    assert len(unknown) == 1
    duration = unknown[0]
    assert duration["status"] == "unknown"
    assert duration["capability_claim_ids"] == []
    # This added duration is absent from the original PDF evidence as well as
    # the lab declaration: the numeric backfill must not give it an identity.
    assert duration["requirement_claim"]["raw_text"] == "1"
    assert duration["requirement_claim"]["normalization_state"] == "unresolved"
    assert row["comparison"]["unknown_count"] == 1
    assert row["comparison"]["status"] == "needs_information"
    artifact = workspace.matching.artifacts(document_id, response.json()["run_id"])
    assert all(
        link["relation"] == "unknown" and not link["capability_claim_ids"]
        for item in artifact["matching_machine"]["output"]["items"]
        for link in item["claim_alignments"]
    )


def test_rules_first_policy_does_not_collide_with_or_rewrite_old_comparison_snapshot(setup):
    client, workspace, document_id, _provider, _settings = setup
    current = client.get(f"/api/comparisons/{document_id}").json()
    legacy = deepcopy(current)
    legacy.pop("comparison_policy_version")
    legacy.pop("comparison_id")
    legacy.pop("created_at")
    for row in legacy["requirements"]:
        row.pop("comparison_engine")
    old_identity = digest(
        [
            current["report_input_fingerprint"], current["document"],
            NORMALIZER_VERSION, REGISTRY_VERSION, current["matching_run_id"],
        ]
    )
    old = workspace.library.store.save_comparison(old_identity, legacy)
    response = client.get(f"/api/comparisons/{document_id}")
    assert response.status_code == 200, response.text
    assert response.json()["comparison_id"] == current["comparison_id"]
    assert response.json()["comparison_id"] != old["comparison_id"]
    assert workspace.library.store.comparison(old["comparison_id"]) == old


def test_source_attention_prevents_a_decisive_rule_item_from_bypassing_review(setup):
    client, workspace, document_id, _provider, _settings = setup
    lab = workspace.labs.load_or_create()
    lab.capabilities[0].atomic_claims[0].parameter_id = "temperature"
    workspace.labs.save(lab)
    run = workspace.specs.load_latest_extraction(document_id).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.review_issues = ["req_pencil: Check this source extraction."]
    workspace.specs.save_extraction(document_id, run)
    response = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert response.status_code == 200, response.text
    row = client.get(f"/api/comparisons/{document_id}").json()["requirements"][0]
    assert row["source_review_state"] == "needs_attention"
    assert row["comparison_engine"] == "ai_semantic_matching"
    assert row["comparison"]["status"] == "needs_information"
    assert any(check["kind"] == "source" for check in row["comparison"]["checks"])


def test_cached_model_alias_does_not_repeat_call_and_explicit_rerun_does(setup):
    client, _workspace, document_id, provider, _settings = setup
    body = body_for(client, document_id)
    first = client.post(f"/api/specifications/{document_id}/matching", json=body).json()
    cached = client.post(f"/api/specifications/{document_id}/matching", json=body).json()
    assert cached["run_id"] == first["run_id"]
    assert cached["cached"] is True
    assert provider.calls == 1
    rerun = client.post(
        f"/api/specifications/{document_id}/matching", json={**body, "rerun": True}
    ).json()
    assert rerun["run_id"] != first["run_id"]
    assert provider.calls == 2


def test_permission_and_stale_input_rejected_before_any_provider_call(setup):
    client, workspace, document_id, provider, _settings = setup
    body = body_for(client, document_id)
    denied = client.post(
        f"/api/specifications/{document_id}/matching", json={**body, "permission": False}
    )
    assert denied.status_code == 400
    lab = workspace.labs.load_or_create()
    lab.name = "Updated while dialog open"
    workspace.labs.save(lab)
    stale = client.post(f"/api/specifications/{document_id}/matching", json=body)
    assert stale.status_code == 409
    assert provider.calls == 0


def test_failed_proposal_kept_and_previous_success_used_without_corrupting_source(setup):
    client, workspace, document_id, provider, _settings = setup
    body = body_for(client, document_id)
    first = client.post(f"/api/specifications/{document_id}/matching", json=body).json()
    before = workspace.specs.load_latest_extraction(document_id).model_dump(mode="json")
    provider.invalid = True
    failure = client.post(
        f"/api/specifications/{document_id}/matching", json={**body, "rerun": True}
    )
    assert failure.status_code == 502
    state = client.get(f"/api/specifications/{document_id}/matching").json()
    assert state["current_run_id"] == first["run_id"]
    assert state["runs"][0]["status"] == "failed"
    failed_id = state["runs"][0]["run_id"]
    artifact = client.get(
        f"/api/specifications/{document_id}/matching/{failed_id}/artifacts"
    ).json()["matching_machine"]
    assert artifact["raw_response"]
    assert artifact["output"] is None
    assert (
        client.get(f"/api/comparisons/{document_id}").json()["matching_run_id"] == first["run_id"]
    )
    assert workspace.specs.load_latest_extraction(document_id).model_dump(mode="json") == before


def test_lab_edit_invalidates_alignment_but_preserves_historical_artifact(setup):
    client, workspace, document_id, _provider, _settings = setup
    first = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    ).json()
    lab = workspace.labs.load_or_create()
    lab.capabilities[0].atomic_claims[0].maximum = Decimal(22)
    workspace.labs.save(lab)
    assert (
        client.get(f"/api/specifications/{document_id}/matching").json()["current_run_id"] is None
    )
    second = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert second.status_code == 200
    current = client.get(f"/api/comparisons/{document_id}").json()
    assert current["summary"]["partially_matched"] == 1, current["requirements"]
    artifact = client.get(
        f"/api/specifications/{document_id}/matching/{first['run_id']}/artifacts"
    ).json()["matching_machine"]
    assert artifact["input_snapshot"]["capabilities"][0]["claims"][0]["maximum"] == "30"


def test_matching_run_survives_app_recreation_and_cannot_be_read_under_another_document(setup):
    client, workspace, document_id, provider, settings = setup
    created = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    ).json()
    other = workspace.specs.import_pdf("other.pdf", make_pdf("Another independent document."))
    reloaded = LocalHTTPClient(create_app(settings))
    state = reloaded.get(f"/api/specifications/{document_id}/matching").json()
    assert state["current_run_id"] == created["run_id"]
    assert (
        reloaded.get(
            f"/api/specifications/{other.document_id}/matching/{created['run_id']}/artifacts"
        ).status_code
        == 400
    )
    assert provider.calls == 1


def test_report_scope_changes_keep_matching_cache_and_machine_results_unchanged(setup):
    client, workspace, document_id, provider, _settings = setup
    first = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    ).json()
    machine_before = workspace.matching.store.load(document_id, first["run_id"])
    comparison = client.get(f"/api/comparisons/{document_id}").json()
    exclude_body = {
        "run_id": comparison["source_run_id"],
        "input_fingerprint": comparison["report_input_fingerprint"],
        "version": comparison["report_workspace_version"],
        "requirement_ids": ["req_pencil"],
        "excluded": True,
        "note": "Outside this assessment scope",
    }
    excluded = client.post(f"/api/reports/{document_id}/exclusions", json=exclude_body)
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["summary"]["excluded"] == 1
    assert excluded.json()["matching_run_id"] == first["run_id"]
    cached = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    ).json()
    assert cached["cached"] is True
    assert provider.calls == 1
    assert workspace.matching.store.load(document_id, first["run_id"]) == machine_before
    artifact = client.get(
        f"/api/specifications/{document_id}/matching/{first['run_id']}/artifacts"
    ).json()
    assert artifact["comparison_validated"][0]["status"] == "fully_matched"
    assert artifact["human_actions_at_matching"]["report_workspace"]["scope_actions"] == []


def test_ai_null_capability_preserves_a_known_not_listed_test_gap(setup, monkeypatch):
    client, workspace, document_id, provider, _settings = setup
    lab = workspace.labs.load_or_create()
    lab.capabilities = []
    workspace.labs.save(lab)
    original_generate = provider.generate_structured

    def return_no_capability(request):
        result = original_generate(request)
        payload = result.parsed
        for item in payload["items"]:
            item["capability_id"] = None
            for alignment in item["claim_alignments"]:
                alignment["capability_claim_ids"] = []
                alignment["relation"] = "unknown"
                alignment["reason"] = "No lab declaration is supplied."
        return result.model_copy(update={"parsed": payload, "raw_text": json.dumps(payload)})

    monkeypatch.setattr(provider, "generate_structured", return_no_capability)
    response = client.post(
        f"/api/specifications/{document_id}/matching", json=body_for(client, document_id)
    )
    assert response.status_code == 200, response.text
    comparison = client.get(f"/api/comparisons/{document_id}").json()
    assert comparison["summary"]["not_matched"] == 1
    method = next(
        check
        for check in comparison["requirements"][0]["comparison"]["checks"]
        if check["kind"] == "method"
    )
    assert method["status"] == "gap"

"""Real login/tenant API boundaries with offline inference fixtures only."""

import importlib
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient

from scione.api.app import create_app
from scione.api.public_views import business_view
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth import AuthManager
from scione.auth.models import AuthPrincipal, _principal
from scione.auth.passwords import hash_password
from scione.config import Settings
from scione.matching.workspace import MatchingWorkspace
from scione.monitoring import MonitoringStore
from scione.specifications.extraction import CustomerExtractionError
from scione.specifications.models import CustomerExtractionRun

PASSWORD = "Offline authenticated-inference fixture password"
PRIVATE_RESPONSE = "PRIVATE MODEL RESPONSE MUST REMAIN IN IMMUTABLE ARTIFACT"
SOURCE = "Gloss: ISO 2813. Measure gloss at 60 degrees."


@pytest.fixture
def authenticated_api(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path,
        scione_database_url=None,
        scione_auth_database_url=None,
        scione_auth_enabled=True,
        model_provider="sail",
        sail_api_key="synthetic-test-configuration-only",
        sail_model="server-managed-model",
        sail_max_output_tokens=2048,
    )
    manager = AuthManager(settings)
    manager.initialize()
    registry = TenantWorkspaceRegistry(settings)
    lab = manager.store.create_lab("Inference boundary lab")
    registry.provision(lab.id)
    manager.store.mark_lab_provisioned(lab.id)
    encoded = hash_password(PASSWORD)
    for username, role in (("lab.account", "lab"), ("operator", "maintainer")):
        manager.store.create_user(username, username, role, encoded, lab_id=lab.id)
    manager.close()
    registry.close_all()
    app = create_app(settings, frontend_dir=tmp_path / "no-frontend")
    workspace = app.state.tenant_registry.workspace_for(lab.id)
    captured = {"factory": [], "extraction": [], "matching": []}
    api = importlib.import_module("scione.api.app")

    def factory(selected, *, provider_name, model_name):
        captured["factory"].append((selected, provider_name, model_name))
        return SimpleNamespace(provider=provider_name, model=model_name)

    class OfflineRunner:
        def __init__(self, adapter, **kwargs):
            self.adapter = adapter

        def run(self, document, *, strategy):
            captured["extraction"].append(strategy)
            return CustomerExtractionRun.model_validate(
                {
                    "run_id": str(uuid4()),
                    "document_id": document.sha256[:16],
                    "started_at": datetime.now(UTC),
                    "schema_version": "0.1",
                    "prompt_version": "offline_authenticated_fixture",
                    "provider": self.adapter.provider,
                    "model": self.adapter.model,
                    "latency_ms": 1,
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                    "raw_response": PRIVATE_RESPONSE,
                    "extraction": {
                        "schema_version": "0.1",
                        "document": {"title": "Gloss requirements"},
                        "requirements": [
                            {
                                "id": "req_gloss",
                                "original_test_name": "Gloss",
                                "requested_methods": [{"code": "ISO 2813"}],
                                "evidence": [{"page": 1, "quote": SOURCE}],
                            }
                        ],
                    },
                }
            )

    def matching(self, document_id, body, provider):
        captured["matching"].append(body)
        return {
            "document_id": document_id,
            "run_id": str(uuid4()),
            "raw_response": PRIVATE_RESPONSE,
            "input_snapshot": {"private": True},
            "requirements": [{"test_name": "Gloss"}],
        }

    monkeypatch.setattr(api, "create_configured_provider", factory)
    monkeypatch.setattr(api, "CustomerRequirementRunner", OfflineRunner)
    monkeypatch.setattr(MatchingWorkspace, "run", matching)
    with TestClient(app) as client:
        yield client, workspace, captured


def login_and_upload(client, username="lab.account"):
    signed_in = client.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert signed_in.status_code == 200, signed_in.text
    headers = {"X-CSRF-Token": signed_in.json()["csrf_token"]}
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), SOURCE)
        data = pdf.tobytes()
    uploaded = client.post(
        "/api/specifications",
        headers=headers,
        files={"file": ("customer.pdf", data, "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()["document_id"], headers, signed_in.json()["user"]["id"]


def untrusted_analysis(**changes):
    return {
        "provider": "groq",
        "model": "untrusted-expensive-model",
        "output_limit": 65536,
        "strategy": "whole_document",
        "permission": False,
        "billing_acknowledged": False,
        "free_tier_confirmed": False,
        **changes,
    }


def test_lab_inference_uses_server_policy_and_hides_raw_artifacts_after_every_source_action(
    authenticated_api,
):
    client, workspace, captured = authenticated_api
    document_id, headers, user_id = login_and_upload(client)
    path = f"/api/specifications/{document_id}"
    analyzed = client.post(path + "/analysis", headers=headers, json=untrusted_analysis())
    assert analyzed.status_code == 200, analyzed.text
    selected, provider, model = captured["factory"][0]
    assert (provider, model) == ("sail", "server-managed-model")
    assert selected.sail_max_output_tokens == 2048
    assert selected.sail_temperature == 0 and selected.scione_single_attempt_mode
    assert captured["extraction"] == ["inventory_compact_document"]
    result = analyzed.json()
    assert set(result["run"]) == {"run_id", "document_id", "started_at", "extraction"}
    original = workspace.specs.load_latest_extraction(document_id).model_dump(mode="json")
    assert original["raw_response"] == PRIVATE_RESPONSE
    reviewed = client.post(
        path + "/review",
        headers=headers,
        json={
            "run_id": result["run"]["run_id"],
            "version": result["review"]["version"],
            "facts_version": 0,
            "requirement_ids": ["req_gloss"],
            "decision": "confirmed",
            "note": "Compared against the source PDF.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert (
        workspace.specs.load_review(
            document_id,
            original["run_id"],
        )
        .decisions["req_gloss"]
        .actor
        == user_id
    )
    requirements = deepcopy(result["run"]["extraction"]["requirements"])
    requirements[0]["original_test_name"] = "Gloss measurement"
    facts = client.post(
        path + "/facts",
        headers=headers,
        json={
            "run_id": original["run_id"],
            "expected_version": 0,
            "expected_review_version": reviewed.json()["review"]["version"],
            "requirements": requirements,
            "note": "Clarified the source test label.",
        },
    )
    assert facts.status_code == 200, facts.text
    assert workspace.facts.latest(document_id, original["run_id"]).actor == user_id
    for response in (analyzed, reviewed, facts, client.get(path), client.get("/api/library")):
        assert PRIVATE_RESPONSE not in response.text and '"raw_response"' not in response.text
    assert workspace.specs.load_latest_extraction(document_id).model_dump(mode="json") == original
    for restricted in (
        f"/api/diagnostics/{document_id}/raw",
        path + "/matching/unknown/artifacts",
        path + "/analysis-failures/unknown/artifacts",
        f"/api/reports/{document_id}",
    ):
        assert client.get(restricted).status_code == 403


@pytest.mark.parametrize("username,managed", [("lab.account", True), ("operator", False)])
def test_lab_matching_policy_cannot_be_overridden_but_maintainer_choice_remains(
    authenticated_api,
    username,
    managed,
):
    client, _workspace, captured = authenticated_api
    document_id, headers, _ = login_and_upload(client, username)
    path = f"/api/specifications/{document_id}"
    analyzed = client.post(
        path + "/analysis",
        headers=headers,
        json=untrusted_analysis(permission=True, billing_acknowledged=True),
    )
    assert analyzed.status_code == 200, analyzed.text
    state = client.get(path + "/matching").json()
    matched = client.post(
        path + "/matching",
        headers=headers,
        json={
            "run_id": state["source_run_id"],
            "input_fingerprint": state["report_input_fingerprint"],
            "provider": "groq",
            "model": "requested-maintainer-model",
            "output_limit": 65536,
            "permission": not managed,
            "rerun": True,
        },
    )
    assert matched.status_code == 200, matched.text
    selected, provider, model = captured["factory"][-1]
    request = captured["matching"][-1]
    if managed:
        assert (provider, model) == ("sail", "server-managed-model")
        assert selected.sail_max_output_tokens == request.output_limit == 2048
        assert request.permission and not request.rerun
        assert PRIVATE_RESPONSE not in matched.text and "input_snapshot" not in matched.text
    else:
        assert (provider, model) == ("groq", "requested-maintainer-model")
        assert selected.groq_max_completion_tokens == request.output_limit == 65536
        assert request.rerun and PRIVATE_RESPONSE in matched.text
        assert captured["extraction"] == ["whole_document"]


def test_business_view_is_a_detached_redaction_and_never_rewrites_an_audit_payload():
    payload = {
        "run_id": "immutable",
        "extraction": {"requirements": [{"name": "Gloss"}]},
        "raw_response": PRIVATE_RESPONSE,
        "input_snapshot": {"raw_response": PRIVATE_RESPONSE},
        "provider": "internal",
    }
    original = deepcopy(payload)
    token = _principal.set(AuthPrincipal("u", "lab", "Lab", "lab", "a" * 32, "Lab"))
    try:
        redacted = business_view(payload)
        assert PRIVATE_RESPONSE not in str(redacted)
        redacted["extraction"]["requirements"][0]["name"] = "View-only change"
        assert payload == original
    finally:
        _principal.reset(token)


def test_successful_extraction_records_one_operation_with_public_run_counts(authenticated_api):
    client, workspace, _captured = authenticated_api
    document_id, headers, _ = login_and_upload(client)
    response = client.post(
        f"/api/specifications/{document_id}/analysis",
        headers=headers,
        json=untrusted_analysis(),
    )
    assert response.status_code == 200, response.text
    entries = MonitoringStore(workspace.library.store).listing(since="", limit=100)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "extraction" and entry["status"] == "success"
    assert entry["document_id"] == document_id
    assert entry["run_id"] == response.json()["run"]["run_id"]
    assert entry["page_count"] == entry["test_count"] == 1
    assert entry["finished_at"] and entry["duration_ms"] >= 0


def test_cached_matching_records_cache_reuse_instead_of_a_fresh_success(
    authenticated_api,
    monkeypatch,
):
    client, workspace, _captured = authenticated_api
    document_id, headers, _ = login_and_upload(client)
    path = f"/api/specifications/{document_id}"
    assert (
        client.post(path + "/analysis", headers=headers, json=untrusted_analysis()).status_code
        == 200
    )
    state = client.get(path + "/matching").json()
    cached_run_id = str(uuid4())

    def cached_matching(self, document_id, body, provider):
        return {"run_id": cached_run_id, "cached": True, "status": "completed"}

    monkeypatch.setattr(MatchingWorkspace, "run", cached_matching)
    response = client.post(
        path + "/matching",
        headers=headers,
        json={
            "run_id": state["source_run_id"],
            "input_fingerprint": state["report_input_fingerprint"],
            "provider": "groq",
            "model": "untrusted",
            "output_limit": 65536,
        },
    )
    assert response.status_code == 200 and response.json()["cached"]
    entries = MonitoringStore(workspace.library.store).listing(since="", limit=100)
    matching = [entry for entry in entries if entry["kind"] == "matching"]
    assert len(matching) == 1
    assert matching[0]["status"] == "cache_reuse" and matching[0]["run_id"] == cached_run_id


def test_failed_provider_operation_is_recorded_without_changing_the_original_api_error(
    authenticated_api,
    monkeypatch,
):
    client, workspace, _captured = authenticated_api
    document_id, headers, _ = login_and_upload(client)
    api = importlib.import_module("scione.api.app")

    def unavailable(self, document, *, strategy):
        raise CustomerExtractionError("The offline provider could not complete analysis.")

    monkeypatch.setattr(api.CustomerRequirementRunner, "run", unavailable)
    path = f"/api/specifications/{document_id}/analysis"
    observed = client.post(path, headers=headers, json=untrusted_analysis())
    assert observed.status_code == 400
    entries = MonitoringStore(workspace.library.store).listing(since="", limit=100)
    assert len(entries) == 1 and entries[0]["status"] == "failure"
    assert entries[0]["kind"] == "extraction" and entries[0]["finished_at"]
    # Removing observation must not change the existing business failure path.
    monkeypatch.setattr(api, "start_operation", lambda *_args, **_kwargs: None)
    baseline = client.post(path, headers=headers, json=untrusted_analysis())
    assert baseline.status_code == 400 and baseline.json() == observed.json()
    assert workspace.specs.load_latest_extraction(document_id) is None


@pytest.mark.parametrize("method", ["start", "finish"])
def test_metrics_write_failure_cannot_change_success_or_leak_exception_details(
    authenticated_api,
    monkeypatch,
    caplog,
    method,
):
    client, workspace, captured = authenticated_api
    document_id, headers, _ = login_and_upload(client)
    private_error = "PRIVATE-MONITORING-BOUND-PARAMETER-MUST-NOT-LEAK"

    def unavailable(*_args, **_kwargs):
        raise RuntimeError(private_error)

    monkeypatch.setattr(MonitoringStore, method, unavailable)
    response = client.post(
        f"/api/specifications/{document_id}/analysis",
        headers=headers,
        json=untrusted_analysis(),
    )
    assert response.status_code == 200, response.text
    assert len(captured["extraction"]) == 1
    assert (
        workspace.specs.load_latest_extraction(document_id).run_id
        == response.json()["run"]["run_id"]
    )
    assert private_error not in response.text and private_error not in caplog.text
    assert "metrics" in caplog.text and "RuntimeError" in caplog.text


def test_operation_dashboard_is_maintainer_only(authenticated_api):
    client, _workspace, _captured = authenticated_api
    login_and_upload(client)
    assert client.get("/api/developer/metrics").status_code == 403
    signed_in = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": PASSWORD},
    )
    assert signed_in.status_code == 200
    metrics = client.get("/api/developer/metrics")
    assert metrics.status_code == 200, metrics.text
    assert PRIVATE_RESPONSE not in metrics.text and PASSWORD not in metrics.text

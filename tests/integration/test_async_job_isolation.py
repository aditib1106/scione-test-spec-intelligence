"""Detached inference failures cannot leak tenants, replace old results, or hold leases."""

from __future__ import annotations

import importlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient

from scione.api.app import create_app
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth import AuthManager
from scione.auth.models import current_principal
from scione.auth.passwords import hash_password
from scione.config import Settings
from scione.providers.base import ModelProvider, ProviderError, StructuredGenerationResponse
from scione.specifications.models import CustomerExtractionRun

PASSWORD = "Synthetic asynchronous isolation password 2026"
SOURCE = "Gloss: ISO 2813. Measure at 60 degrees."
PRIVATE_RESPONSE = "PRIVATE_ASYNC_PROVIDER_ARTIFACT"
PRIVATE_FAILURE = "PRIVATE_FAILED_PROVIDER_BODY"


def _pdf(label="First customer document"):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 50), SOURCE)
        page.insert_text((40, 75), label)
        page.insert_text(
            (40, 100),
            "Synthetic customer instructions for one laboratory gloss measurement. "
            "Retain the original observations and document the result for customer review.",
        )
        return document.tobytes(no_new_id=True)


@dataclass
class _Plan:
    block: bool = False
    fail_at: int | None = None
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    calls: int = 0


class _ControlledProvider(ModelProvider):
    """Exercise the production runner while every model response stays offline."""

    def __init__(self, registry):
        self.registry = registry
        self.plans = {}
        self.records = []
        self.lock = threading.Lock()

    @property
    def provider_name(self):
        return "sail"

    @property
    def model_name(self):
        return "offline-async-isolation"

    @property
    def generation_settings(self):
        return {"max_output_tokens": 2048, "automatic_retries": 0}

    def plan(self, lab_id, *, block=False, fail_at=None):
        plan = _Plan(block=block, fail_at=fail_at)
        with self.lock:
            self.plans[lab_id] = plan
        return plan

    def release_all(self):
        for plan in self.plans.values():
            plan.release.set()

    def _context(self):
        principal = current_principal()
        return {
            "user_id": principal.user_id if principal else None,
            "lab_id": principal.lab_id if principal else None,
            "bound_lab_id": self.registry.current_lab_id(),
            "workspace_root": str(self.registry.current().settings.scione_workspace_dir),
        }

    def generate_structured(self, request):
        before = self._context()
        with self.lock:
            plan = self.plans.setdefault(before["lab_id"], _Plan())
            plan.calls += 1
            number = plan.calls
        if plan.block and number == 1:
            plan.entered.set()
            if not plan.release.wait(10):
                raise AssertionError("The controlled inference job was not released by its test.")
        after = self._context()
        with self.lock:
            self.records.append({"before": before, "after": after, "call": number})
        if plan.fail_at == number:
            raise ProviderError(
                "Synthetic extraction provider failed.", raw_text=PRIVATE_FAILURE,
                provider=self.provider_name, model=self.model_name,
            )
        if "inventory" in request.schema_name:
            payload = {
                "tests": [{"test": "Gloss", "evidence": [{"page": 1, "quote": SOURCE}]}],
                "attachments": [], "uncertainties": [],
            }
        else:
            payload = {
                "document": {"title": "Offline laboratory gloss requirements"},
                "shared_requirements": [],
                "requirements": [{
                    "test": "Gloss", "methods": ["ISO 2813"], "method_roles": [],
                    "conditions": ["Measure at 60 degrees."], "criteria": [], "scope": [],
                    "instructions": [], "evidence": [{"page": 1, "quote": SOURCE}],
                    "uncertainty": None,
                    "atomic_claims": [{
                        "parameter_id": "measurement_angle", "kind": "condition",
                        "stage": "measurement", "operator": "=", "values": [60],
                        "unit": "°", "raw_text": "60 degrees", "source_pages": [1],
                    }],
                }],
                "review_issues": [], "document_notices": [],
            }
        return StructuredGenerationResponse(
            provider=self.provider_name, model=self.model_name,
            raw_text=PRIVATE_RESPONSE + json.dumps(payload), parsed=payload, latency_ms=1,
            generation_settings=self.generation_settings,
        )


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path.resolve(),
        scione_database_url=None, scione_auth_database_url=None,
        scione_auth_enabled=True, scione_bootstrap_accounts=None,
        model_provider="sail", sail_api_key="synthetic-offline-test-only",
        sail_model="offline-async-isolation", sail_max_output_tokens=2048,
        gemini_api_key=None, groq_api_key=None, fireworks_api_key=None,
    )
    manager = AuthManager(settings)
    manager.initialize()
    provisioning = TenantWorkspaceRegistry(settings)
    hashed = hash_password(PASSWORD)
    lab_ids = {}
    for username, title in (("labalpha", "Laboratory Alpha"), ("labbeta", "Laboratory Beta")):
        lab = manager.store.create_lab(title)
        provisioning.provision(lab.id)
        manager.store.mark_lab_provisioned(lab.id)
        manager.store.create_user(username, title, "lab", hashed, lab_id=lab.id)
        lab_ids[username] = lab.id
    manager.store.create_user(
        "operator", "Fixture maintainer", "maintainer", hashed, lab_id=lab_ids["labalpha"],
    )
    manager.close()
    provisioning.close_all()
    app = create_app(settings, frontend_dir=tmp_path / "no-frontend")
    provider = _ControlledProvider(app.state.tenant_registry)
    module = importlib.import_module("scione.api.app")
    monkeypatch.setattr(module, "create_configured_provider", lambda *_a, **_kw: provider)
    with TestClient(app) as client:
        try:
            yield SimpleNamespace(
                app=app, client=client, registry=app.state.tenant_registry,
                provider=provider, lab_ids=lab_ids,
            )
        finally:
            provider.release_all()


def _login(api, username):
    # Each captured header bundle represents an independent browser. Passing an
    # existing browser cookie to login deliberately replaces that prior session.
    response = api.client.post(
        "/api/auth/login", headers={"Cookie": ""},
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {
        "Cookie": "; ".join(f"{key}={value}" for key, value in api.client.cookies.items()),
        "X-CSRF-Token": response.json()["csrf_token"],
    }, response.json()["user"]


def _upload(api, headers, content, filename="customer.pdf"):
    response = api.client.post(
        "/api/specifications", headers=headers,
        files={"file": (filename, content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["document_id"]


def _start(api, headers, document_id):
    return api.client.post(
        f"/api/specifications/{document_id}/analysis",
        headers={**headers, "Prefer": "respond-async", "X-Inference-Request-ID": str(uuid4())},
        json={
            "provider": "sail", "model": "offline-async-isolation", "output_limit": 2048,
            "strategy": "inventory_compact_document", "permission": True,
            "billing_acknowledged": True,
        },
    )


def _operation(response, document_id):
    assert response.status_code == 202, response.text
    operation = response.json()
    assert operation["document_id"] == document_id
    assert operation["kind"] == "extraction" and operation["state"] == "running"
    assert operation["status_url"] == f"/api/operations/{operation['operation_id']}"
    return operation


def _terminal(api, headers, operation):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = api.client.get(operation["status_url"], headers=headers)
        assert response.status_code == 200, response.text
        if response.json()["state"] in {"completed", "failed"}:
            return response.json()
        threading.Event().wait(0.01)
    pytest.fail("The offline inference job did not reach a terminal state.")


def _seed_previous(workspace, document_id):
    run = CustomerExtractionRun.model_validate({
        "run_id": str(uuid4()), "document_id": document_id, "started_at": datetime.now(UTC),
        "schema_version": "0.1", "prompt_version": "previous_success_fixture",
        "provider": "offline", "model": "no_network", "latency_ms": 0,
        "usage": {}, "raw_response": "OLDER_SUCCESS_MUST_REMAIN_UNCHANGED",
        "extraction": {
            "schema_version": "0.1", "document": {"title": "Previous success"},
            "requirements": [{
                "id": "req_gloss", "original_test_name": "Gloss",
                "requested_methods": [{"code": "ISO 2813"}],
                "evidence": [{"page": 1, "quote": SOURCE}],
            }],
        },
    })
    workspace.specs.save_extraction(document_id, run)
    return run


def test_failed_job_preserves_archive_releases_lease_and_leaves_same_pdf_other_lab_untouched(api):
    alpha_headers, _ = _login(api, "labalpha")
    content = _pdf()
    document_id = _upload(api, alpha_headers, content, "alpha.pdf")
    different_document = _upload(api, alpha_headers, _pdf("Different customer document"))
    alpha = api.registry.workspace_for(api.lab_ids["labalpha"])
    previous = _seed_previous(alpha, document_id)
    immutable_paths = [
        alpha.specs._document_directory(document_id) / "original.pdf",
        alpha.specs._run_directory(document_id, previous.run_id) / "run.json",
        alpha.specs._run_directory(document_id, previous.run_id) / "raw_response.txt",
        alpha.specs._document_directory(document_id) / "latest_run.json",
    ]
    archived = {path: path.read_bytes() for path in immutable_paths}
    failing = api.provider.plan(api.lab_ids["labalpha"], block=True, fail_at=2)
    job_a = _operation(_start(api, alpha_headers, document_id), document_id)
    assert failing.entered.wait(3)
    for doc in (document_id, different_document):
        busy = _start(api, alpha_headers, doc)
        assert busy.status_code in {409, 429}, busy.text
        assert "running" in busy.text.lower() or "busy" in busy.text.lower()
    assert failing.calls == 1  # Rejected submissions are not queued model requests.

    beta_headers, _ = _login(api, "labbeta")
    assert _upload(api, beta_headers, content, "beta.pdf") == document_id
    job_b = _operation(_start(api, beta_headers, document_id), document_id)
    for headers, foreign in ((beta_headers, job_a), (alpha_headers, job_b)):
        hidden = api.client.get(foreign["status_url"], headers=headers)
        assert hidden.status_code == 404, hidden.text
    running = api.client.get(job_a["status_url"], headers=alpha_headers)
    assert running.status_code == 200 and running.json()["state"] == "running"
    success = _terminal(api, beta_headers, job_b)
    assert success["state"] == "completed" and success["result"]["run_id"]
    assert PRIVATE_RESPONSE not in json.dumps(success)
    failing.release.set()
    failure = _terminal(api, alpha_headers, job_a)
    assert failure["state"] == "failed" and failure["error"]["status"] >= 400
    assert PRIVATE_FAILURE not in json.dumps(failure)
    assert {path: path.read_bytes() for path in immutable_paths} == archived
    assert alpha.specs.load_latest_extraction(document_id).run_id == previous.run_id
    assert alpha.specs.load_failure(document_id).previous_run_id == previous.run_id
    beta = api.registry.workspace_for(api.lab_ids["labbeta"])
    assert beta.specs.load_latest_extraction(document_id).run_id == success["result"]["run_id"]
    assert beta.specs.load_failure(document_id) is None

    api.provider.plan(api.lab_ids["labalpha"])
    next_job = _operation(_start(api, alpha_headers, different_document), different_document)
    assert _terminal(api, alpha_headers, next_job)["state"] == "completed"
    assert alpha.specs.load_latest_extraction(document_id).run_id == previous.run_id
    assert api.client.get(job_a["status_url"], headers=beta_headers).status_code == 404
    assert api.client.get(job_b["status_url"], headers=alpha_headers).status_code == 404


@pytest.mark.parametrize("change", ["logout_and_login", "maintainer_workspace_switch"])
def test_admitted_job_keeps_original_principal_and_workspace_after_browser_changes(api, change):
    username = "labalpha" if change == "logout_and_login" else "operator"
    original_headers, original_user = _login(api, username)
    document_id = _upload(api, original_headers, _pdf())
    plan = api.provider.plan(api.lab_ids["labalpha"], block=True)
    operation = _operation(_start(api, original_headers, document_id), document_id)
    assert plan.entered.wait(3)
    if change == "logout_and_login":
        logged_out = api.client.post("/api/auth/logout", headers=original_headers)
        assert logged_out.status_code == 200, logged_out.text
        assert api.client.get(operation["status_url"], headers=original_headers).status_code == 401
        other_headers, _ = _login(api, "labbeta")
    else:
        switched = api.client.post(
            "/api/auth/workspace", headers=original_headers,
            json={"lab_id": api.lab_ids["labbeta"]},
        )
        assert switched.status_code == 200, switched.text
        other_headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in api.client.cookies.items()),
            "X-CSRF-Token": switched.json()["csrf_token"],
        }
    assert api.client.get(operation["status_url"], headers=other_headers).status_code == 404
    plan.release.set()
    alpha_headers, _ = _login(api, "labalpha")
    result = _terminal(api, alpha_headers, operation)
    assert result["state"] == "completed"
    records = [row for row in api.provider.records
               if row["before"]["user_id"] == original_user["id"]]
    assert len(records) == 2
    for record in records:
        assert record["before"] == record["after"]
        assert record["after"]["lab_id"] == api.lab_ids["labalpha"]
        assert record["after"]["bound_lab_id"] == api.lab_ids["labalpha"]
        assert api.lab_ids["labalpha"] in record["after"]["workspace_root"]
    assert api.registry.workspace_for(api.lab_ids["labalpha"]).specs.load_latest_extraction(
        document_id
    ).run_id == result["result"]["run_id"]
    assert not api.registry.workspace_for(api.lab_ids["labbeta"]).specs.list_documents()


@pytest.mark.parametrize("capacity_owner", ["gate", "job_manager"])
def test_global_capacity_rejects_instead_of_queuing_and_recovers(api, capacity_owner):
    if capacity_owner == "gate":
        api.app.state.inference_gate.max_concurrent = 1
    else:
        api.app.state.inference_jobs.max_running = 1
    alpha_headers, _ = _login(api, "labalpha")
    data = _pdf()
    document_id = _upload(api, alpha_headers, data)
    plan = api.provider.plan(api.lab_ids["labalpha"], block=True)
    operation = _operation(_start(api, alpha_headers, document_id), document_id)
    assert plan.entered.wait(3)
    beta_headers, _ = _login(api, "labbeta")
    _upload(api, beta_headers, data)
    busy = _start(api, beta_headers, document_id)
    assert busy.status_code in {409, 429}, busy.text
    assert "busy" in busy.text.lower() or "running" in busy.text.lower()
    assert not api.provider.plans.get(api.lab_ids["labbeta"])
    plan.release.set()
    assert _terminal(api, alpha_headers, operation)["state"] == "completed"
    beta_job = _operation(_start(api, beta_headers, document_id), document_id)
    assert _terminal(api, beta_headers, beta_job)["state"] == "completed"
    assert api.app.state.inference_gate.snapshot(workspace_id=api.lab_ids["labalpha"]) == []
    assert api.app.state.inference_gate.snapshot(workspace_id=api.lab_ids["labbeta"]) == []


def test_closed_job_manager_keeps_completed_receipt_but_rejects_new_work(api):
    headers, _ = _login(api, "labalpha")
    document_id = _upload(api, headers, _pdf())
    operation = _operation(_start(api, headers, document_id), document_id)
    completed = _terminal(api, headers, operation)
    assert completed["state"] == "completed"
    calls_before = len(api.provider.records)
    api.app.state.inference_jobs.shutdown()
    rejected = _start(api, headers, document_id)
    assert rejected.status_code == 503, rejected.text
    assert "shutting down" in rejected.text.lower() and "no work was started" in (
        rejected.text.lower()
    )
    assert len(api.provider.records) == calls_before
    receipt = api.client.get(operation["status_url"], headers=headers)
    assert receipt.status_code == 200 and receipt.json() == completed
    assert api.app.state.inference_gate.snapshot(workspace_id=api.lab_ids["labalpha"]) == []

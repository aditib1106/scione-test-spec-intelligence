"""Reconnectable observation of paid work without a queue or provider calls."""

import asyncio
import json
from http.cookies import SimpleCookie

import httpx
import pymupdf
import pytest
from fastapi import FastAPI

from scione.api.inference_activity import create_inference_activity_router
from scione.api.inference_gate import InferenceGate
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth.models import AuthPrincipal, _principal
from scione.config import Settings
from scione.monitoring.store import MonitoringStore

LAB_A, LAB_B = "a" * 32, "b" * 32


@pytest.fixture
def activity(tmp_path):
    registry = TenantWorkspaceRegistry(
        Settings(_env_file=None, scione_workspace_dir=tmp_path, scione_database_url=None)
    )
    a, b = registry.workspace_for(LAB_A), registry.workspace_for(LAB_B)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), "Synthetic test document; no model call.")
        content = pdf.tobytes()
    document = a.specs.import_pdf("private-customer-a.pdf", content)
    b.specs.import_pdf("private-customer-b.pdf", content)
    gate = InferenceGate()
    app = FastAPI()
    app.include_router(create_inference_activity_router(gate, registry))

    class FixedTestSessions:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            cookie = SimpleCookie()
            cookie.load(dict(scope.get("headers", [])).get(b"cookie", b"").decode())
            session = cookie.get("session")
            value = session.value if session is not None else None
            lab_id = {"opaque-a": LAB_A, "opaque-b": LAB_B}.get(value)
            principal = None
            if value in {"opaque-a", "opaque-b", "opaque-admin"}:
                principal = AuthPrincipal(
                    user_id=value,
                    username=value,
                    display_name="Fixture",
                    role="maintainer" if value == "opaque-admin" else "lab",
                    lab_id=lab_id,
                    lab_name="Fixture lab" if lab_id else None,
                )
            token = _principal.set(principal)
            try:
                await self.app(scope, receive, send)
            finally:
                _principal.reset(token)

    app.add_middleware(FixedTestSessions)
    yield app, gate, registry, document.document_id
    registry.close_all()


def request(app, path, session="opaque-a"):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get(path, headers={"Cookie": f"session={session}"})
    return asyncio.run(send())


def test_status_survives_new_observer_and_isolated_same_pdf_tenant(activity):
    app, gate, registry, document_id = activity
    store_a = MonitoringStore(registry.workspace_for(LAB_A).library.store)
    store_b = MonitoringStore(registry.workspace_for(LAB_B).library.store)
    event_a = store_a.start("extraction", document_id)
    event_b = store_b.start("matching", document_id)
    store_b.finish(event_b, status="failure", duration_ms=12)
    path = f"/api/specifications/{document_id}/operations"
    with gate.claim(f"{LAB_A}:{document_id}", "analysis", workspace_id=LAB_A) as notify:
        notify({"stage": "extracting", "completed_steps": 2, "total_steps": 7,
                "message": "private-customer-a.pdf", "percent": 100})
        a = request(app, path).json()
        b = request(app, path, "opaque-b").json()
        assert a["state"] == "running" and a["latest"]["id"] == event_a
        assert a["active"][0]["progress"] == {
            "stage": "extracting", "completed_steps": 2, "total_steps": 7,
            "percent": 28.6, "message": "Reading test requirements.",
        }
        assert b["state"] == "idle" and b["active"] == []
        assert b["latest"]["id"] == event_b and b["latest"]["status"] == "failure"
        reopened = request(app, "/api/operations?lab_id=" + LAB_B).json()
        assert reopened["active"][0]["operation_id"] == a["active"][0]["operation_id"]
        assert reopened["background_resume_supported"] is False
        assert request(app, "/api/operations", "opaque-b").json()["active"] == []
        assert request(app, "/api/operations", "opaque-admin").json()["active"] == []
        encoded = json.dumps(a)
        assert LAB_A not in encoded and "private-customer" not in encoded
        assert "provider" not in encoded and "raw_response" not in encoded
    store_a.finish(event_a, status="success", duration_ms=20, run_id="saved-run")
    completed = request(app, path).json()
    assert completed["state"] == "idle" and completed["active"] == []
    assert completed["latest"]["run_id"] == "saved-run"


def test_incomplete_event_is_completion_unknown_never_auto_retried(activity):
    app, gate, registry, document_id = activity
    store = MonitoringStore(registry.workspace_for(LAB_A).library.store)
    identifier = store.start("matching", document_id)
    response = request(app, f"/api/specifications/{document_id}/operations")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "completion_unknown" and body["active"] == []
    assert body["latest"]["id"] == identifier and body["latest"]["status"] == "running"
    assert "No automatic retry" in body["notice"]
    assert gate.snapshot(workspace_id=LAB_A) == []
    assert store.latest_document(document_id)["status"] == "running"


def test_foreign_or_invalid_document_status_is_hidden_and_auth_required(activity):
    app, _gate, registry, _document_id = activity
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), "Only Laboratory A owns this PDF.")
        only_a = registry.workspace_for(LAB_A).specs.import_pdf("a-only.pdf", pdf.tobytes())
    path = f"/api/specifications/{only_a.document_id}/operations"
    assert request(app, path).status_code == 200
    assert request(app, path, "opaque-b").status_code == 404
    assert request(app, "/api/specifications/not-a-document/operations").status_code == 404
    assert request(app, "/api/operations", "not-a-session").status_code == 401


def test_status_telemetry_failure_does_not_hide_active_work_or_expose_details(
    activity, monkeypatch
):
    app, gate, _registry, document_id = activity

    def unavailable(*_args):
        raise RuntimeError("PRIVATE_DATABASE_PASSWORD_AND_SOURCE")

    monkeypatch.setattr(MonitoringStore, "latest_document", unavailable)
    with gate.claim(f"{LAB_A}:{document_id}", "matching", workspace_id=LAB_A):
        response = request(app, f"/api/specifications/{document_id}/operations")
        assert response.status_code == 200 and response.json()["state"] == "running"
        assert response.json()["latest"] is None
        assert "temporarily unavailable" in response.json()["notice"]
        assert "PRIVATE" not in response.text

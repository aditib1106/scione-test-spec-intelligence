"""Async HTTP receipts return before blocked work and expose only compact outcomes."""

import importlib
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

from test_authenticated_inference import (
    authenticated_api,
    login_and_upload,
    untrusted_analysis,
)

from scione.matching.workspace import MatchingWorkspace
from scione.specifications.store import SpecStoreError

__all__ = ["authenticated_api"]


def terminal(client, path):
    deadline = monotonic() + 4
    while monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        if response.json()["state"] != "running":
            return response.json()
        sleep(0.01)
    raise AssertionError("The offline job did not finish.")


def test_async_analysis_receipt_returns_while_work_is_blocked_and_reuses_request_id(
    authenticated_api, monkeypatch,
):
    client, _workspace, captured = authenticated_api
    document_id, headers, _user = login_and_upload(client)
    api = importlib.import_module("scione.api.app")
    original = api.CustomerRequirementRunner.run
    started, release = Event(), Event()

    def blocking(self, document, *, strategy, on_progress=None):
        on_progress({"stage": "inventory", "completed_steps": 0, "total_steps": 2})
        started.set()
        assert release.wait(4)
        return original(self, document, strategy=strategy, on_progress=on_progress)

    monkeypatch.setattr(api.CustomerRequirementRunner, "run", blocking)
    request_id = str(uuid4())
    headers.update({"Prefer": "respond-async", "X-Inference-Request-ID": request_id})
    path = f"/api/specifications/{document_id}"
    try:
        accepted = client.post(path + "/analysis", headers=headers, json=untrusted_analysis())
        assert accepted.status_code == 202 and started.wait(1)
        receipt = accepted.json()
        assert receipt["request_id"] == request_id
        assert accepted.headers["Preference-Applied"] == "respond-async"
        running = client.get(receipt["status_url"]).json()
        assert running["state"] == "running" and running["progress"]["total_steps"] == 2
        recent = client.get(path + "/operations").json()["recent_job"]
        assert recent["operation_id"] == receipt["operation_id"]
        assert recent["request_id"] == request_id
        repeated = client.post(path + "/analysis", headers=headers, json=untrusted_analysis())
        assert repeated.status_code == 202
        assert repeated.json()["operation_id"] == receipt["operation_id"]
        release.set()
        result = terminal(client, receipt["status_url"])
        assert result["state"] == "completed"
        assert result["result"]["status"] == "completed"
        assert result["result_url"] == path
        assert "PRIVATE" not in str(result) and "extraction" not in result["result"]
        saved = client.get(path).json()
        assert saved["run"]["run_id"] == result["result"]["run_id"]
        assert len(captured["extraction"]) == 1
        assert client.app.state.inference_gate.snapshot(
            workspace_id=client.get("/api/auth/session").json()["user"]["lab_id"]
        ) == []
    finally:
        release.set()


def test_async_matching_failure_remains_terminal_502_without_raw_detail(
    authenticated_api, monkeypatch,
):
    from fastapi import HTTPException

    client, _workspace, _captured = authenticated_api
    document_id, headers, _user = login_and_upload(client)
    path = f"/api/specifications/{document_id}"
    assert client.post(
        path + "/analysis", headers=headers, json=untrusted_analysis()
    ).status_code == 200
    overview = client.get(path + "/matching").json()

    def failed(self, *_args, **_kwargs):
        raise HTTPException(502, {"message": "PRIVATE SOURCE AND DATABASE PASSWORD"})

    monkeypatch.setattr(MatchingWorkspace, "run", failed)
    accepted = client.post(path + "/matching", headers={**headers, "Prefer": "respond-async"},
                           json={"run_id": overview["source_run_id"],
                                 "input_fingerprint": overview["report_input_fingerprint"],
                                 "provider": "sail", "model": "ignored-user-selection"})
    assert accepted.status_code == 202, accepted.text
    result = terminal(client, accepted.json()["status_url"])
    assert result["state"] == "failed" and result["error"]["status"] == 502
    assert "PRIVATE" not in str(result) and "result" not in result


def test_async_domain_failure_preserves_legacy_error_status(authenticated_api, monkeypatch):
    client, workspace, _captured = authenticated_api
    document_id, headers, _user = login_and_upload(client)

    def unavailable(*_args, **_kwargs):
        raise SpecStoreError("PRIVATE /data/customer-source-path")

    monkeypatch.setattr(workspace, "current_document", unavailable)
    accepted = client.post(
        f"/api/specifications/{document_id}/analysis",
        headers={**headers, "Prefer": "respond-async"}, json=untrusted_analysis(),
    )
    assert accepted.status_code == 202
    result = terminal(client, accepted.json()["status_url"])
    assert result["error"]["status"] == 400 and "PRIVATE" not in str(result)

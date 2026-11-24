"""Server timestamps stay in the display envelope, not capability or matching inputs."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_private_capabilities_api import PASSWORD, body, configuration, save_new

from scione.api.app import create_app
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.api.workspace import Workspace
from scione.auth import AuthManager
from scione.auth.passwords import hash_password
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.openai_compatible import OpenAICompatibleModelProvider
from scione.reports.matching import lab_fingerprint


@pytest.fixture(autouse=True)
def forbid_inference(monkeypatch):
    def reject(*_args, **_kwargs):
        pytest.fail("Capability display metadata must never call a model.")

    for provider in (GeminiModelProvider, GroqModelProvider, OpenAICompatibleModelProvider):
        monkeypatch.setattr(provider, "generate_structured", reject)


def test_api_metadata_is_separate_version_bound_and_persists_after_full_app_restart(
    tmp_path, monkeypatch,
):
    from scione.catalogs import capability_creation as module

    times = iter(["2026-11-24T12:00:00+00:00", "2026-11-24T12:01:00+00:00"])
    monkeypatch.setattr(module, "now", lambda: next(times))
    settings = configuration(tmp_path / "workspace", scione_auth_enabled=False)
    with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as client:
        empty = client.get("/api/workspace").json()
        assert empty["capability_creation"] == {
            "lab_id": empty["lab"]["id"], "lab_version": empty["lab"]["version"],
            "created_at": {},
        }
        first = save_new(client, body(empty["lab"]["version"]))
        saved = save_new(client, body(
            first["lab"]["version"], name="Second thermal exposure", code="LAB-SOP-18"
        ))
        lab = saved["lab"]
        expected_ids = [entry["id"] for entry in lab["capabilities"]]
        overview = client.get("/api/workspace").json()
        hint = overview["capability_creation"]
        assert hint == {
            "lab_id": lab["id"], "lab_version": lab["version"],
            "created_at": {
                expected_ids[0]: "2026-11-24T12:00:00+00:00",
                expected_ids[1]: "2026-11-24T12:01:00+00:00",
            },
        }
        for timestamp in hint["created_at"].values():
            assert datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0
        assert overview["lab"] == client.get("/api/lab").json() == lab
        assert all("created_at" not in entry for entry in lab["capabilities"])
        workspace = Workspace(settings)
        try:
            before_fingerprint = lab_fingerprint(workspace.labs.load_or_create())
            assert workspace.labs.creation_metadata(workspace.labs.load_or_create()) == hint
            assert lab_fingerprint(workspace.labs.load_or_create()) == before_fingerprint
        finally:
            workspace.library.store.close()
        invalid = client.post("/api/lab/capabilities", json={
            **body(lab["version"]), "created_at": "2000-01-01T00:00:00Z",
        })
        assert invalid.status_code == 422
        assert client.get("/api/workspace").json()["capability_creation"] == hint
    with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as restarted:
        after = restarted.get("/api/workspace").json()
        assert after["capability_creation"] == hint
        assert after["lab"] == lab
        assert [entry["id"] for entry in after["lab"]["capabilities"]] == expected_ids


def test_authenticated_workspace_creation_metadata_cannot_leak_other_labs(tmp_path):
    settings = configuration(tmp_path / "tenants", scione_auth_enabled=True)
    manager = AuthManager(settings)
    manager.initialize()
    registry = TenantWorkspaceRegistry(settings)
    for username in ("creation_first", "creation_second"):
        lab = manager.store.create_lab(username + " lab")
        registry.provision(lab.id)
        manager.store.mark_lab_provisioned(lab.id)
        manager.store.create_user(
            username, username, "lab", hash_password(PASSWORD), lab_id=lab.id
        )
    registry.close_all()
    manager.close()
    with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as client:
        signed = client.post("/api/auth/login", json={
            "username": "creation_first", "password": PASSWORD,
        })
        assert signed.status_code == 200
        headers = {"X-CSRF-Token": signed.json()["csrf_token"]}
        first_lab = client.get("/api/lab").json()
        created = save_new(client, body(first_lab["version"]), headers=headers)
        cap_id = created["lab"]["capabilities"][0]["id"]
        first_hint = client.get("/api/workspace").json()["capability_creation"]
        assert cap_id in first_hint["created_at"]
        signed = client.post("/api/auth/login", json={
            "username": "creation_second", "password": PASSWORD,
        })
        assert signed.status_code == 200
        second = client.get("/api/workspace").json()
        assert second["lab"]["capabilities"] == []
        assert second["capability_creation"]["created_at"] == {}
        assert cap_id not in str(second["capability_creation"])


def test_explicit_embedded_reset_moves_sidecar_with_archive_and_starts_empty(tmp_path):
    settings = configuration(tmp_path / "workspace", scione_auth_enabled=False)
    with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as client:
        empty = client.get("/api/workspace").json()
        created = save_new(client, body(empty["lab"]["version"]))
        identifier = created["lab"]["capabilities"][0]["id"]
        hint = client.get("/api/workspace").json()["capability_creation"]
        reset = client.post("/api/workspace/reset", json={"acknowledged": True})
        assert reset.status_code == 200, reset.text
        backup = Path(reset.json()["backup_path"]) / "library.sqlite3"
        with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as connection:
            original = connection.execute(
                "SELECT capability_id, created_at FROM lab_capability_creation"
            ).fetchall()
        assert original == [(identifier, hint["created_at"][identifier])]
        restarted = client.get("/api/workspace").json()
        assert restarted["lab"]["capabilities"] == []
        assert restarted["capability_creation"]["created_at"] == {}
        assert identifier not in str(restarted["capability_creation"])

"""Private capability CRUD enters matching without changing another lab or the shared bank."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from scione.api.app import create_app
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.api.workspace import Workspace
from scione.auth import AuthManager
from scione.auth.passwords import hash_password
from scione.catalogs import load_reference_catalog
from scione.catalogs.sql_lab_store import lab_revisions
from scione.config import Settings
from scione.library.store import LibraryStore
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.openai_compatible import OpenAICompatibleModelProvider
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore

PASSWORD = "Synthetic-private-capability-fixture-password"


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Capability CRUD, retrieval and saved comparisons must not call a model.")

    for provider in (GeminiModelProvider, GroqModelProvider, OpenAICompatibleModelProvider):
        monkeypatch.setattr(provider, "generate_structured", reject)


def configuration(root, **updates):
    return Settings(
        _env_file=None,
        scione_workspace_dir=root.resolve(),
        scione_database_url=None,
        scione_auth_database_url=None,
        groq_api_key=None,
        gemini_api_key=None,
        fireworks_api_key=None,
        sail_api_key=None,
        **updates,
    )


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "workspace"
    settings = configuration(root)
    with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as client:
        yield client, settings


def body(version, *, name="Custom cure exposure", code="LAB-SOP-17", test_id=None):
    payload = {
        "version": version,
        "capability": {
            "id": "client_draft",
            "canonical_test_id": test_id or "draft_test",
            "method_ids": [],
            "atomic_claims": [
                {
                    "id": "lab_temperature",
                    "parameter_id": "temperature",
                    "label": "Temperature",
                    "kind": "condition",
                    "stage": "general",
                    "operator": "range",
                    "minimum": "20",
                    "maximum": "70",
                    "unit": "°C",
                    "raw_text": "Temperature from 20 to 70 °C",
                }
            ],
        },
        "custom_methods": [{"name": "Controlled thermal exposure", "identifier": code}],
    }
    if test_id is None:
        payload["custom_test"] = {
            "name": name,
            "description": "A lab-defined exposure using an agreed temperature setting.",
            "aliases": ["Local cure exposure"],
        }
    return payload


def save_new(client, payload, **kwargs):
    response = client.post("/api/lab/capabilities", json=payload, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def seed_source(root, *, code="LAB-SOP-17"):
    text = f"Custom cure exposure: {code}. Hold at 25 °C."
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 60), text)
        source = FileSpecStore(root).import_pdf("private-cure-spec.pdf", pdf.tobytes())
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "document_id": source.document_id,
            "started_at": datetime.now(UTC),
            "schema_version": "0.1",
            "prompt_version": "offline_private_identity_fixture",
            "provider": "offline",
            "model": "no_network",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "Preserved offline extraction fixture",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Private procedure request"},
                "requirements": [
                    {
                        "id": "req_private_temperature",
                        "original_test_name": "Custom cure exposure",
                        "requested_methods": [{"code": code, "role": "primary"}],
                        "conditions": [{"name": "Temperature", "raw_value": "25", "unit": "°C"}],
                        "evidence": [{"page": 1, "quote": text}],
                        "atomic_claims": [
                            {
                                "id": "requested_temperature",
                                "parameter_id": "temperature",
                                "label": "Temperature",
                                "kind": "condition",
                                "stage": "general",
                                "operator": "=",
                                "value": "25",
                                "unit": "°C",
                                "raw_text": "25 °C",
                                "source_pages": [1],
                            }
                        ],
                    }
                ],
            },
        }
    )
    FileSpecStore(root).save_extraction(source.document_id, run)
    return source.document_id, run


def test_private_capability_crud_survives_restart_and_preserves_identity_history(api, tmp_path):
    client, settings = api
    base = load_reference_catalog().model_dump(mode="json")
    version = client.get("/api/lab").json()["version"]
    created = save_new(client, body(version))
    lab = created["lab"]
    capability = lab["capabilities"][0]
    test = lab["custom_tests"][0]
    method = lab["custom_methods"][0]
    assert capability["id"].startswith("cap_") and capability["id"] != "client_draft"
    assert test["id"].startswith("labtest_")
    assert method["id"].startswith("labmethod_")
    assert capability["canonical_test_id"] == test["id"]
    assert capability["method_ids"] == [method["id"]]
    assert any(item["id"] == test["id"] for item in created["catalog"]["tests"])
    assert load_reference_catalog().model_dump(mode="json") == base

    update = {
        "version": lab["version"],
        "capability": capability,
        "custom_test": {
            **{key: test[key] for key in ("id", "name", "description")},
            "name": "Renamed cure exposure",
            "aliases": ["Local cure exposure"],
        },
        "custom_methods": [
            {"id": method["id"], "identifier": "LAB-SOP-18", "name": "Updated controlled exposure"}
        ],
    }
    edited = client.put("/api/lab/capabilities/" + capability["id"], json=update)
    assert edited.status_code == 200, edited.text
    current = edited.json()["lab"]
    assert current["capabilities"][0]["id"] == capability["id"]
    assert current["custom_tests"][0]["id"] == test["id"]
    assert current["custom_tests"][0]["name"] == "Renamed cure exposure"
    assert current["custom_methods"][0]["identifier"] == "LAB-SOP-18"
    with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as reloaded:
        assert reloaded.get("/api/lab").json() == current
    stale = client.put("/api/lab/capabilities/" + capability["id"], json=update)
    assert stale.status_code == 409
    assert client.get("/api/lab").json() == current
    removed = client.request(
        "DELETE", "/api/lab/capabilities/" + capability["id"], json={"version": current["version"]}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["lab"]["capabilities"] == []
    assert removed.json()["lab"].get("custom_tests", []) == []
    assert removed.json()["lab"].get("custom_methods", []) == []
    assert not any(item["id"] == test["id"] for item in client.get("/api/catalog").json()["tests"])
    library = LibraryStore(settings.scione_workspace_dir)
    try:
        with library.engine.connect() as connection:
            preserved = connection.execute(
                select(lab_revisions.c.payload).where(
                    lab_revisions.c.workspace_id == "local_lab",
                    lab_revisions.c.version == lab["version"],
                )
            ).scalar_one()
        assert preserved["custom_tests"][0]["name"] == "Custom cure exposure"
        assert preserved["custom_methods"][0]["identifier"] == "LAB-SOP-17"
    finally:
        library.close()


def test_existing_method_codes_are_reused_and_private_methods_do_not_modify_shared_catalog(api):
    client, _settings = api
    original = client.get("/api/catalog").json()
    version = client.get("/api/lab").json()["version"]
    created = save_new(client, body(version, code="ISO 2813", test_id="gloss"))
    assert created["lab"].get("custom_methods", []) == []
    assert created["lab"]["capabilities"][0]["method_ids"] == ["iso_2813"]
    assert created["catalog"] == original
    created = save_new(client, body(created["lab"]["version"], test_id="gloss"))
    method = created["lab"]["custom_methods"][0]
    assert len(created["catalog"]["methods"]) == len(original["methods"]) + 1
    assert (
        method["id"]
        in next(test for test in created["catalog"]["tests"] if test["id"] == "gloss")["method_ids"]
    )
    again = save_new(client, body(created["lab"]["version"], test_id="gloss"))
    assert len(again["lab"]["custom_methods"]) == 1
    assert again["lab"]["capabilities"][-1]["method_ids"] == [method["id"]]
    assert load_reference_catalog().model_dump(mode="json") == original


def test_unrelated_shared_methods_are_rejected_without_partial_profile_changes(api):
    client, _settings = api
    lab = client.get("/api/lab").json()
    response = client.post(
        "/api/lab/capabilities",
        json=body(lab["version"], test_id="pencil_hardness", code="ISO 2813"),
    )
    assert response.status_code == 400, response.text
    assert client.get("/api/lab").json() == lab


def test_private_procedure_formatting_reuses_identity_and_reaches_the_matching_snapshot(api):
    client, settings = api
    created = save_new(client, body(client.get("/api/lab").json()["version"], code="TEST123"))
    capability = created["lab"]["capabilities"][0]
    method = created["lab"]["custom_methods"][0]
    again = save_new(
        client,
        body(created["lab"]["version"], code="TEST-123", test_id=capability["canonical_test_id"]),
    )
    assert len(again["lab"]["custom_methods"]) == 1
    assert again["lab"]["capabilities"][-1]["method_ids"] == [method["id"]]
    document_id, _run = seed_source(settings.scione_workspace_dir, code="TEST-123")
    workspace = Workspace(settings)
    try:
        snapshot = workspace.matching.snapshot(workspace.draft(document_id))
        assert capability["canonical_test_id"] in snapshot.requirements[0].catalog_test_ids
        assert method["id"] in {entry.id for entry in snapshot.catalog.methods}
    finally:
        workspace.library.store.close()
    response = client.get(f"/api/specifications/{document_id}/matching")
    assert response.status_code == 200, response.text
    assert response.json()["current_run_id"] is None


def test_private_method_rename_cannot_duplicate_an_existing_shared_method_code(api):
    client, _settings = api
    payload = body(client.get("/api/lab").json()["version"])
    payload["custom_methods"][0]["aliases"] = ["LOCAL-CURE-17"]
    created = save_new(client, payload)
    lab = created["lab"]
    capability = lab["capabilities"][0]
    method = lab["custom_methods"][0]
    response = client.put(
        "/api/lab/capabilities/" + capability["id"],
        json={
            "version": lab["version"],
            "capability": capability,
            "custom_methods": [
                {"id": method["id"], "name": "Conflicting method", "identifier": "ISO 2813"}
            ],
        },
    )
    assert response.status_code == 400, response.text
    assert client.get("/api/lab").json() == lab
    response = client.put(
        "/api/lab/capabilities/" + capability["id"],
        json={
            "version": lab["version"],
            "capability": capability,
            "custom_methods": [
                {
                    "id": method["id"],
                    "name": "Renamed local procedure",
                    "identifier": method["identifier"],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["lab"]["custom_methods"][0]["aliases"] == method["aliases"]


def test_private_identities_reach_candidates_numeric_comparison_and_immutable_exports(api):
    client, settings = api
    created = save_new(client, body(client.get("/api/lab").json()["version"]))
    capability = created["lab"]["capabilities"][0]
    document_id, original_run = seed_source(settings.scione_workspace_dir)
    workspace = Workspace(settings)
    try:
        draft = workspace.draft(document_id)
        snapshot = workspace.matching.snapshot(draft)
        assert capability["canonical_test_id"] in snapshot.requirements[0].catalog_test_ids
        assert capability["id"] in snapshot.requirements[0].capability_ids
        assert snapshot.catalog.method_by_id(capability["method_ids"][0]).identifier == "LAB-SOP-17"
    finally:
        workspace.library.store.close()
    response = client.get("/api/comparisons/" + document_id)
    assert response.status_code == 200, response.text
    compared = response.json()
    checks = compared["requirements"][0]["comparison"]["checks"]
    assert any(check["kind"] == "method" and check["status"] == "supported" for check in checks)
    assert any(
        check.get("parameter_id") == "temperature" and check["status"] == "supported"
        for check in checks
    )
    snapshot_id = compared.get("export_snapshot_id", compared["comparison_id"])
    export_url = "/api/comparison-snapshots/" + snapshot_id + "/json"
    exported = client.get(export_url)
    assert exported.status_code == 200
    before = exported.json()
    removed = client.request(
        "DELETE",
        "/api/lab/capabilities/" + capability["id"],
        json={"version": created["lab"]["version"]},
    )
    assert removed.status_code == 200, removed.text
    assert client.get(export_url).json() == before
    reloaded = FileSpecStore(settings.scione_workspace_dir).load_extraction(
        document_id, original_run.run_id
    )
    assert reloaded == original_run


def test_authenticated_labs_cannot_read_edit_or_reuse_another_labs_private_identity(tmp_path):
    settings = configuration(tmp_path / "tenants", scione_auth_enabled=True)
    manager = AuthManager(settings)
    manager.initialize()
    registry = TenantWorkspaceRegistry(settings)
    for username in ("first", "second"):
        lab = manager.store.create_lab(username + " lab")
        registry.provision(lab.id)
        manager.store.mark_lab_provisioned(lab.id)
        manager.store.create_user(username, username, "lab", hash_password(PASSWORD), lab_id=lab.id)
    registry.close_all()
    manager.close()
    app = create_app(settings, frontend_dir=tmp_path / "no-frontend")
    with TestClient(app) as client:
        signed = client.post("/api/auth/login", json={"username": "first", "password": PASSWORD})
        assert signed.status_code == 200, signed.text
        headers = {"X-CSRF-Token": signed.json()["csrf_token"]}
        first_lab = client.get("/api/lab").json()
        first = save_new(client, body(first_lab["version"]), headers=headers)
        capability = first["lab"]["capabilities"][0]
        test = first["lab"]["custom_tests"][0]
        method = first["lab"]["custom_methods"][0]
        signed = client.post("/api/auth/login", json={"username": "second", "password": PASSWORD})
        assert signed.status_code == 200, signed.text
        headers = {"X-CSRF-Token": signed.json()["csrf_token"]}
        second = client.get("/api/lab").json()
        catalog = client.get("/api/catalog").json()
        assert not any(item["id"] == test["id"] for item in catalog["tests"])
        assert not any(item["id"] == method["id"] for item in catalog["methods"])
        guessed = deepcopy(capability)
        update = {"version": second["version"], "capability": guessed}
        response = client.put(
            "/api/lab/capabilities/" + capability["id"], json=update, headers=headers
        )
        assert response.status_code == 404
        response = client.post("/api/lab/capabilities", json=update, headers=headers)
        assert response.status_code == 400
        assert client.get("/api/lab").json() == second
        response = client.request(
            "DELETE",
            "/api/lab/capabilities/" + capability["id"],
            json={"version": second["version"]},
            headers=headers,
        )
        assert response.status_code == 404

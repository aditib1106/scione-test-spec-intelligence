"""New providers use explicit consent and selected limits without real API calls."""

import importlib

import pytest
from test_semantic_matching_api import body_for, setup
from test_workspace_api import LocalHTTPClient, detailed_analysis_payload, seed_source

from scione.api.app import create_app
from scione.config import Settings
from scione.providers.static import StaticModelProvider
from scione.specifications.store import FileSpecStore

__all__ = ["setup"]


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
@pytest.mark.parametrize("old_google_flag", [False, True])
def test_new_analysis_provider_requires_its_own_usage_acknowledgment(
    tmp_path, monkeypatch, provider, old_google_flag
):
    root = tmp_path / "workspace"
    source, old, _ = seed_source(root)
    settings = Settings(_env_file=None, scione_workspace_dir=root, scione_database_url=None)
    api_module = importlib.import_module("scione.api.app")

    def forbid_factory(*_args, **_kwargs):
        pytest.fail("Provider construction must follow explicit provider usage consent.")

    monkeypatch.setattr(api_module, "create_configured_provider", forbid_factory)
    client = LocalHTTPClient(create_app(settings))
    response = client.post(
        f"/api/specifications/{source.document_id}/analysis",
        json={
            "provider": provider,
            "model": "offline-model",
            "strategy": "whole_document",
            "output_limit": 4096,
            "permission": True,
            "billing_acknowledged": False,
            "free_tier_confirmed": old_google_flag,
        },
    )
    assert response.status_code == 400
    assert FileSpecStore(root).load_latest_extraction(source.document_id).run_id == old.run_id


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
def test_new_analysis_provider_receives_selected_model_and_budget(tmp_path, monkeypatch, provider):
    root = tmp_path / "workspace"
    source, _old, _ = seed_source(root)
    settings = Settings(
        _env_file=None,
        scione_workspace_dir=root,
        scione_database_url=None,
        scione_single_attempt_mode=False,
    )
    api_module = importlib.import_module("scione.api.app")
    captured = []

    def factory(selected, *, provider_name, model_name):
        captured.append((selected, provider_name, model_name))
        return StaticModelProvider(detailed_analysis_payload(), model_name="resolved-offline-model")

    monkeypatch.setattr(api_module, "create_configured_provider", factory)
    client = LocalHTTPClient(create_app(settings))
    response = client.post(
        f"/api/specifications/{source.document_id}/analysis",
        json={
            "provider": provider,
            "model": "chosen-model",
            "strategy": "whole_document",
            "output_limit": 5120,
            "permission": True,
            "billing_acknowledged": True,
        },
    )
    assert response.status_code == 200, response.text
    selected, name, model = captured[0]
    assert name == provider and model == "chosen-model"
    assert getattr(selected, f"{provider}_max_output_tokens") == 5120
    assert getattr(selected, f"{provider}_temperature") == 0.0
    assert selected.scione_single_attempt_mode is True
    assert response.json()["run"]["model"] == "resolved-offline-model"


@pytest.mark.parametrize("provider", ["fireworks", "sail"])
def test_new_matching_provider_receives_selected_model_and_budget(setup, monkeypatch, provider):
    client, _workspace, document_id, fake, _settings = setup
    api_module = importlib.import_module("scione.api.app")
    captured = []

    def factory(selected, *, provider_name, model_name):
        captured.append((selected, provider_name, model_name))
        return fake

    monkeypatch.setattr(api_module, "create_configured_provider", factory)
    body = body_for(client, document_id, provider=provider, model="chosen-model", output_limit=6144)
    denied = client.post(
        f"/api/specifications/{document_id}/matching", json={**body, "permission": False}
    )
    assert denied.status_code == 400 and not captured
    response = client.post(f"/api/specifications/{document_id}/matching", json=body)
    assert response.status_code == 200, response.text
    selected, name, model = captured[0]
    assert name == provider and model == "chosen-model"
    assert getattr(selected, f"{provider}_max_output_tokens") == 6144
    assert getattr(selected, f"{provider}_temperature") == 0.0
    assert selected.scione_single_attempt_mode is True
    assert fake.calls == 1

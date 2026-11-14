"""In-process paid-operation gates and failed artifacts; no HTTP model calls."""

import asyncio
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

import httpx
import pytest
from test_semantic_matching_api import body_for, setup
from test_workspace_api import LocalHTTPClient, detailed_analysis_payload, seed_source

from scione.api.app import create_app
from scione.config import Settings
from scione.providers.base import ProviderError, StructuredGenerationResponse, TokenUsage
from scione.providers.static import StaticModelProvider
from scione.specifications.store import FileSpecStore, SpecStoreError

__all__ = ["setup"]


def analysis_body():
    return {
        "provider": "sail",
        "model": "requested-alias",
        "strategy": "whole_document",
        "output_limit": 4096,
        "permission": True,
        "billing_acknowledged": True,
    }


class FailingProvider(StaticModelProvider):
    def __init__(self, failure):
        super().__init__({}, model_name="requested-alias")
        self.failure = failure
        self.calls = 0
        # This intentionally sensitive attribute must never be serialized.
        self._api_key = "UNUSED_PRIVATE_FIXTURE_KEY"

    @property
    def generation_settings(self):
        return {"temperature": 0.0, "max_completion_tokens": 4096}

    def generate_structured(self, request):
        self.calls += 1
        if self.failure == "provider":
            raise ProviderError(
                "The structured response was truncated.",
                raw_text='{"private_customer_output":',
                provider="sail",
                model="actual-resolved-model",
                provider_request_id="provider-request-one",
                usage=TokenUsage(input_tokens=12, output_tokens=7, total_tokens=19),
            )
        payload = (
            {"wrong": "private_customer_output"}
            if self.failure == "schema"
            else {"schema_version": "0.1", "document": {}, "requirements": []}
        )
        return StructuredGenerationResponse(
            provider="sail",
            model="actual-resolved-model",
            raw_text=json.dumps(payload),
            parsed=payload,
            usage={"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
            latency_ms=1,
            provider_request_id="provider-request-one",
            generation_settings=self.generation_settings,
        )


@pytest.mark.parametrize("failure", ["provider", "schema", "empty"])
def test_failed_extraction_is_private_replayable_and_never_replaces_success(
    tmp_path, monkeypatch, failure
):
    root = tmp_path / "workspace"
    source, previous, lab_store = seed_source(root)
    settings = Settings(_env_file=None, scione_workspace_dir=root, scione_database_url=None)
    api = importlib.import_module("scione.api.app")
    provider = FailingProvider(failure)
    monkeypatch.setattr(api, "create_configured_provider", lambda *args, **kwargs: provider)
    client = LocalHTTPClient(create_app(settings))
    path = f"/api/specifications/{source.document_id}/analysis"
    first = client.post(path, json=analysis_body())
    assert first.status_code == 400
    assert provider.calls == 1
    assert "private_customer_output" not in first.text
    assert "No automatic retry" in first.text
    store = FileSpecStore(root)
    assert store.load_latest_extraction(source.document_id).run_id == previous.run_id
    metadata = store.load_failure(source.document_id)
    assert metadata.attempt_id
    artifact = store.load_failure_artifact(source.document_id, metadata.attempt_id)
    assert artifact.status == "failed" and len(artifact.calls) == 1
    assert artifact.requested_model == "requested-alias"
    assert artifact.generation_settings["temperature"] == 0.0
    call = artifact.calls[0]
    assert call.request.user_prompt and call.request.json_schema
    generated = call.failure if failure == "provider" else call.response
    assert generated.model == "actual-resolved-model"
    assert generated.provider_request_id == "provider-request-one"
    assert generated.usage.total_tokens == 19
    if failure != "empty":
        raw = generated.raw_text
        assert "private_customer_output" in raw
    text = artifact.model_dump_json()
    assert "UNUSED_PRIVATE_FIXTURE_KEY" not in text and "Authorization" not in text
    assert "raw_response" not in metadata.model_dump_json()
    download = client.get(
        f"/api/specifications/{source.document_id}/analysis-failures/{metadata.attempt_id}/artifacts"
    )
    assert download.status_code == 200
    assert download.json() == artifact.model_dump(mode="json")
    assert "attachment" in download.headers["content-disposition"]
    with pytest.raises(SpecStoreError, match="immutable"):
        store.save_failure(metadata, artifact=artifact)
    assert store.load_failure_artifact(source.document_id, metadata.attempt_id) == artifact
    second = client.post(path, json=analysis_body())
    assert second.status_code == 400 and provider.calls == 2  # Explicit retry only.
    assert store.load_failure(source.document_id).attempt_id != metadata.attempt_id
    assert store.load_failure_artifact(source.document_id, metadata.attempt_id) == artifact
    lab_store.library.close()


@pytest.mark.parametrize("active_stage", ["analysis", "matching"])
def test_both_paid_routes_share_the_document_gate_and_reads_still_work(
    setup, monkeypatch, active_stage
):
    client, workspace, document_id, fake, _settings = setup
    body = body_for(client, document_id)
    api = importlib.import_module("scione.api.app")
    constructed = []

    def forbidden_factory(*args, **kwargs):
        constructed.append(True)
        pytest.fail("A concurrent request must fail before provider construction.")

    monkeypatch.setattr(api, "create_configured_provider", forbidden_factory)
    with client.app.state.inference_gate.claim(document_id, active_stage):
        analysis = client.post(
            f"/api/specifications/{document_id}/analysis", json=analysis_body()
        )
        matching = client.post(f"/api/specifications/{document_id}/matching", json=body)
        assert analysis.status_code == matching.status_code == 409
        assert "No additional model call" in analysis.text
        assert client.get(f"/api/specifications/{document_id}").status_code == 200
        assert client.get(f"/api/specifications/{document_id}/matching").status_code == 200
    assert not constructed and fake.calls == 0
    monkeypatch.setattr(api, "create_configured_provider", lambda *args, **kwargs: fake)
    response = client.post(f"/api/specifications/{document_id}/matching", json=body)
    assert response.status_code == 200 and fake.calls == 1


def test_paid_route_error_releases_gate_for_explicit_next_request(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    source, _previous, _lab_store = seed_source(root)
    settings = Settings(_env_file=None, scione_workspace_dir=root, scione_database_url=None)
    api = importlib.import_module("scione.api.app")
    provider = FailingProvider("schema")
    monkeypatch.setattr(api, "create_configured_provider", lambda *args, **kwargs: provider)
    client = LocalHTTPClient(create_app(settings))
    path = f"/api/specifications/{source.document_id}/analysis"
    assert client.post(path, json=analysis_body()).status_code == 400
    provider = StaticModelProvider(detailed_analysis_payload())
    monkeypatch.setattr(api, "create_configured_provider", lambda *args, **kwargs: provider)
    assert client.post(path, json=analysis_body()).status_code == 200


def test_new_navigation_observer_attaches_without_starting_duplicate_work(setup, monkeypatch):
    client, _workspace, document_id, provider, _settings = setup
    body = body_for(client, document_id)
    entered, release = Event(), Event()
    original = provider.generate_structured

    def blocked_generation(request):
        entered.set()
        assert release.wait(timeout=10), "The offline inference fixture was not released."
        return original(request)

    monkeypatch.setattr(provider, "generate_structured", blocked_generation)
    path = f"/api/specifications/{document_id}/matching"
    with ThreadPoolExecutor(max_workers=1) as executor:
        submitted = executor.submit(client.post, path, json=body)
        try:
            assert entered.wait(timeout=10)
            # A new observer represents leaving/reopening a page or reloading;
            # it does not own, cancel, or restart the accepted paid operation.
            reopened = LocalHTTPClient(client.app)
            active = reopened.get("/api/operations")
            assert active.status_code == 200
            operation = active.json()["active"][0]
            assert operation["document_id"] == document_id and operation["kind"] == "matching"
            status = reopened.get(f"/api/specifications/{document_id}/operations").json()
            assert status["state"] == "running"
            assert status["active"][0]["operation_id"] == operation["operation_id"]
            assert status["latest"]["status"] == "running"
            assert reopened.get(f"/api/specifications/{document_id}").status_code == 200
            duplicate = reopened.post(path, json=body)
            assert duplicate.status_code == 409
            assert "No additional model call" in duplicate.text
            assert provider.calls == 0  # One blocked fixture, no second admission.
        finally:
            release.set()
        response = submitted.result(timeout=10)
    assert response.status_code == 200 and provider.calls == 1
    final = client.get(f"/api/specifications/{document_id}/operations").json()
    assert final["state"] == "idle" and final["active"] == []
    assert final["latest"]["status"] == "success"
    assert final["latest"]["run_id"] == response.json()["run_id"]


def test_disconnected_http_observer_does_not_cancel_an_admitted_worker(setup, monkeypatch):
    client, _workspace, document_id, provider, _settings = setup
    body = body_for(client, document_id)
    entered, release = Event(), Event()
    original = provider.generate_structured

    def blocked_generation(request):
        entered.set()
        assert release.wait(timeout=10)
        return original(request)

    monkeypatch.setattr(provider, "generate_structured", blocked_generation)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
        ) as observer:
            pending = asyncio.create_task(observer.post(
                f"/api/specifications/{document_id}/matching", json=body
            ))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                pending.cancel()  # Equivalent to losing this HTTP response observer.
                await asyncio.sleep(0)
                response = await observer.get(f"/api/specifications/{document_id}/operations")
                assert response.status_code == 200 and response.json()["state"] == "running"
                duplicate = await observer.post(
                    f"/api/specifications/{document_id}/matching", json=body
                )
                assert duplicate.status_code == 409
            finally:
                release.set()
                with suppress(asyncio.CancelledError):
                    await pending
            # The accepted synchronous worker may finish after its HTTP task.
            # Observe bounded completion; never resubmit inference to recover it.
            for _ in range(100):
                status = await observer.get(f"/api/specifications/{document_id}/operations")
                if not status.json()["active"]:
                    break
                await asyncio.sleep(0.01)
            assert status.json()["state"] == "idle"
            assert status.json()["latest"]["status"] == "success"
            assert status.json()["latest"]["run_id"]
            assert provider.calls == 1

    asyncio.run(scenario())

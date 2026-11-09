"""Explicit API runs propagate effective provider settings into cache selection."""

from test_semantic_matching_api import body_for
from test_semantic_matching_api import setup as matching_context

setup = matching_context


def test_new_generation_settings_make_a_new_call_but_unchanged_settings_reuse(setup, monkeypatch):
    client, workspace, document_id, provider, _settings = setup
    selected = {"temperature": 0.0, "max_output_tokens": 8192, "reasoning_effort": "low"}
    monkeypatch.setattr(
        type(provider), "generation_settings", property(lambda _self: dict(selected))
    )
    body = body_for(client, document_id, output_limit=8192)
    first = client.post(f"/api/specifications/{document_id}/matching", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False

    same = client.post(f"/api/specifications/{document_id}/matching", json=body)
    assert same.status_code == 200, same.text
    assert same.json()["cached"] is True
    assert same.json()["run_id"] == first.json()["run_id"]
    assert provider.calls == 1

    selected["max_output_tokens"] = 16384
    changed_body = {**body, "output_limit": 16384}
    changed = client.post(f"/api/specifications/{document_id}/matching", json=changed_body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["cached"] is False
    assert changed.json()["run_id"] != first.json()["run_id"]
    assert provider.calls == 2
    assert client.post(
        f"/api/specifications/{document_id}/matching", json=changed_body
    ).json()["cached"] is True
    assert provider.calls == 2

    # Find the original matching profile even though the newest row differs.
    selected["max_output_tokens"] = 8192
    original_profile = client.post(f"/api/specifications/{document_id}/matching", json=body)
    assert original_profile.status_code == 200, original_profile.text
    assert original_profile.json()["cached"] is True
    assert original_profile.json()["run_id"] == first.json()["run_id"]
    assert provider.calls == 2
    assert len(workspace.matching.store.listing(document_id)) == 2

"""Human-readable reviewer names are recorded by the server, never re-derived."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from test_library_api import _seed
from test_library_api import client as client
from test_related_review_references import _prior, _query, _setup

from scione.api.app import create_app
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth import AuthManager
from scione.auth.passwords import hash_password
from scione.auth.store import users
from scione.config import Settings
from scione.library.assessment_reviews import SQLAssessmentReviews, reviews
from scione.library.store import LibraryStore, digest
from scione.matching.service import SemanticMatchingRunner
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.openai_compatible import OpenAICompatibleModelProvider


@pytest.fixture(autouse=True)
def forbid_inference(monkeypatch):
    def reject(*_args, **_kwargs):
        pytest.fail("Reviewer names and history must never request model inference.")

    monkeypatch.setattr(SemanticMatchingRunner, "run", reject)
    for provider in (GroqModelProvider, GeminiModelProvider, OpenAICompatibleModelProvider):
        monkeypatch.setattr(provider, "generate_structured", reject)


def _review_body(machine, *, version=0):
    return {
        "comparison_id": machine["comparison_id"],
        "input_fingerprint": machine["report_input_fingerprint"],
        "requirement_id": "req_gloss", "expected_version": version,
        "review_status": "accepted", "decision": "keep", "note": "Checked the saved finding.",
    }


def _copy_machine(store, machine, title):
    payload = deepcopy(machine)
    for key in ("comparison_id", "created_at"):
        payload.pop(key, None)
    payload["document"]["title"] = title
    return store.save_comparison(digest(payload), payload)


def test_authenticated_reviewer_name_is_server_owned_frozen_and_tenant_private(tmp_path):
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path.resolve(), scione_database_url=None,
        scione_auth_database_url=None, scione_auth_enabled=True,
        groq_api_key=None, gemini_api_key=None, fireworks_api_key=None, sail_api_key=None,
    )
    password = "Offline assessment label test passphrase!"
    manager = AuthManager(settings)
    manager.initialize()
    registry = TenantWorkspaceRegistry(settings)
    reports, user_ids = {}, {}
    for index, lab_id in enumerate(("c" * 32, "d" * 32)):
        manager.store.create_lab(f"Reviewer label lab {index}", lab_id)
        registry.provision(lab_id)
        manager.store.mark_lab_provisioned(lab_id)
        user = manager.store.create_user(
            f"label-reviewer-{index}", f"Reviewer {index} original name", "lab",
            hash_password(password), lab_id=lab_id,
        )
        user_ids[index] = user["id"]
        workspace = registry.workspace_for(lab_id)
        earlier, _ = _seed(workspace.settings.scione_workspace_dir, "Earlier")
        current, _ = _seed(workspace.settings.scione_workspace_dir, "Current")
        reports[index] = (
            workspace.library.comparison(earlier.document_id),
            workspace.library.comparison(current.document_id),
        )
    registry.close_all()
    try:
        with TestClient(create_app(settings, frontend_dir=tmp_path / "no-frontend")) as http:
            for index in (0, 1):
                signed = http.post("/api/auth/login", json={
                    "username": f"label-reviewer-{index}", "password": password,
                })
                assert signed.status_code == 200, signed.text
                headers = {"X-CSRF-Token": signed.json()["csrf_token"]}
                previous, current = reports[index]
                document_id = previous["document"]["document_id"]
                path = f"/api/comparisons/{document_id}/reviews"
                original_machine = http.get(
                    f"/api/comparison-snapshots/{previous['comparison_id']}/json"
                ).json()
                forged = http.post(path, json={
                    **_review_body(previous), "actor_label": "Forged reviewer name",
                }, headers=headers)
                assert forged.status_code == 422
                assert http.get(path).json()["events"] == []
                saved = http.post(path, json=_review_body(previous), headers=headers)
                assert saved.status_code == 200, saved.text
                event = saved.json()["assessment_reviews"]["req_gloss"]
                assert event["actor"] == user_ids[index]
                assert event["actor_label"] == f"Reviewer {index} original name"
                snapshot_id = saved.json()["export_snapshot_id"]
                frozen = http.get(f"/api/comparison-snapshots/{snapshot_id}/json").json()
                assert frozen["assessment_reviews"]["req_gloss"] == event
                assert http.get(
                    f"/api/comparison-snapshots/{previous['comparison_id']}/json"
                ).json() == original_machine
                with manager.store.engine.begin() as connection:
                    connection.execute(update(users).where(users.c.id == user_ids[index]).values(
                        display_name=f"Reviewer {index} later name"
                    ))
                # The next authenticated request sees the new profile, while
                # the old event and already exported snapshot keep the old name.
                assert http.get(path).json()["events"] == [event]
                assert http.get(f"/api/comparison-snapshots/{snapshot_id}/json").json() == frozen
                again = http.post(path, json=_review_body(previous, version=1), headers=headers)
                assert again.status_code == 200, again.text
                latest = again.json()["assessment_reviews"]["req_gloss"]
                assert latest["actor"] == event["actor"]
                assert latest["actor_label"] == f"Reviewer {index} later name"
                assert http.get(path).json()["events"] == [event, latest]
                current_document = current["document"]["document_id"]
                related = http.get(
                    f"/api/comparisons/{current_document}/related-reviews", params=_query(current),
                )
                assert related.status_code == 200, related.text
                references = related.json()["entries"]
                assert len(references) == 1
                assert references[0]["review"]["actor_label"] == f"Reviewer {index} later name"
                assert f"Reviewer {1 - index}" not in related.text
                assert user_ids[1 - index] not in related.text
                http.post("/api/auth/logout", headers=headers)
    finally:
        manager.close()


@pytest.mark.parametrize("legacy", [False, True], ids=["recorded-label", "old-missing-label"])
def test_carryforward_preserves_stored_label_or_legacy_absence_without_rewriting_history(
    client, legacy,
):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    try:
        machine, event = _prior(store, original)
        assert event["actor_label"] == "Local reviewer"
        if legacy:
            event.pop("actor_label")
            # Fixture representing a pre-label event; production migration never
            # updates a historical event or looks its actor up in the user table.
            with store.engine.begin() as connection:
                connection.execute(update(reviews).where(
                    reviews.c.workspace_id == "local_lab", reviews.c.review_id == event["review_id"]
                ).values(payload=event))
        review_store = SQLAssessmentReviews(store, "local_lab")
        assert review_store.history(machine["comparison_id"]) == [event]
        copied_machine = _copy_machine(store, machine, "Display title changed only")
        carried = review_store.latest_for_assessment(copied_machine)["req_gloss"]
        assert carried.get("actor_label") == event.get("actor_label")
        assert ("actor_label" in carried) is not legacy
        assert carried["carried_from_review_id"] == event["review_id"]
        assert review_store.history(machine["comparison_id"]) == [event]
        result = http.get(
            f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report),
        )
        assert result.status_code == 200, result.text
        assert len(result.json()["entries"]) == 1
        reference = result.json()["entries"][0]["review"]
        assert reference.get("actor_label") == event.get("actor_label")
        assert ("actor_label" in reference) is not legacy
        assert store.comparison(machine["comparison_id"]) == machine
    finally:
        store.close()


def test_related_references_reject_a_carried_event_with_a_changed_reviewer_label(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    try:
        machine, original_event = _prior(store, original)
        copied_machine = _copy_machine(store, machine, "New display metadata")
        tampered = {
            **original_event, "review_id": str(uuid4()),
            "comparison_id": copied_machine["comparison_id"],
            "created_at": "2099-01-01T00:00:00+00:00",
            "actor_label": "Changed provenance label",
            "carried_from_review_id": original_event["review_id"],
            "carried_from_comparison_id": original_event["comparison_id"],
        }
        with store.engine.begin() as connection:
            connection.execute(insert(reviews).values(
                workspace_id="local_lab", review_id=tampered["review_id"],
                comparison_id=copied_machine["comparison_id"], requirement_id="req_gloss",
                version=1, created_at=tampered["created_at"], payload=tampered,
            ))
        before = SQLAssessmentReviews(store, "local_lab").history(
            copied_machine["comparison_id"]
        )
        response = http.get(
            f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report),
        )
        assert response.status_code == 200, response.text
        references = response.json()["entries"]
        assert len(references) == 1
        assert references[0]["review"]["review_id"] == original_event["review_id"]
        assert "Changed provenance label" not in response.text
        with store.engine.connect() as connection:
            assert connection.execute(select(reviews.c.payload).where(
                reviews.c.workspace_id == "local_lab", reviews.c.review_id == tampered["review_id"],
            )).scalar_one() == before[0]
    finally:
        store.close()

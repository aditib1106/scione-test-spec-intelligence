"""Exact historical references never become a new source or capability decision."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event
from sqlalchemy import func, insert, select
from test_library_api import _seed
from test_library_api import client as client

from scione.api.app import create_app
from scione.api.contracts import AssessmentReview
from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth import AuthManager
from scione.auth.passwords import hash_password
from scione.config import Settings
from scione.library.assessment_reviews import SQLAssessmentReviews, reviews
from scione.library.related_reviews import related_review_references
from scione.library.store import LibraryStore, comparisons, digest
from scione.matching.service import SemanticMatchingRunner
from scione.providers.openai_compatible import OpenAICompatibleModelProvider
from scione.specifications.store import FileSpecStore


@pytest.fixture(autouse=True)
def forbid_new_inference(monkeypatch):
    def reject(*_args, **_kwargs):
        pytest.fail("Historical references must not invoke a model or semantic matching runner.")

    monkeypatch.setattr(SemanticMatchingRunner, "run", reject)
    monkeypatch.setattr(OpenAICompatibleModelProvider, "generate_structured", reject)


def _query(report, requirement_id="req_gloss"):
    return {
        "requirement_id": requirement_id,
        "comparison_id": report["comparison_id"],
        "input_fingerprint": report["report_input_fingerprint"],
    }


def _body(machine, *, version=0, **changes):
    return AssessmentReview(
        comparison_id=machine["comparison_id"],
        input_fingerprint=machine["report_input_fingerprint"],
        requirement_id="req_gloss", expected_version=version,
        review_status=changes.pop("review_status", "accepted"),
        decision=changes.pop("decision", "decline"),
        note=changes.pop("note", "A prior lab limitation; review this request independently."),
        **changes,
    )


def _prior(store, base, *, workspace_id="local_lab", name=None, methods=None, **review_changes):
    payload = deepcopy(base)
    for key in ("comparison_id", "created_at"):
        payload.pop(key, None)
    row = next(row for row in payload["requirements"] if row["requirement_id"] == "req_gloss")
    if name is not None:
        row["test_name"] = name
    if methods is not None:
        row["methods"] = methods
    payload["test_fixture_unique"] = uuid4().hex
    machine = store.save_comparison(digest(payload), payload)
    selected = next(row for row in machine["requirements"] if row["requirement_id"] == "req_gloss")
    review = SQLAssessmentReviews(store, workspace_id).save(
        machine, selected, _body(machine, **review_changes)
    )
    return machine, review


def _setup(client):
    http, root = client
    previous, _ = _seed(root, "Earlier")
    previous_live = http.get(f"/api/comparisons/{previous.document_id}").json()
    original = http.get(
        f"/api/comparison-snapshots/{previous_live['comparison_id']}/json"
    ).json()
    current, _ = _seed(root, "Current")
    response = http.get(f"/api/comparisons/{current.document_id}")
    assert response.status_code == 200, response.text
    return http, root, previous, original, current, response.json()


def _counts(store):
    with store.engine.connect() as connection:
        return tuple(
            connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in (reviews, comparisons)
        )


def test_exact_prior_review_returns_safe_excerpt_without_writes_or_changed_findings(client):
    http, root, previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    # Deliberately untrusted extra artifact fields must not appear in the reference.
    original["provider_response"] = "private raw provider response"
    original["private_url"] = "private storage URL"
    original["document"]["company_name"] = "Previous customer"
    original["requirements"][0]["comparison"]["checks"][0]["requirement_claim"] = {
        "raw_text": "private raw claim body"
    }
    machine, recorded = _prior(store, original)
    before = _counts(store)
    source_before = http.get(f"/api/specifications/{current.document_id}").json()
    result = http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["reference_only"] is True
    assert payload["document_id"] == current.document_id
    assert payload["comparison_id"] == report["comparison_id"]
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["review"]["review_id"] == recorded["review_id"]
    assert entry["review"]["decision"] == "decline"
    assert entry["review"]["review_status"] == "accepted"
    assert entry["document"]["document_id"] == previous.document_id
    assert entry["methods"] == ["ASTM D1627-16"]
    assert entry["same_source"] is False
    assert entry["customer_context"] == "unassigned"
    assert entry["comparison"]["status"] == machine["requirements"][0]["comparison"]["status"]
    assert entry["comparison"]["checks"]
    for secret in (
        "private raw provider response", "private storage URL", "private raw claim body",
    ):
        assert secret not in result.text
    assert _counts(store) == before
    assert http.get(f"/api/specifications/{current.document_id}").json() == source_before
    after = http.get(f"/api/comparisons/{current.document_id}").json()
    assert after == report
    assert http.get(f"/api/comparison-snapshots/{machine['comparison_id']}/json").json() == machine
    store.close()


def test_exact_name_method_set_and_edition_only_no_fuzzy_alias_transfer(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    good, _ = _prior(store, original, name="  SPECULAR   GLOSS ", methods=[" astm   D1627-16 "])
    for name, methods in (
        ("Gloss", ["ASTM D1627-16"]),
        ("Specular gloss", ["ASTM D1627-17"]),
        ("Specular gloss", ["ASTMD1627-16"]),
        ("Specular gloss", ["ASTM D1627-16", "ISO 2813"]),
        ("Specular gloss", []),
    ):
        _prior(store, original, name=name, methods=methods)
    response = http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    )
    assert response.status_code == 200, response.text
    assert [entry["review"]["comparison_id"] for entry in response.json()["entries"]] == [
        good["comparison_id"]
    ]
    store.close()


def test_archived_foreign_workspace_and_same_document_reviews_are_not_references(client):
    http, root, previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    _prior(store, original, workspace_id="another-lab", note="Not public or visible to this lab.")
    current_machine = http.get(f"/api/comparison-snapshots/{report['comparison_id']}/json").json()
    _prior(store, current_machine, note="This source's own history is not a related document.")
    response = http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    )
    assert response.status_code == 200 and response.json()["entries"] == []
    assert "Not public" not in response.text
    _prior(store, original)
    assert http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    ).json()["entries"]
    record = store.document(previous.document_id)
    store.set_document_archived(
        previous.document_id, archived=True, expected_version=record["version"]
    )
    assert http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    ).json()["entries"] == []
    store.close()


def test_latest_event_per_assessment_and_pending_decision_remain_explicit(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    machine, _first = _prior(store, original, decision="keep")
    requirement = machine["requirements"][0]
    newest = SQLAssessmentReviews(store, "local_lab").save(
        machine, requirement,
        _body(machine, version=1, review_status="pending", decision="clarify_customer"),
    )
    result = http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    ).json()
    assert len(result["entries"]) == 1
    assert result["entries"][0]["review"]["review_id"] == newest["review_id"]
    assert result["entries"][0]["review"]["review_status"] == "pending"
    assert result["entries"][0]["review"]["decision"] == "clarify_customer"
    store.close()


def test_different_customer_and_changed_lab_are_context_not_current_capability_proof(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    original["document"]["company_name"] = "Prior customer"
    _prior(store, original)
    metadata = http.get(f"/api/library/{current.document_id}").json()["document"]
    response = http.put(f"/api/library/{current.document_id}/metadata", json={
        "expected_version": metadata["version"], "company_name": "Current customer",
        "specification_id": metadata["specification_id"], "title": metadata["title"],
        "revision": metadata["revision"],
    })
    assert response.status_code == 200, response.text
    lab = http.get("/api/lab").json()
    lab["description"] = "Current lab declaration differs from the historical assessment."
    assert http.put("/api/lab", json=lab).status_code == 200
    updated = http.get(f"/api/comparisons/{current.document_id}").json()
    path = f"/api/comparisons/{current.document_id}/related-reviews"
    assert http.get(path, params=_query(report)).status_code == 409
    current_result = http.get(path, params=_query(updated))
    assert current_result.status_code == 200, current_result.text
    entry = current_result.json()["entries"][0]
    assert entry["customer_context"] == "different"
    assert entry["different_customer"] is True
    assert entry["lab_changed"] is True
    assert entry["lab_version"] != updated["lab_version"]
    assert entry["same_source"] is False
    assert http.get(f"/api/comparisons/{current.document_id}").json() == updated
    store.close()


def test_carryforward_aliases_are_deduplicated_without_applying_any_previous_decision(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    _machine, previous_event = _prior(store, original)
    for _ in range(2):
        body = deepcopy(original)
        body["display_fixture_unique"] = uuid4().hex
        machine = store.save_comparison(digest(body), body)
        copied = {
            **previous_event, "review_id": str(uuid4()), "comparison_id": machine["comparison_id"],
            "created_at": machine["created_at"], "version": 1,
            "carried_from_review_id": previous_event["review_id"],
            "carried_from_comparison_id": previous_event["comparison_id"],
        }
        with store.engine.begin() as connection:
            connection.execute(insert(reviews).values(
                workspace_id="local_lab", review_id=copied["review_id"],
                comparison_id=copied["comparison_id"], requirement_id="req_gloss",
                version=1, created_at=copied["created_at"], payload=copied,
            ))
        previous_event = copied
    before = _counts(store)
    result = http.get(
        f"/api/comparisons/{current.document_id}/related-reviews", params=_query(report)
    )
    assert result.status_code == 200, result.text
    assert len(result.json()["entries"]) == 1
    assert _counts(store) == before
    assert http.get(f"/api/comparisons/{current.document_id}").json() == report
    store.close()


def test_bound_stale_inputs_unknown_requirement_and_methodless_current_test(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    _prior(store, original)
    path = f"/api/comparisons/{current.document_id}/related-reviews"
    for changes in ({"comparison_id": "old"}, {"input_fingerprint": "old"}):
        assert http.get(path, params={**_query(report), **changes}).status_code == 409
    assert http.get(path, params=_query(report, "not-a-current-requirement")).status_code == 400
    assert http.get(
        "/api/comparisons/ffffffffffffffff/related-reviews", params=_query(report)
    ).status_code == 404
    specs = FileSpecStore(root)
    run = specs.load_latest_extraction(current.document_id).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].requested_methods = []
    specs.save_extraction(current.document_id, run)
    changed = http.get(f"/api/comparisons/{current.document_id}").json()
    assert changed["requirements"][0]["methods"] == []
    assert http.get(path, params=_query(changed)).json()["entries"] == []
    store.close()


def test_result_bound_and_query_order_only_scalar_metadata(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    for index in range(12):
        _prior(store, original, note=f"Independent saved decision {index}.")
    # Inspect the actual SELECT passed to this disposable SQLite engine. The
    # same SQLAlchemy query compiles for MySQL without sorting artifact JSON.
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if "latest_related_review" in statement:
            statements.append(statement)

    sql_event.listen(store.engine, "before_cursor_execute", capture)
    workspace = SimpleNamespace(
        specs=FileSpecStore(root),
        library=SimpleNamespace(store=store, comparison=lambda _: report),
        labs=SimpleNamespace(workspace_id="local_lab"),
    )
    try:
        result = related_review_references(workspace, current.document_id, **_query(report))
    finally:
        sql_event.remove(store.engine, "before_cursor_execute", capture)
    assert len(result["entries"]) == 10
    assert len(statements) == 1
    assert "length(comparison_runs.payload) AS stored_length" in statements[0]
    assert "payload" not in statements[0].replace(
        "length(comparison_runs.payload) AS stored_length", "stored_length"
    )
    assert "LIMIT" in statements[0]
    store.close()


def _capture_snapshot_reads(store, report, root, document_id):
    reads = []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        if " ".join(statement.split()).startswith("SELECT comparison_runs.payload FROM"):
            reads.append(parameters[0])

    sql_event.listen(store.engine, "before_cursor_execute", capture)
    workspace = SimpleNamespace(
        specs=FileSpecStore(root),
        library=SimpleNamespace(store=store, comparison=lambda _: deepcopy(report)),
        labs=SimpleNamespace(workspace_id="local_lab"),
    )
    before = deepcopy(report)
    try:
        result = related_review_references(workspace, document_id, **_query(report))
    finally:
        sql_event.remove(store.engine, "before_cursor_execute", capture)
    assert report == before
    return result, reads


def test_oversized_history_is_skipped_before_loading_json_without_truncating_other_findings(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    oversized = deepcopy(original)
    oversized["old_large_artifact"] = "x" * 1_000_001
    huge, _ = _prior(store, oversized)
    small, _ = _prior(store, original)
    before = _counts(store)
    result, reads = _capture_snapshot_reads(store, report, root, current.document_id)
    assert result["history_limited"] is True
    assert huge["comparison_id"] not in reads
    assert reads == [small["comparison_id"]]
    assert len(result["entries"]) == 1
    excerpt = result["entries"][0]["comparison"]
    old_requirement = small["requirements"][0]["comparison"]
    assert excerpt["status"] == old_requirement["status"]
    assert len(excerpt["checks"]) == len(old_requirement["checks"])
    assert [check["requested"] for check in excerpt["checks"]] == [
        check["requested"] for check in old_requirement["checks"]
    ]
    assert _counts(store) == before
    assert http.get(f"/api/comparisons/{current.document_id}").json() == report
    store.close()


def test_total_unique_machine_stored_length_is_budgeted_before_payload_reads(client):
    http, root, _previous, original, current, report = _setup(client)
    store = LibraryStore(root)
    padded = deepcopy(original)
    padded["old_large_artifact"] = "x" * 800_000
    machines = [_prior(store, padded)[0] for _ in range(6)]
    identifiers = [machine["comparison_id"] for machine in machines]
    with store.engine.connect() as connection:
        sizes = dict(connection.execute(
            select(comparisons.c.comparison_id, func.length(comparisons.c.payload))
            .where(comparisons.c.comparison_id.in_(identifiers))
        ).all())
    assert all(size < 1_000_000 for size in sizes.values())
    result, reads = _capture_snapshot_reads(store, report, root, current.document_id)
    assert result["history_limited"] is True
    assert len(reads) == len(set(reads)) == 4
    assert len(result["entries"]) == 4
    assert sum(sizes[identifier] for identifier in reads) <= 4_000_000
    skipped = set(identifiers) - set(reads)
    assert len(skipped) == 2
    assert all(
        sum(sizes[identifier] for identifier in reads) + sizes[identifier] > 4_000_000
        for identifier in skipped
    )
    assert all(entry["comparison"]["checks"] for entry in result["entries"])
    assert http.get(f"/api/comparisons/{current.document_id}").json() == report
    store.close()


def test_authenticated_labs_cannot_read_each_others_historical_references(tmp_path):
    root = tmp_path.resolve()
    settings = Settings(
        _env_file=None, scione_workspace_dir=root, scione_database_url=None,
        scione_auth_database_url=None, scione_auth_enabled=True,
        groq_api_key=None, gemini_api_key=None, fireworks_api_key=None, sail_api_key=None,
    )
    manager = AuthManager(settings)
    manager.initialize()
    registry = TenantWorkspaceRegistry(settings)
    reports = {}
    for index, lab_id in enumerate(("a" * 32, "b" * 32)):
        manager.store.create_lab(f"Lab {index}", lab_id)
        registry.provision(lab_id)
        manager.store.mark_lab_provisioned(lab_id)
        manager.store.create_user(
            f"related-{index}", f"Lab {index}", "lab",
            hash_password("Offline reference test passphrase!"), lab_id=lab_id,
        )
        workspace = registry.workspace_for(lab_id)
        previous, _ = _seed(workspace.settings.scione_workspace_dir, "Earlier")
        old_report = workspace.library.comparison(previous.document_id)
        original = workspace.library.store.comparison(old_report["comparison_id"])
        _prior(
            workspace.library.store, original, workspace_id=workspace.labs.workspace_id,
            note=f"Private limitation from Lab {index}.",
        )
        current, _ = _seed(workspace.settings.scione_workspace_dir, "Current")
        reports[index] = workspace.library.comparison(current.document_id)
    manager.close()
    registry.close_all()
    app = create_app(settings, frontend_dir=root / "no-frontend")
    with TestClient(app) as client:
        for index in (0, 1):
            login = client.post("/api/auth/login", json={
                "username": f"related-{index}", "password": "Offline reference test passphrase!",
            })
            assert login.status_code == 200, login.text
            current = reports[index]
            document_id = current["document"]["document_id"]
            result = client.get(
                f"/api/comparisons/{document_id}/related-reviews", params=_query(current)
            )
            assert result.status_code == 200, result.text
            assert f"Private limitation from Lab {index}." in result.text
            assert f"Private limitation from Lab {1 - index}." not in result.text
            # Even the other lab's assessment ID cannot select its business DB.
            other = reports[1 - index]
            foreign = client.get(
                f"/api/comparisons/{document_id}/related-reviews", params=_query(other)
            )
            assert foreign.status_code == 409
            assert f"Private limitation from Lab {1 - index}." not in foreign.text
            client.post("/api/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})

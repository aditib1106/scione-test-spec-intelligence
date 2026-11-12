"""Parameter review remains source-owned, editable and distinct from machine output."""

from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from test_semantic_matching_api import SOURCE, make_pdf, setup  # noqa: F401

from scione.library.store import runs
from scione.specifications.models import RequirementEvidence
from scione.structured import normalize_requirement
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim

__all__ = ["setup"]


def edit_parameters(client, document_id, change):
    data = client.get(f"/api/specifications/{document_id}").json()
    requirements = deepcopy(data["run"]["extraction"]["requirements"])
    change(requirements[0])
    return client.post(
        f"/api/specifications/{document_id}/facts",
        json={
            "run_id": data["run"]["run_id"],
            "expected_version": data["facts_version"],
            "expected_review_version": data["review"]["version"],
            "requirements": requirements,
            "note": "Checked and corrected this parameter against its source quotation.",
        },
    )


def source_atom(**updates):
    if "value" in updates:
        updates["value"] = Decimal(str(updates["value"]))
    return (
        AtomicClaim(
            id="human_atom_temperature",
            parameter_id="temperature",
            label="Operating temperature",
            operator="=",
            value=23,
            unit="°C",
            raw_text=SOURCE,
            source_pages=[1],
        )
        .model_copy(update=updates)
        .model_dump(mode="json")
    )


def test_normalized_parameter_view_and_explicit_atomic_correction_preserve_machine_source(setup):
    client, workspace, doc, provider, _settings = setup
    machine = workspace.specs.load_latest_extraction(doc).model_dump(mode="json")
    data = client.get(f"/api/specifications/{doc}").json()
    claims = data["normalized_requirements"]["req_pencil"]["claims"]
    assert any(
        row["parameter_id"] == "temperature" and row["display_value"] == "23 °C" for row in claims
    )
    result = edit_parameters(client, doc, lambda row: row.update(atomic_claims=[source_atom()]))
    assert result.status_code == 200, result.text
    assert result.json()["facts_version"] == 1
    normalized = result.json()["normalized_requirements"]["req_pencil"]["claims"]
    assert any(claim["source_atom_ids"] == ["human_atom_temperature"] for claim in normalized)
    saved_id = result.json()["run"]["extraction"]["requirements"][0]["atomic_claims"][0]["id"]
    assert saved_id.startswith("human_atom_")
    assert workspace.specs.load_latest_extraction(doc).model_dump(mode="json") == machine
    assert provider.calls == 0


def test_parameter_number_not_in_owned_quote_is_rejected_without_revision(setup):
    client, workspace, doc, _provider, _settings = setup
    result = edit_parameters(
        client, doc, lambda row: row.update(atomic_claims=[source_atom(value=999)])
    )
    assert result.status_code == 400
    assert "not in this parameter's source quotation" in result.json()["detail"]
    run = workspace.specs.load_latest_extraction(doc)
    assert workspace.facts.latest(doc, run.run_id) is None


def test_changed_parameter_cannot_borrow_unquoted_source_or_claim_page(setup):
    client, workspace, doc, _provider, _settings = setup
    for mutation in ({"raw_text": "Another test operates at 23 °C."}, {"source_pages": [2]}):
        result = edit_parameters(
            client,
            doc,
            lambda row, mutation=mutation: row.update(atomic_claims=[source_atom(**mutation)]),
        )
        assert result.status_code == 400
        assert "belonging to this test" in result.json()["detail"]
    assert workspace.facts.latest(doc, workspace.specs.load_latest_extraction(doc).run_id) is None


def test_parameter_preview_is_bounded_to_current_effective_requirement(setup):
    client, _workspace, doc, _provider, _settings = setup
    assert (
        edit_parameters(
            client, doc, lambda row: row.update(atomic_claims=[source_atom()])
        ).status_code
        == 200
    )
    path = f"/api/specifications/{doc}/pages/1"
    data = client.get(f"/api/specifications/{doc}").json()
    claim = next(
        claim
        for claim in data["normalized_requirements"]["req_pencil"]["claims"]
        if claim["source_atom_ids"] == ["human_atom_temperature"]
    )
    result = client.get(path + f"?requirement_id=req_pencil&claim_id={claim['id']}")
    assert result.status_code == 200
    assert result.headers["x-highlight-scope"] == "parameter"
    assert "x-highlight-focus" in result.headers
    missing = client.get(path + "?requirement_id=req_pencil&claim_id=other_test_atom")
    assert missing.status_code == 404
    assert client.get(path + "?claim_id=human_atom_temperature").status_code == 422


def test_rejecting_new_bad_atom_does_not_prevent_unrelated_correction_of_an_old_bad_atom(setup):
    client, workspace, doc, _provider, _settings = setup
    run = workspace.specs.load_latest_extraction(doc).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].atomic_claims = [
        AtomicClaim.model_validate(source_atom(parameter_id="specimen_length"))
    ]
    workspace.specs.save_extraction(doc, run)
    result = edit_parameters(
        client, doc, lambda row: row.update(original_test_name="Pencil hardness (checked)")
    )
    assert result.status_code == 200, result.text
    corrected = edit_parameters(client, doc, lambda row: row.update(atomic_claims=[source_atom()]))
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["facts_version"] >= 1


def test_unchanged_unlocated_model_quote_does_not_block_valid_edit_or_certify_new_facts(setup):
    client, workspace, doc, _provider, _settings = setup
    run = workspace.specs.load_latest_extraction(doc).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].evidence.append(
        RequirementEvidence(page=1, quote="This invented model quotation says 999 degrees C.")
    )
    workspace.specs.save_extraction(doc, run)
    result = edit_parameters(
        client, doc, lambda row: row.update(original_test_name="Pencil hardness (checked)")
    )
    assert result.status_code == 200, result.text
    evidence = result.json()["run"]["extraction"]["requirements"][0]["evidence"]
    assert any("invented" in entry["quote"] for entry in evidence)
    invalid = edit_parameters(
        client, doc, lambda row: row.update(atomic_claims=[source_atom(
            value=999, raw_text="This invented model quotation says 999 degrees C."
        )])
    )
    assert invalid.status_code == 400
    assert "belonging to this test" in invalid.json()["detail"]


def test_changed_or_new_unlocated_quote_is_still_rejected(setup):
    client, _workspace, doc, _provider, _settings = setup
    result = edit_parameters(
        client, doc, lambda row: row["evidence"].append(
            {"page": 1, "quote": "Not physically present in the PDF."}
        )
    )
    assert result.status_code == 400
    assert "Locate each test's quotation" in result.json()["detail"]


def test_owned_context_only_edit_cannot_certify_an_old_model_unit_error(setup):
    client, workspace, doc, _provider, _settings = setup
    run = workspace.specs.load_latest_extraction(doc).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].atomic_claims = [
        AtomicClaim.model_validate(source_atom(unit="panels"))
    ]
    workspace.specs.save_extraction(doc, run)

    def change(row):
        row["atomic_claims"][0]["source_context"] = "operating temperature"

    result = edit_parameters(client, doc, change)
    assert result.status_code == 200, result.text
    atom = next(
        claim for claim in result.json()["normalized_requirements"]["req_pencil"]["claims"]
        if "human_atom_temperature" in claim["source_atom_ids"]
    )
    assert atom["normalization_state"] == "unresolved"
    assert atom["availability"] == "unknown"
    # A number or unit change is not a context-only correction.
    result = edit_parameters(
        client, doc,
        lambda row: row["atomic_claims"][0].update(value="999", source_context="temperature"),
    )
    assert result.status_code == 400

    result = edit_parameters(
        client, doc,
        lambda row: row["atomic_claims"][0].update(stage="curing", source_context="temperature"),
    )
    assert result.status_code == 400


def test_context_only_edit_cannot_insert_unstated_parameter_scope(setup):
    client, workspace, doc, _provider, _settings = setup
    run = workspace.specs.load_latest_extraction(doc).model_copy(deep=True)
    run.run_id = str(uuid4())
    run.extraction.requirements[0].atomic_claims = [AtomicClaim.model_validate(source_atom())]
    workspace.specs.save_extraction(doc, run)
    result = edit_parameters(
        client, doc,
        lambda row: row["atomic_claims"][0].update(source_context="curing of aluminium panels"),
    )
    assert result.status_code == 400
    assert "keep context wording supported" in result.json()["detail"]


def test_library_read_reprojects_old_planning_index_without_rewriting_machine_extraction(setup):
    client, workspace, fixture_doc, provider, _settings = setup
    raw = "Assign three to baseline checks and three to thermal cycling."
    stored = workspace.specs.import_pdf("planning.pdf", make_pdf(raw))
    run = workspace.specs.load_latest_extraction(fixture_doc).model_copy(deep=True)
    run.run_id, run.document_id = str(uuid4()), stored.document_id
    row = run.extraction.requirements[0]
    row.conditions = []
    row.evidence = [RequirementEvidence(page=1, quote=raw)]
    row.atomic_claims = [AtomicClaim(
        id="allocation", parameter_id="panel_count", label="Panel count", operator="=",
        value=3, unit="panels", raw_text=raw,
        source_context="to baseline checks, from each lot", source_pages=[1],
    )]
    workspace.specs.save_extraction(stored.document_id, run)
    before = run.model_dump_json()
    current = normalize_requirement(row, native_first=True)
    legacy = current.model_copy(deep=True, update={
        "normalizer_version": "atomic_requirements_v0_39_native_first",
    })
    legacy.claims[0] = legacy.claims[0].model_copy(update={
        "parameter_id": None, "normalization_state": "unresolved", "availability": "unknown",
    })
    workspace.library.store.ensure_document(stored, run.extraction.document)
    workspace.library.store.save_requirements(run, [legacy])
    original_source = run.extraction.model_dump(mode="json")
    response = client.get(f"/api/library/{stored.document_id}")
    assert response.status_code == 200, response.text
    claims = workspace.library.store.load_requirements(run.run_id)[0]["claims"]
    assert claims[0]["parameter_id"] == "sample_allocation"
    assert claims[0]["normalization_state"] == "parsed"
    with workspace.library.store.engine.connect() as connection:
        saved = connection.execute(
            select(runs.c.source, runs.c.normalizer_version).where(runs.c.run_id == run.run_id)
        ).one()
    assert saved.source == original_source
    assert saved.normalizer_version == f"{NORMALIZER_VERSION}_native_first"
    original = workspace.specs.load_extraction(stored.document_id, run.run_id)
    assert original.model_dump_json() == before
    assert provider.calls == 0


def test_oversized_analysis_has_clear_error_without_calling_provider_or_replacing_source(setup):
    client, workspace, doc, provider, settings = setup
    previous = workspace.specs.load_latest_extraction(doc).run_id
    settings.scione_customer_input_character_limit = 10
    result = client.post(
        f"/api/specifications/{doc}/analysis",
        json={
            "provider": "groq",
            "model": "offline-model",
            "strategy": "inventory_compact_document",
            "output_limit": 2048,
            "permission": True,
        },
    )
    assert result.status_code == 400
    assert "too long for the current analysis limit" in result.json()["detail"]
    assert "No AI request was sent" in result.json()["detail"]
    assert provider.calls == 0
    assert workspace.specs.load_latest_extraction(doc).run_id == previous
    saved = workspace.specs.load_failure(doc)
    assert saved.error_code == "input_too_large"
    assert "input characters including" in saved.message  # private diagnostic is intact
    assert "too long" in client.get(f"/api/specifications/{doc}").json()["failure"]["message"]

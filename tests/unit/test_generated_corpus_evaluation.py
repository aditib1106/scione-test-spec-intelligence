"""An offline scorer must not disguise schema success as factual correctness."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scione.evaluation.generated_corpus import (
    evaluate_corpus,
    load_reference,
    method_key,
    payload_matches,
    score_document,
)
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def atom(**updates):
    return AtomicClaim.model_validate(
        {
            "id": "claim_1",
            "parameter_id": "temperature",
            "label": "Temperature",
            "kind": "condition",
            "stage": "cold",
            "operator": "=",
            "value": "60",
            "unit": "°C",
            "raw_text": "at 60 °C",
            "source_pages": [1],
            **updates,
        }
    )


def requirement(identifier="req_first", method="ISO 60001:2024", claims=None):
    return CustomerRequirement.model_validate(
        {
            "id": identifier,
            "original_test_name": "Example test",
            "requested_methods": [{"code": method}],
            "atomic_claims": [claim.model_dump(mode="json") for claim in claims or []],
            "evidence": [{"page": 1, "quote": "at 60 °C"}],
        }
    )


def run(requirements=None):
    return CustomerExtractionRun.model_validate(
        {
            "run_id": "run_test",
            "started_at": datetime(2026, 11, 9, tzinfo=UTC),
            "document_id": "model_generated_id_is_not_source_identity",
            "prompt_version": "test_prompt",
            "schema_version": "0.1",
            "provider": "manual_test_fixture",
            "model": "no_model",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "{}",
            "extraction": {
                "schema_version": "0.1",
                "document": {"identifier": "wrong-id"},
                "requirements": [row.model_dump(mode="json") for row in requirements or []],
            },
        }
    )


def reference():
    return {
        "document_id": "DOC-1",
        "file_name": "DOC-1.pdf",
        "sha256": "a" * 64,
        "split": "development",
        "expected_test_rows": [
            {
                "reference_id": "T1",
                "bank_test_id": "BANK-1",
                "name": "Example",
                "methods": [{"code": "ISO 60001:2024"}],
            }
        ],
        "critical_facts": [
            {
                "fact_id": "F1",
                "tests": ["T1"],
                "parameter": "temperature",
                "role": "condition",
                "stage": "cold",
                "expected": {"operator": "=", "value": "60", "unit": "°C"},
                "evidence": {"page": 1, "quote": "at 60 °C"},
            }
        ],
        "not_separate_tests": [],
        "ambiguities": [],
    }


def save_reference(tmp_path, documents=None):
    path = tmp_path / "reference.json"
    path.write_text(
        json.dumps(
            {
                "dataset_id": "unit_test_manual_source",
                "documents": documents or [reference()],
                "limitations": ["Partial manual source reference, not client gold."],
            }
        )
    )
    return path


def save_run(tmp_path, value=None, *, name="DOC-1", source_hash="a" * 64):
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    value = value or run([requirement(claims=[atom()])])
    (directory / "extraction_run.json").write_text(value.model_dump_json())
    (directory / "document.json").write_text(
        json.dumps(
            {
                "sha256": source_hash,
                "file_name": "DOC-1.pdf",
                "pages": [{"page_number": 1, "text": "The request uses ISO 60001:2024 at 60 °C."}],
            }
        )
    )
    return directory


def test_method_key_keeps_edition_part_and_family():
    assert method_key(" DIN EN ISO 60001:2024-08 ") == "DINENISO60001:2024-08"
    assert method_key("ISO 60001:2024") != method_key("ISO 60001:2023")
    assert method_key("ISO 60001-2:2024") != method_key("ISO 60001:2024")


def test_separate_revision_is_not_an_extra_phantom_method():
    row = requirement()
    row.requested_methods[0].code = "ISO 60001"
    row.requested_methods[0].revision = "2024"
    score = score_document(reference(), run([row]))
    assert score["method_reference_coverage"]["recovered_codes"] == ["ISO60001:2024"]
    assert score["method_reference_coverage"]["unreferenced_code_candidates"] == []


def test_decimal_operators_unit_prefixes_and_affine_temperature_are_exact():
    expected = {"operator": "=", "value": "60", "unit": "°C"}
    assert payload_matches(expected, atom(value="140", unit="°F"))
    assert not payload_matches(expected, atom(value="60", operator=">="))
    assert not payload_matches(
        {"operator": "=", "value": "1", "unit": "MPa"}, atom(value="1", unit="mPa")
    )
    assert not payload_matches(
        {"operator": "=", "value": "100.000000000000001", "unit": "°C"},
        atom(value="100.000000000000002"),
    )
    assert payload_matches(
        {"operator": "=", "value": "60", "tolerance": "2", "unit": "°C"},
        atom(value="140", tolerance="3.6", unit="°F"),
    )
    assert not payload_matches(
        {"operator": "=", "value": "1020", "unit": "MPa"},
        atom(value="1.020", unit="MPa"),
    )


def test_schema_success_payload_source_and_stage_are_separate():
    row = requirement(claims=[atom(stage="hot")])
    score = score_document(
        reference(),
        run([row]),
        source={
            "pages": [{"page_number": 1, "text": "At 60 °C the specimen is held."}],
        },
    )
    outcome = score["selected_critical_facts"]["extraction_atomic_claims"][0]
    assert score["status"] == "valid_saved_extraction_contract"
    assert not score["factual_correctness_established_by_contract"]
    assert outcome["payload_recovered"]
    assert outcome["grounded_payload_recovered"]
    assert outcome["stage_association"] == "explicit_conflicting_stage_words"
    assert score["selected_critical_facts"]["normalized_system_claims"] is None


def test_unfamiliar_stage_is_pending_not_optimistically_called_equivalent():
    row = requirement(claims=[atom(stage="supplier-phase-alpha")])
    score = score_document(reference(), run([row]))
    outcome = score["selected_critical_facts"]["extraction_atomic_claims"][0]
    assert outcome["payload_recovered"]
    assert outcome["stage_association"] == "manual_semantic_review"
    assert not outcome["grounded_payload_recovered"]  # No source artifact supplied.


def test_same_number_on_wrong_test_does_not_recover_the_reference_fact():
    ref = reference()
    ref["expected_test_rows"].append(
        {
            "reference_id": "T2",
            "bank_test_id": "BANK-2",
            "name": "Different test",
            "methods": [{"code": "ISO 60002:2024"}],
        }
    )
    actual = run(
        [
            requirement(),
            requirement("req_second", "ISO 60002:2024", [atom()]),
        ]
    )
    outcome = score_document(ref, actual)["selected_critical_facts"]["extraction_atomic_claims"][0]
    assert outcome["status"] == "payload_under_other_test"
    assert not outcome["payload_recovered"]


def test_unknown_parameter_identity_is_an_explicit_review_item():
    row = requirement(claims=[atom(parameter_id="unfamiliar-property", label="Unfamiliar")])
    outcome = score_document(reference(), run([row]))["selected_critical_facts"][
        "extraction_atomic_claims"
    ][0]
    assert outcome["status"] == "manual_review_parameter_identity"
    assert not outcome["payload_recovered"]


def test_customer_defined_tests_are_not_forced_into_a_bank_name_match():
    ref = reference()
    ref["expected_test_rows"][0]["methods"] = []
    ref["expected_test_rows"][0]["bank_test_id"] = None
    row = requirement(method="customer-method-unknown")
    score = score_document(ref, run([row]))
    coverage = score["test_coverage"]
    assert coverage["method_anchored_covered_rows"] == 0
    assert len(coverage["customer_defined_reference_rows_for_manual_review"]) == 1
    assert coverage["unassigned_requirement_candidates"][0]["id"] == row.id
    assert (
        score["selected_critical_facts"]["extraction_atomic_claims"][0]["status"]
        == "manual_review_custom_test_identity"
    )


def test_exact_duplicates_and_possible_merges_are_visible_diagnostics():
    ref = reference()
    ref["expected_test_rows"].append(
        {
            "reference_id": "T2",
            "bank_test_id": "BANK-2",
            "name": "Second",
            "methods": [{"code": "ISO 60002:2024"}],
        }
    )
    first = requirement(claims=[atom()])
    duplicate = first.model_copy(deep=True, update={"id": "req_duplicate"})
    duplicate.atomic_claims[0].id = "another-id"
    merged = requirement("req_merged", "ISO 60001:2024")
    merged.requested_methods.append(type(merged.requested_methods[0])(code="ISO 60002:2024"))
    score = score_document(ref, run([first, duplicate, merged]))
    coverage = score["test_coverage"]
    assert coverage["exact_duplicate_requirement_groups"] == [["req_first", "req_duplicate"]]
    assert (
        coverage["possible_cross_test_merges_for_manual_review"][0]["requirement_id"]
        == "req_merged"
    )
    assert len(coverage["maximum_one_to_one_assignment"]) == 2


def test_missing_and_invalid_runs_remain_in_end_to_end_denominators(tmp_path):
    second = reference()
    second.update({"document_id": "DOC-2", "file_name": "DOC-2.pdf", "sha256": "b" * 64})
    path = save_reference(tmp_path, [reference(), second])
    saved = tmp_path / "runs"
    save_run(saved)
    result = evaluate_corpus(path, saved)
    assert result["summary"]["expected_documents"] == 2
    assert result["summary"]["failed_or_missing_documents"] == 1
    assert result["summary"]["exact_method_reference_recall_including_failed_documents"] == 0.5
    facts = result["summary"]["selected_critical_facts"]["extraction_atomic_claims"]
    assert facts["selected_reference_facts_including_failed_documents"] == 2
    assert facts["typed_payload_recall_including_failed_documents"] == 0.5
    assert facts["precision"] is None
    assert facts["whole_document_accuracy"] is None


def test_source_identity_uses_pdf_hash_not_model_identifier_or_folder(tmp_path):
    path = save_reference(tmp_path)
    saved = tmp_path / "runs"
    directory = save_run(saved, name="opaque-source-id")
    result = evaluate_corpus(path, saved)
    assert result["summary"]["valid_saved_contract_documents"] == 1
    assert result["documents"][0]["source_identity_status"] == "source_pdf_hash_matched"
    source = json.loads((directory / "document.json").read_text())
    source["sha256"] = "0" * 64
    (directory / "document.json").write_text(json.dumps(source))
    result = evaluate_corpus(path, saved)
    assert result["summary"]["valid_saved_contract_documents"] == 0
    assert len(result["unassigned_artifacts"]) == 1


def test_normalized_facts_are_scored_separately_and_not_generated_by_evaluator(tmp_path):
    path = save_reference(tmp_path)
    saved = tmp_path / "runs"
    directory = save_run(saved, run([requirement()]))
    normalized = NormalizedRequirement(
        requirement_id="req_first", test_name="Example", claims=[atom()]
    )
    (directory / "normalized.json").write_text(json.dumps([normalized.model_dump(mode="json")]))
    result = evaluate_corpus(path, saved)
    layers = result["summary"]["selected_critical_facts"]
    assert layers["extraction_atomic_claims"]["typed_payload_recovered"] == 0
    assert layers["normalized_system_claims"]["typed_payload_recovered"] == 1
    assert "normalized.json" in result["documents"][0]["artifact_sha256"]


def test_multiple_saved_attempts_are_not_cherry_picked(tmp_path):
    path = save_reference(tmp_path)
    saved = tmp_path / "runs"
    save_run(saved / "attempt_1")
    save_run(saved / "attempt_2", run([requirement()]))
    result = evaluate_corpus(path, saved)
    assert result["documents"][0]["status"] == "multiple_attempts_require_explicit_selection"
    assert result["summary"]["recovered_method_references"] == 0
    assert result["summary"]["failed_or_missing_documents"] == 1


def test_errata_requires_explicit_matching_hash_and_preserves_frozen_reference(tmp_path):
    path = save_reference(tmp_path)
    original = path.read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    errata = tmp_path / "errata.json"
    errata.write_text(
        json.dumps(
            {
                "parent_reference_sha256": sha,
                "changes": [
                    {
                        "document_id": "DOC-1",
                        "reference_id": "T1",
                        "field": "bank_test_id",
                        "corrected": None,
                    }
                ],
            }
        )
    )
    digest = hashlib.sha256(errata.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="supplied together"):
        load_reference(path, errata=errata)
    with pytest.raises(ValueError, match="does not match"):
        load_reference(path, expected_sha256="0" * 64)
    changed, provenance = load_reference(path, sha, errata, digest)
    assert changed["documents"][0]["expected_test_rows"][0]["bank_test_id"] is None
    assert provenance["errata"]["sha256"] == digest
    assert path.read_bytes() == original


def cli():
    script = Path(__file__).parents[2] / "scripts/evaluate_generated_corpus.py"
    spec = importlib.util.spec_from_file_location("offline_generated_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_never_loads_settings_or_opens_network_and_hashes_its_inputs(tmp_path, monkeypatch):
    import scione.config

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline evaluation cannot use settings or network.")

    monkeypatch.setattr(scione.config, "Settings", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    path = save_reference(tmp_path)
    saved = tmp_path / "runs"
    save_run(saved)
    output = tmp_path / "score.json"
    assert (
        cli().main(["--reference", str(path), "--runs", str(saved), "--output", str(output)]) == 0
    )
    result = json.loads(output.read_text())
    assert result["new_api_calls"] == 0
    assert result["provenance"]["reference_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["provenance"]["scorer_source_sha256"]
    assert result["provenance"]["cli_source_sha256"]
    before = (saved / "DOC-1/extraction_run.json").read_bytes()
    with pytest.raises(SystemExit):
        cli().main(
            [
                "--reference",
                str(path),
                "--runs",
                str(saved),
                "--output",
                str(saved / "DOC-1/extraction_run.json"),
            ]
        )
    assert (saved / "DOC-1/extraction_run.json").read_bytes() == before

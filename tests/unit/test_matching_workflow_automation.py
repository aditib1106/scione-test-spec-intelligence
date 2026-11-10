"""Selected manual PDF facts and an independent lab, not extraction accuracy."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scione.catalogs.models import LabProfile
from scione.evaluation.generated_matching import _policy_run, method_statuses
from scione.matching import compile_matching_run
from scione.matching.models import MatchingInput, SemanticMatchingOutput
from scione.source_text import citation_contains

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/generated_matching_workflow_v2.json"
FROZEN_SHA256 = "72425675e5915f9441337485151a375a45670b6ee09890502874a85240d0f328"


def reference():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FROZEN_SHA256
    return json.loads(raw)


def compile_reference(fixture):
    snapshot = MatchingInput.model_validate(fixture["snapshot"])
    output = SemanticMatchingOutput.model_validate(fixture["expected_output"])
    attempt = _policy_run(snapshot, output, "manual_selected_workflow_policy_probe")
    return compile_matching_run(attempt, method_statuses(snapshot, attempt.output))


def test_richer_manual_lab_proves_automation_without_turning_missing_into_support():
    fixture = reference()
    assert fixture["annotation_status"] == "manual_selected_source_matching_only"
    comparisons = compile_reference(fixture)
    assert {row.requirement_id: row.status for row in comparisons} == fixture["expected_overall"]
    checks = {check.id: check for row in comparisons for check in row.checks}
    assert len(fixture["expected_checks"]) == 35
    for identifier, expected in fixture["expected_checks"].items():
        assert checks[identifier].status == expected
    assert sum(check.status == "supported" for check in checks.values()) == 37
    assert sum(check.status == "gap" for check in checks.values()) == 1
    assert sum(check.status == "unknown" for check in checks.values()) == 1
    assert checks["req_workflow_4_topcoat"].status == "unknown"
    assert checks["req_workflow_2_retention"].status == "gap"


def test_deleting_a_declared_field_never_keeps_an_old_automatic_support():
    fixture = deepcopy(reference())
    snapshot = fixture["snapshot"]
    row = snapshot["capabilities"][0]
    removed = row["claims"].pop(0)
    row["capability"]["atomic_claims"] = [
        claim for claim in row["capability"]["atomic_claims"] if claim["id"] != removed["id"]
    ]
    target = fixture["expected_output"]["items"][0]["claim_alignments"][0]
    target.update(relation="unknown", capability_claim_ids=[])
    result = compile_reference(fixture)[0]
    assert result.status == "needs_information"
    assert (
        next(check for check in result.checks if check.id == target["requirement_claim_id"]).status
        == "unknown"
    )


def test_smaller_operating_range_creates_a_real_gap_instead_of_a_green_method_match():
    fixture = deepcopy(reference())
    row = fixture["snapshot"]["capabilities"][0]
    for claim in [*row["claims"], *row["capability"]["atomic_claims"]]:
        if claim["id"] == "lab_req_workflow_1_shared_0":
            claim["maximum"] = "120"
    result = compile_reference(fixture)[0]
    assert result.status == "partially_matched"
    assert (
        next(check for check in result.checks if check.id == "req_workflow_1_shared_0").status
        == "gap"
    )


def test_runtime_lab_example_is_explicit_optional_synthetic_input():
    from importlib.resources import files

    item = json.loads(
        files("scione.catalogs").joinpath("data/csp301_typed_lab_example.json").read_text()
    )
    assert item["synthetic"] is True
    assert "independent of model outputs" in item["description"]
    assert LabProfile.model_validate(item["lab"]).model_dump(mode="json") == reference()["lab"]


def test_detailed_lab_example_keeps_explicit_gap_missing_field_and_provenance():
    from importlib.resources import files

    item = json.loads(
        files("scione.catalogs").joinpath("data/csp301_detailed_lab_example.json").read_text()
    )
    assert item["synthetic"] is True
    assert "after earlier diagnostics" in item["authoring_status"]
    assert "not independent validation gold" in item["authoring_status"]
    lab = LabProfile.model_validate(item["lab"])
    assert len(lab.capabilities) == 4
    assert sum(len(cap.atomic_claims) for cap in lab.capabilities) == 76
    assert not any(
        claim.parameter_id == "topcoat_thickness"
        for cap in lab.capabilities
        for claim in cap.atomic_claims
    )
    gloss = next(cap for cap in lab.capabilities if cap.canonical_test_id == "gloss")
    negative = next(
        claim for claim in gloss.atomic_claims if claim.parameter_id == "gloss_retention"
    )
    assert negative.availability == "unsupported"
    assert negative.source_context is None
    assert negative.stage == "after_cycle"


def test_selected_source_quotes_are_literal_in_the_synthetic_pdf():
    pymupdf = pytest.importorskip("pymupdf")
    fixture = reference()
    source = Path(__file__).resolve().parents[2] / fixture["source_pdf"]
    if not source.exists():
        pytest.skip("Optional source PDF is local; the frozen quotations remain available.")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture["source_pdf_sha256"]
    with pymupdf.open(source) as pdf:
        pages = [page.get_text(sort=True) for page in pdf]
    for item in fixture["snapshot"]["requirements"]:
        for claim in item["normalized"]["claims"]:
            assert any(
                citation_contains(pages[page - 1], claim["raw_text"])
                for page in claim["source_pages"]
            ), claim["id"]

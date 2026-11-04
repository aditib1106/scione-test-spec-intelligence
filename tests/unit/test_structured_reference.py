"""The offline reference is source checked, clearly manual, and not expected scores."""

import json
from pathlib import Path

import pytest

from scione.catalogs.seed import default_csp301_lab, load_reference_catalog
from scione.reports.matching import build_report
from scione.reports.store import FileReportStore
from scione.specifications.extraction import validate_customer_sources
from scione.specifications.review import assess_requirements
from scione.specifications.store import FileSpecStore
from scione.structured import compare_requirement, normalize_capability, normalize_requirement
from scione.structured.examples import csp301_reference_run

SAMPLE = Path(__file__).resolve().parents[2] / "docs/samples/CSP-EN-301_demo_spec.pdf"


def test_manual_example_is_explicit_and_all_evidence_locates_on_its_source(tmp_path):
    stored = FileSpecStore(tmp_path).import_pdf(SAMPLE.name, SAMPLE.read_bytes())
    run = csp301_reference_run(stored)
    assert run.provider == "manual_reference"
    assert run.model == "human-prepared-example"
    assert run.raw_response_kind == "manual_reference"
    assert run.calls == []
    assert run.usage.total_tokens is None
    assert json.loads(run.raw_response)["kind"] == "manual_reference"
    assert len(run.extraction.requirements) == 4
    assert validate_customer_sources(run.extraction, stored.document) == []
    assessments = assess_requirements(run, stored.document)
    assert assessments["req_001"].state == "ready"
    assert assessments["req_002"].state == "ready"
    assert assessments["req_003"].state == "needs_attention"
    assert assessments["req_004"].state == "ready"
    assert run.extraction.document.identifier == "CSP-EN-301"
    assert run.extraction.document.revision == "Issue A"
    FileSpecStore(tmp_path).save_extraction(stored.document_id, run)
    assert FileSpecStore(tmp_path).load_latest_extraction(stored.document_id).run_id == run.run_id
    # Existing report storage accepts only canonical UUID run IDs.
    workspace = FileReportStore(tmp_path).load_workspace(stored.document_id, run.run_id)
    assert workspace.run_id == run.run_id
    legacy_report = build_report(
        stored,
        run,
        FileSpecStore(tmp_path).load_review(stored.document_id, run.run_id),
        default_csp301_lab(),
        load_reference_catalog(),
        workspace,
        pdf_bytes=SAMPLE.read_bytes(),
    )
    adhesion = legacy_report.lines[0]
    assert adhesion.considered_method_codes == ["ISO 60038:2021"]
    assert adhesion.capability.id == "cap_csp301_cross_cut"
    assert len(adhesion.requirement.requested_methods) == 2


def test_manual_reference_rejects_a_changed_document(tmp_path):
    stored = FileSpecStore(tmp_path).import_pdf(SAMPLE.name, SAMPLE.read_bytes())
    stored.document.sha256 = "0" * 64
    with pytest.raises(ValueError, match="only for the bundled"):
        csp301_reference_run(stored)


def test_worked_example_finds_real_output_gap_and_preserves_missing_declarations(tmp_path):
    stored = FileSpecStore(tmp_path).import_pdf(SAMPLE.name, SAMPLE.read_bytes())
    run = csp301_reference_run(stored)
    results = {
        req.original_test_name: compare_requirement(
            normalize_requirement(req), normalize_capability(cap)
        )
        for req, cap in zip(
            run.extraction.requirements, default_csp301_lab().capabilities, strict=True
        )
    }
    gloss = results["Specular gloss"]
    assert any(c.parameter_id == "gloss_retention" and c.status == "gap" for c in gloss.checks)
    assert any(c.parameter_id == "gloss" and c.status == "supported" for c in gloss.checks)
    assert gloss.status == "partially_matched"
    color = results["Colour difference"]
    assert any(c.parameter_id == "illuminant" and c.status == "unknown" for c in color.checks)
    assert any(c.parameter_id == "reference_panel" and c.status == "unknown" for c in color.checks)
    assert any(
        c.parameter_id == "color_difference" and c.status == "supported" for c in color.checks
    )
    thickness = results["Dry film thickness"]
    for parameter in ["temperature", "relative_humidity", "duration"]:
        assert any(
            c.parameter_id == parameter and c.status == "supported" for c in thickness.checks
        )
    assert any(
        c.parameter_id == "topcoat_thickness" and c.status == "unknown" for c in thickness.checks
    )
    assert all(
        check.status == "informational"
        for result in results.values()
        for check in result.checks
        if check.requirement_claim and "written approval" in check.requirement_claim.raw_text
    )

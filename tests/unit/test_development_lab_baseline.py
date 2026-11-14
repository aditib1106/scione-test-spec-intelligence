"""Team-derived setup declarations stay bounded and never auto-replace user data."""

import hashlib
import json
import runpy
from importlib.resources import files
from pathlib import Path

from scione.catalogs.bank import load_synthetic_method_bank
from scione.catalogs.baseline import development_lab_baseline
from scione.catalogs.generated import canonical_test_id_for_family
from scione.catalogs.models import LabCapability, validate_lab_references
from scione.catalogs.seed import load_reference_catalog
from scione.catalogs.sql_lab_store import SQLLabStore
from scione.library.store import LibraryStore


def test_every_bank_family_has_a_real_source_method_and_all_lab_methods_are_available():
    catalog = load_reference_catalog()
    bank = load_synthetic_method_bank()
    for family_id in bank.family_names:
        test = catalog.test_by_id(canonical_test_id_for_family(family_id, catalog))
        assert test.method_ids
    baseline = development_lab_baseline()
    validate_lab_references(baseline, catalog)
    assert len(baseline.capabilities) == 70
    assert all(cap.price is None for cap in baseline.capabilities)
    assert all(
        claim.normalization_state == "reviewed"
        for cap in baseline.capabilities for claim in cap.atomic_claims
    )


def test_baseline_provenance_preserves_source_rows_and_separates_extensions():
    payload = json.loads(
        files("scione.catalogs").joinpath("data/development_lab_baseline_v1.json").read_text()
    )
    assert payload["synthetic"] is True
    assert len(payload["source_rows"]) == 57
    assert len(payload["selected_declarations"]) == 70
    assert payload["original_source_method_count"] == 49
    assert len(payload["source_csv_sha256"]) == hashlib.sha256().digest_size * 2
    assert payload["baseline_id"] == "team_lab_development_v2"
    assert all(
        row["origin"] == "independent_frozen_laboratory_service_policy_v2"
        for row in payload["selected_declarations"]
    )
    assert any(
        claim.parameter_id == "gloss_retention" and claim.availability == "unsupported"
        for cap in development_lab_baseline().capabilities for claim in cap.atomic_claims
    )


def test_setup_loader_is_detached_and_sql_preserves_prior_lab_revision(tmp_path):
    catalog = load_reference_catalog()
    store = LibraryStore(tmp_path)
    labs = SQLLabStore(store, catalog)
    previous = labs.load_or_create()
    baseline = development_lab_baseline()
    assert not labs.load_or_create().capabilities
    labs.save(previous.model_copy(update={"capabilities": baseline.capabilities}))
    saved = labs.load_or_create()
    assert saved.name == previous.name
    assert saved.version == previous.version + 1
    assert len(saved.capabilities) == 70
    baseline.capabilities.clear()
    assert len(development_lab_baseline().capabilities) == 70
    assert len(labs.load_or_create().capabilities) == 70
    store.close()


def test_explicit_lab_ranges_keep_units_and_do_not_invent_compound_or_missing_fields():
    helper = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build_development_lab.py")
    )["explicit_reporting_ranges"]
    text = "Tensile strength: 1–2000 MPa; elongation: 0–100%."
    cap = LabCapability(
        id="source_cap", canonical_test_id="custom_test",
        declared_constraints="Not specified.", notes=f"Reportable outputs: {text}",
    )
    claims = helper(cap, text)
    tensile = next(c for c in claims if c.parameter_id == "tensile_strength")
    assert str(tensile.minimum) == "1" and str(tensile.maximum) == "2000"
    assert tensile.unit == "MPa" and tensile.kind == "reporting"
    assert next(c for c in claims if c.parameter_id == "elongation").unit == "%"
    assert any(c.availability == "unknown" for c in claims if c.kind == "condition")
    compound = "Pb and Cd: 0.1–100 mg/kg each; Grade: 1–5, including half grades."
    cap.notes = f"Reportable outputs: {compound}"
    assert all(
        c.normalization_state == "unresolved"
        for c in helper(cap, compound) if c.kind == "reporting"
    )

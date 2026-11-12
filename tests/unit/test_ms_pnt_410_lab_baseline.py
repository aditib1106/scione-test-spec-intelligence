"""A fixed fictional coatings lab supplies bounded, independent comparison cases."""

import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

import pymupdf

from scione.catalogs.baseline import development_lab_baseline, ms_pnt_410_lab_baseline
from scione.catalogs.models import validate_lab_references
from scione.catalogs.seed import load_reference_catalog


def _payload():
    return json.loads(
        files("scione.catalogs").joinpath("data/ms_pnt_410_lab_baseline_v1.json").read_text()
    )


def _cap(method_id):
    return next(
        cap for cap in ms_pnt_410_lab_baseline().capabilities if method_id in cap.method_ids
    )


def _claim(method_id, parameter_id, stage="measurement"):
    return next(
        claim for claim in _cap(method_id).atomic_claims
        if claim.parameter_id == parameter_id and claim.stage == stage
    )


def test_separate_synthetic_profile_has_twelve_valid_catalog_services_and_fresh_values():
    original_default = development_lab_baseline().model_dump(mode="json")
    lab = ms_pnt_410_lab_baseline()
    assert len(lab.capabilities) == 12
    validate_lab_references(lab, load_reference_catalog())
    assert "synthetic" in lab.name.lower()
    assert all(cap.price is not None and cap.currency == "USD" for cap in lab.capabilities)
    assert all(
        claim.normalization_state == "reviewed" and not claim.source_pages
        for cap in lab.capabilities for claim in cap.atomic_claims
    )
    lab.capabilities.clear()
    assert len(ms_pnt_410_lab_baseline().capabilities) == 12
    assert development_lab_baseline().model_dump(mode="json") == original_default


def test_finite_gloss_and_hardness_declarations_cover_measurement_without_promising_pass():
    gloss = _claim("iso_2813", "gloss")
    assert gloss.kind == "reporting" and gloss.unit == "GU"
    assert gloss.minimum == 0 and gloss.maximum == 200
    assert gloss.minimum <= Decimal("85") - 3 <= Decimal("85") + 3 <= gloss.maximum
    angle = _claim("iso_2813", "angle")
    assert angle.values == [Decimal("20"), Decimal("60"), Decimal("85")]
    assert _claim("iso_15184", "load").value == 750
    hardness = _claim("iso_15184", "pencil_hardness")
    assert hardness.kind == "reporting" and " F," in hardness.text_value
    assert "does not guarantee" in _cap("iso_15184").notes


def test_intended_partial_gaps_and_unknowns_are_not_backfilled_from_specification():
    water_temperature = _claim("iso_16925", "water_temperature")
    assert water_temperature.maximum == 50 < 60
    assert _claim("iso_16925", "distance").maximum == 150
    assert _claim("iso_16925", "duration").maximum == 120
    assert _claim("iso_6270_2", "duration", "exposure").maximum == 168 < 240
    assert _claim("iso_4624", "pull_off_strength").maximum == 3 < 5
    assert _claim("iso_2812_2", "duration", "exposure").maximum >= 240
    assert _claim("iso_9227", "corrosion_creep", "after_cycle").availability == "unknown"
    assert _claim("sae_j2527", "color_difference", "after_cycle").availability == "unknown"
    assert _claim("sae_j2527", "radiant_exposure", "exposure").maximum >= 3000


def test_unoffered_exact_methods_remain_absent_despite_related_offered_services():
    catalog = load_reference_catalog()
    offered = {
        catalog.method_by_id(method_id).identifier
        for cap in ms_pnt_410_lab_baseline().capabilities for method_id in cap.method_ids
    }
    assert "ISO 9227" in offered and "SAE J2527" in offered and "ISO 16474-3" in offered
    assert offered.isdisjoint(_payload()["intentionally_not_offered"])
    assert "VDA 233-102" not in offered and "SAE J1976" not in offered


def test_expectations_keep_source_provenance_and_separate_independent_numeric_control():
    data = _payload()
    assert data["synthetic"] is True
    assert "not human/client-validated gold" in data["authoring_status"]
    assert "fictional" in data["pricing_note"]
    assert {item["expected"] for item in data["expectations"]} == {
        "supported", "partially_matched", "needs_information", "not_offered",
    }
    assert all(item["scope"] and item["source_page"] >= 1 for item in data["expectations"])
    control = next(item for item in data["expectations"] if item["id"] == "gloss_tolerance_control")
    assert "invented" in control["scope"] and "not quoted" in control["scope"]
    root = Path(__file__).resolve().parents[2]
    source = root / data["source_pdf"]
    # The optional client document is not required to install this fixture.
    if source.exists():
        assert hashlib.sha256(source.read_bytes()).hexdigest() == data["source_pdf_sha256"]
        with pymupdf.open(source) as pdf:
            for item in data["expectations"]:
                text = unicodedata.normalize("NFKC", pdf[item["source_page"] - 1].get_text())
                quote = unicodedata.normalize("NFKC", item["source_quote"])
                assert re.sub(r"\s+", " ", quote) in re.sub(r"\s+", " ", text)

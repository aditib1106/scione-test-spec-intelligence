"""Physical source derivations compose safely and never reuse another input's facts."""

from decimal import Decimal
from pathlib import Path

import pytest

from scione.ingestion.pdf_text import PyMuPDFTextIngestor
from scione.schemas import TextReadingOrder
from scione.specifications.models import CustomerRequirement, RequirementEvidence, RequirementValue
from scione.specifications.parameter_views import (
    _cached_source_views,
    physical_normalized_requirements,
    physical_parameter_views,
)
from scione.structured import normalize_requirement
from scione.structured.models import AtomicClaim

METHOD_CELL = "ISO 16925, X-scribe, 60 °C water, 10 cm, 60 s"
CONDITIONING = "Conditioning 7 days at 23 °C, 50 % RH (ISO 3270)"
APPEARANCE = "Measured on 300 x 500 mm panels sprayed horizontal and vertical."


@pytest.fixture
def source():
    path = Path(__file__).resolve().parents[2] / "docs/samples/MS-PNT-410_demo_spec.pdf"
    if not path.exists():
        pytest.skip("The client source PDF is unavailable in this checkout.")
    document = PyMuPDFTextIngestor(reading_order=TextReadingOrder.GEOMETRIC).ingest(path)
    assert document.page_count == 13
    return document, path.read_bytes()


@pytest.fixture(autouse=True)
def empty_source_cache():
    _cached_source_views.cache_clear()
    yield
    _cached_source_views.cache_clear()


def requirement():
    return CustomerRequirement(
        id="req_source", original_test_name="Pressure water jet adhesion",
        requested_methods=[{"code": "ISO 16925"}],
        conditions=[
            RequirementValue(name="Water jet", raw_value=METHOD_CELL),
            RequirementValue(name="Conditioning", raw_value=CONDITIONING),
            RequirementValue(name="Inherited panel shape", raw_value=APPEARANCE),
        ],
        evidence=[
            RequirementEvidence(
                page=6,
                quote="Pressure water jet ISO 16925, X-scribe, 60 °C "
                      "Characteristic value ≤ 1 adhesion water, 10 cm, 60 s",
            ),
            RequirementEvidence(page=3, quote=CONDITIONING),
            RequirementEvidence(page=5, quote=APPEARANCE),
        ],
        atomic_claims=[
            AtomicClaim(
                id="req_source_claim_1", parameter_id="water_temperature",
                label="Water temperature",
                kind="condition", stage="measurement", operator="=", value="60", unit="°C",
                raw_text=METHOD_CELL, source_pages=[6],
            ),
            AtomicClaim(
                id="req_source_claim_2", parameter_id="panel_shape", label="Panel shape",
                kind="condition", stage="measurement", operator="text", text_value=APPEARANCE,
                raw_text=APPEARANCE, source_pages=[5], normalization_state="parsed",
            ),
        ],
    )


def test_physical_cell_restores_water_temperature_with_source_provenance_and_no_ai(source):
    document, pdf = source
    original = requirement()
    before = original.model_dump_json()
    without_pdf = normalize_requirement(original, native_first=True)
    raw_water = next(c for c in without_pdf.claims if c.id == "req_source_claim_1")
    assert raw_water.normalization_state == "unresolved"
    normalized = physical_normalized_requirements([original], document, pdf_bytes=pdf)[0]
    water = next(c for c in normalized.claims if c.id == "req_source_claim_1")
    assert water.normalization_state == "parsed" and water.parameter_id == "water_temperature"
    assert water.value == 60 and water.unit == "°C" and water.raw_text == METHOD_CELL
    assert water.source_pages == [6]
    assert any(
        action.get("rule") == "physical_table_cell_citation"
        and action["claim_ids"] == [water.id] and action["source_page"] == 6
        and action["establishes_lab_support"] is False
        for action in normalized.normalization_actions
    )
    assert original.model_dump_json() == before


def test_conditioning_completion_and_foreign_section_scope_share_immutable_source_view(source):
    document, pdf = source
    original = requirement()
    before, source_before = original.model_dump_json(), document.model_dump_json()
    views, proofs = physical_parameter_views([original], document, pdf_bytes=pdf)
    view = views[original.id]
    conditioning = {
        atom.parameter_id: atom for atom in view.atomic_claims if atom.stage == "conditioning"
    }
    assert set(conditioning) == {"duration", "temperature", "relative_humidity"}
    assert conditioning["duration"].value == 7 and conditioning["duration"].unit == "days"
    assert conditioning["temperature"].value == 23 and conditioning["temperature"].unit == "°C"
    assert conditioning["relative_humidity"].value == 50
    assert conditioning["relative_humidity"].unit == "% RH"
    assert all(atom.source_pages == [3] for atom in conditioning.values())
    assert "req_source_claim_2" not in {atom.id for atom in view.atomic_claims}
    assert APPEARANCE not in {item.raw_value for item in view.conditions}
    assert CONDITIONING in {item.raw_value for item in view.conditions}
    actions = proofs[original.id]["__source_derivations__"]["actions"]
    assert any(action.get("action") == "added_source_conditioning_atoms" for action in actions)
    assert any(action.get("disposition") == "excluded_model_inheritance" for action in actions)
    assert original.model_dump_json() == before and document.model_dump_json() == source_before


def test_cache_hits_return_independent_mutable_views_and_proof_actions(source):
    document, pdf = source
    original = requirement()
    first, first_proofs = physical_parameter_views([original], document, pdf_bytes=pdf)
    cached = _cached_source_views.cache_info()
    expected = first[original.id].model_dump_json()
    first[original.id].atomic_claims[0].value = Decimal("999")
    first[original.id].evidence.clear()
    first_proofs[original.id]["__source_derivations__"]["actions"].clear()
    second, second_proofs = physical_parameter_views([original], document, pdf_bytes=pdf)
    assert _cached_source_views.cache_info().hits == cached.hits + 1
    assert second[original.id].model_dump_json() == expected
    assert second_proofs[original.id]["__source_derivations__"]["actions"]
    assert original.atomic_claims[0].value == 60 and original.evidence


def test_condition_edit_with_same_requirement_id_does_not_reuse_prior_derived_atoms(source):
    document, pdf = source
    original = requirement()
    first, _ = physical_parameter_views([original], document, pdf_bytes=pdf)
    assert any(atom.stage == "conditioning" for atom in first[original.id].atomic_claims)
    cached = _cached_source_views.cache_info()
    changed = original.model_copy(deep=True)
    changed.conditions[1].raw_value = CONDITIONING.replace("7 days", "8 days")
    second, _ = physical_parameter_views([changed], document, pdf_bytes=pdf)
    assert _cached_source_views.cache_info().misses == cached.misses + 1
    assert not any(atom.stage == "conditioning" for atom in second[original.id].atomic_claims)
    assert any("8 days" in item.raw_value for item in second[original.id].conditions)
    assert original.conditions[1].raw_value == CONDITIONING


def test_wrong_pdf_bytes_cannot_reuse_a_valid_cached_derivation(source):
    document, pdf = source
    original = requirement()
    physical_parameter_views([original], document, pdf_bytes=pdf)
    cached = _cached_source_views.cache_info()
    with pytest.raises(ValueError, match="do not match"):
        physical_parameter_views([original], document, pdf_bytes=pdf + b"\n% altered bytes")
    assert _cached_source_views.cache_info().hits == cached.hits
    valid, _ = physical_parameter_views([original], document, pdf_bytes=pdf)
    assert valid[original.id].evidence


def test_physical_citation_does_not_repair_a_wrong_extracted_numeric_value(source):
    document, pdf = source
    original = requirement()
    original.atomic_claims[0].value = Decimal("61")
    before = original.model_dump_json()
    normalized = physical_normalized_requirements([original], document, pdf_bytes=pdf)[0]
    water = next(atom for atom in normalized.claims if atom.id == "req_source_claim_1")
    assert water.value == 61 and water.normalization_state == "unresolved"
    assert water.availability == "unknown" and water.parameter_id is None
    assert original.model_dump_json() == before

"""Friendly source labels and atom provenance must not conceal ambiguous identity."""

from scione.catalogs import load_reference_catalog
from scione.library.workspace import LibraryWorkspace
from scione.specifications.models import CustomerRequirement, RequestedMethod, RequirementEvidence
from scione.specifications.parameter_view import concise_catalog_name, parameter_review_view
from scione.structured.models import AtomicClaim


def test_concise_label_requires_one_exact_source_method_family():
    catalog = load_reference_catalog()
    source = CustomerRequirement(
        id="req_r",
        original_test_name="Long source description of the scoring and tape procedure",
        requested_methods=[RequestedMethod(code="ISO 60038:2021", role="primary")],
        evidence=[RequirementEvidence(page=1, quote="The scoring procedure uses ISO 60038:2021.")],
    )
    assert concise_catalog_name(source, catalog) == "Cross-cut adhesion"
    assert source.original_test_name.startswith("Long source")
    unknown = source.model_copy(
        update={"requested_methods": [RequestedMethod(code="ISO 60038:2099")]}
    )
    assert concise_catalog_name(unknown, catalog) is None
    composite = source.model_copy(
        update={
            "requested_methods": [
                *source.requested_methods,
                RequestedMethod(code="ASTM D1627-16", role="measurement"),
            ]
        }
    )
    assert concise_catalog_name(composite, catalog) is None


def test_native_provenance_survives_bad_identity_and_merged_identical_atoms():
    raw = "Specimen thickness is 3 mm."
    native = AtomicClaim(
        id="native_bad",
        parameter_id="specimen_length",
        label="Specimen length",
        operator="=",
        value=3,
        unit="mm",
        raw_text=raw,
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_r",
        original_test_name="Source test",
        atomic_claims=[
            native,
            native.model_copy(update={"id": "native_duplicate"}),
        ],
        evidence=[RequirementEvidence(page=1, quote=raw)],
    )
    view = parameter_review_view(source, LibraryWorkspace.present_claim)
    linked = [row for row in view["claims"] if row["source_atom_ids"]]
    assert len(linked) == 1
    assert linked[0]["source_atom_ids"] == ["native_bad", "native_duplicate"]
    assert linked[0]["normalization_state"] == "unresolved"
    assert linked[0]["parameter_id"] is None


def test_grounded_local_quantity_keeps_its_original_source_atom_for_editing():
    raw = "Core diameter shall be 100 mm."
    native = AtomicClaim(
        id="native_unknown_quantity", parameter_id=None, label="Unstructured Requirement",
        operator="=", value=100, unit="mm", raw_text=raw, source_pages=[1],
        normalization_state="unresolved",
    )
    source = CustomerRequirement(
        id="req_r", original_test_name="Source test", atomic_claims=[native],
        evidence=[RequirementEvidence(page=1, quote=raw)],
    )
    view = parameter_review_view(source, LibraryWorkspace.present_claim)
    assert len(view["claims"]) == 1
    assert view["claims"][0]["parameter_id"].startswith("source_quantity_")
    assert view["claims"][0]["source_atom_ids"] == [native.id]
    assert native.parameter_id is None


def test_grounded_local_prose_keeps_its_original_source_atom_for_editing():
    raw = "Record each specimen result separately."
    native = AtomicClaim(
        id="native_unknown_service", parameter_id=None, label="Unstructured Requirement",
        kind="reporting", operator="required", text_value=raw, raw_text=raw,
        source_pages=[1], normalization_state="unresolved",
    )
    source = CustomerRequirement(
        id="req_r", original_test_name="Source test", atomic_claims=[native],
        evidence=[RequirementEvidence(page=1, quote=raw)],
    )
    view = parameter_review_view(source, LibraryWorkspace.present_claim)
    assert len(view["claims"]) == 1
    assert view["claims"][0]["parameter_id"].startswith("source_fact_")
    assert view["claims"][0]["source_atom_ids"] == [native.id]
    assert native.parameter_id is None


def test_shared_quotation_does_not_link_distinct_phases_to_one_edit():
    raw = "ISO class 0 or 1 is required before and after thermal cycling."
    native = AtomicClaim(
        id="native_initial",
        parameter_id="adhesion_class",
        label="Adhesion class",
        kind="criterion",
        stage="initial",
        operator="one_of",
        values=[0, 1],
        unit="class",
        raw_text=raw,
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_r",
        original_test_name="Cross-cut adhesion",
        atomic_claims=[
            native,
            native.model_copy(
                update={
                    "id": "native_after",
                    "stage": "after_cycle",
                }
            ),
        ],
        evidence=[RequirementEvidence(page=1, quote=raw)],
    )
    view = parameter_review_view(source, LibraryWorkspace.present_claim)
    origins = {
        row["stage"]: row["source_atom_ids"] for row in view["claims"] if row["source_atom_ids"]
    }
    assert origins == {"initial": ["native_initial"], "after_cycle": ["native_after"]}


def test_intrinsic_measurement_stage_keeps_its_single_native_origin():
    raw = "Read gloss at five marked locations."
    native = AtomicClaim(
        id="native_positions",
        parameter_id="measurement_locations",
        label="Measurement locations",
        operator="=",
        value=5,
        raw_text=raw,
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_r",
        original_test_name="Gloss",
        atomic_claims=[native],
        evidence=[RequirementEvidence(page=1, quote=raw)],
    )
    view = parameter_review_view(source, LibraryWorkspace.present_claim)
    linked = [row for row in view["claims"] if row["source_atom_ids"]]
    assert len(linked) == 1
    assert linked[0]["source_atom_ids"] == ["native_positions"]
    assert linked[0]["stage"] == "measurement"


def test_same_value_with_different_context_does_not_share_native_origins():
    raw = "Measure 3 mm using the documented procedure."
    native = AtomicClaim(
        id="native_unscoped",
        parameter_id="custom_depth",
        label="Depth",
        operator="=",
        value=3,
        unit="mm",
        raw_text=raw,
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_r",
        original_test_name="Source test",
        atomic_claims=[
            native,
            native.model_copy(
                update={
                    "id": "native_specific",
                    "source_context": "steel witness",
                }
            ),
        ],
        evidence=[RequirementEvidence(page=1, quote=raw)],
    )
    view = parameter_review_view(source, LibraryWorkspace.present_claim)
    linked = {
        row.get("source_context"): row["source_atom_ids"]
        for row in view["claims"]
        if row["source_atom_ids"]
    }
    assert linked == {None: ["native_unscoped"], "steel witness": ["native_specific"]}

"""Identity metadata is explicit, offline and independent of client capability claims."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from scione.catalogs import (
    Alias,
    CatalogError,
    LabCapability,
    LabProfile,
    ReferenceCatalog,
    default_demo_lab,
    load_reference_catalog,
    validate_lab_references,
)
from scione.catalogs.seed import default_csp301_lab


def test_pilot_catalog_retains_aliases_and_explicit_synthetic_editions() -> None:
    catalog = load_reference_catalog()
    assert len(catalog.tests) == 99
    assert len(catalog.methods) == 154
    assert catalog.version == 5
    assert catalog.synthetic
    assert "not an authoritative" in catalog.source_note
    assert all(
        method.edition is None
        for method in catalog.methods
        if not method.id.startswith(("synthetic_", "bank_"))
    )
    pencil = catalog.test_by_id("pencil_hardness")
    assert Alias(text="铅笔硬度", language="zh") in pencil.aliases
    assert pencil.method_ids[0] == "iso_15184"
    assert catalog.method_by_id("iso_15184").identifier == "ISO 15184"


def test_seed_catalog_returns_fresh_objects() -> None:
    first = load_reference_catalog()
    first.tests.clear()
    assert len(load_reference_catalog().tests) == 99


def test_csp301_methods_are_distinct_from_the_original_pilot_methods() -> None:
    catalog = load_reference_catalog()
    assert catalog.test_by_id("cross_cut_adhesion").method_ids[:4] == [
        "iso_2409",
        "astm_d3359",
        "gb_t_9286",
        "synthetic_iso_60038_2021",
    ]
    method = catalog.method_by_id("synthetic_iso_60038_2021")
    assert method.identifier == "ISO 60038:2021"
    assert method.edition == "2021"
    assert "ISO 2409" not in {alias.text for alias in method.aliases}
    assert catalog.method_by_id("synthetic_astm_d1627_16").edition == "16"
    assert catalog.test_by_id("dry_film_thickness").method_ids[:1] == ["synthetic_iso_60152_2025"]


def test_csp301_lab_retains_data_team_declarations_without_inventing_coverage() -> None:
    lab = default_csp301_lab()
    validate_lab_references(lab, load_reference_catalog())
    assert len(lab.capabilities) == 4
    assert all(capability.price is None for capability in lab.capabilities)
    gloss = next(item for item in lab.capabilities if item.id == "cap_csp301_gloss")
    assert "post-cycle gloss and retention: not reportable" in gloss.notes
    assert "30 min between transfers" in gloss.declared_constraints
    assert "Reportable outputs:" in gloss.notes
    thickness = next(item for item in lab.capabilities if item.id == "cap_csp301_thickness")
    assert thickness.declared_constraints == "23 ±2 °C, 50 ±5% RH for 7 days."
    assert "Dry-film thickness: 1–1000 µm." in thickness.notes
    assert "topcoat" not in thickness.notes
    color = next(item for item in lab.capabilities if item.id == "cap_csp301_color")
    assert "measurement temperature not specified" in color.declared_constraints
    assert "D65" not in color.notes
    assert not any(item.structured_limits for item in lab.capabilities)


def test_csp301_lab_returns_independent_editable_profiles() -> None:
    first = default_csp301_lab()
    first.capabilities[0].declared_constraints = "Changed by a lab user"
    assert "Changed" not in default_csp301_lab().capabilities[0].declared_constraints


def test_pilot_customer_procedures_are_not_misrepresented_as_formal_standards() -> None:
    catalog = load_reference_catalog()
    assert catalog.method_by_id("customer_wave_scan").identifier.startswith("Customer procedure:")
    assert catalog.method_by_id("customer_gradient_oven").identifier.startswith(
        "Customer procedure:"
    )
    assert catalog.test_by_id("thermal_cycling").method_ids == []
    assert catalog.test_by_id("water_immersion").method_ids == ["iso_2812_2"]
    chemical = catalog.test_by_id("chemical_resistance")
    aliases = {alias.text.casefold() for alias in chemical.aliases}
    assert not aliases & {"water", "deionized water", "tree resin", "gasoline e10"}
    assert "source chemical/fluid section" in catalog.source_note


def test_demo_lab_is_explicitly_synthetic_and_references_valid_identities() -> None:
    lab = default_demo_lab()
    validate_lab_references(lab, load_reference_catalog())
    assert "Synthetic" in lab.description
    assert len(lab.capabilities) == 5
    stone = next(item for item in lab.capabilities if item.id == "cap_stone_chip")
    assert "23 °C only" in stone.declared_constraints
    assert stone.structured_limits[0].parameter == "temperature"
    assert stone.structured_limits[0].values == [Decimal("23")]
    pencil = next(item for item in lab.capabilities if item.id == "cap_pencil")
    assert pencil.structured_limits[0].values == [Decimal("750")]
    salt = next(item for item in lab.capabilities if item.id == "cap_salt_spray")
    assert salt.method_ids == ["astm_b117"]


@pytest.mark.parametrize("field", ["canonical_test_id", "method_ids"])
def test_unknown_reference_is_rejected(field: str) -> None:
    capability = default_demo_lab().capabilities[0]
    update = {field: ["not_a_method"] if field == "method_ids" else "not_a_test"}
    lab = LabProfile(
        name="Example", id="example", capabilities=[capability.model_copy(update=update)]
    )
    with pytest.raises(CatalogError, match="Unknown"):
        validate_lab_references(lab, load_reference_catalog())


def test_unrelated_method_is_rejected_without_inferring_equivalence() -> None:
    capability = LabCapability(
        id="one", canonical_test_id="pencil_hardness", method_ids=["iso_2409"]
    )
    lab = LabProfile(name="Example", id="example", capabilities=[capability])
    with pytest.raises(CatalogError, match="not associated"):
        validate_lab_references(lab, load_reference_catalog())


def test_zero_price_is_valid_and_stored_as_decimal() -> None:
    capability = LabCapability(
        id="one",
        canonical_test_id="pencil_hardness",
        price="0.00",
        currency="USD",
        pricing_basis="per panel",
    )
    assert capability.price == Decimal("0.00")
    assert isinstance(capability.price, Decimal)


@pytest.mark.parametrize(
    "update",
    [
        {"price": "1.00", "currency": "USD"},
        {"price": "0", "pricing_basis": "per test"},
        {"price": "-1", "currency": "USD", "pricing_basis": "per test"},
        {"price": "NaN", "currency": "USD", "pricing_basis": "per test"},
        {"price": "Infinity", "currency": "USD", "pricing_basis": "per test"},
        {"price": "5", "currency": "USD", "pricing_basis": " "},
    ],
)
def test_invalid_or_incomplete_price_is_rejected(update: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        LabCapability(id="one", canonical_test_id="pencil_hardness", **update)


def test_unknown_fields_and_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        LabCapability(id="one", canonical_test_id="gloss", equivalent_to="other")
    capability = default_demo_lab().capabilities[0]
    with pytest.raises(ValidationError, match="unique"):
        LabProfile(id="one", name="One", capabilities=[capability, capability])
    catalog = load_reference_catalog()
    payload = catalog.model_dump()
    payload["tests"].append(payload["tests"][0])
    with pytest.raises(ValidationError, match="unique"):
        ReferenceCatalog.model_validate(payload)


def test_catalog_rejects_unknown_method_association() -> None:
    payload = load_reference_catalog().model_dump()
    payload["tests"][0]["method_ids"].append("unknown")
    with pytest.raises(ValidationError, match="unknown method"):
        ReferenceCatalog.model_validate(payload)


def test_missing_method_remains_unspecified() -> None:
    capability = LabCapability(id="one", canonical_test_id="gloss")
    lab = LabProfile(id="one", name="One", capabilities=[capability])
    validate_lab_references(lab, load_reference_catalog())
    assert capability.method_ids == []

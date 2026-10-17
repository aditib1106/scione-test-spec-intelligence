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


def test_pilot_catalog_has_tagged_aliases_and_unknown_method_editions() -> None:
    catalog = load_reference_catalog()
    assert len(catalog.tests) == 15
    assert catalog.synthetic
    assert "not an authoritative" in catalog.source_note
    assert all(method.edition is None for method in catalog.methods)
    pencil = catalog.test_by_id("pencil_hardness")
    assert Alias(text="铅笔硬度", language="zh") in pencil.aliases
    assert pencil.method_ids == ["iso_15184"]
    assert catalog.method_by_id("iso_15184").identifier == "ISO 15184"


def test_seed_catalog_returns_fresh_objects() -> None:
    first = load_reference_catalog()
    first.tests.clear()
    assert len(load_reference_catalog().tests) == 15


def test_demo_lab_is_explicitly_synthetic_and_references_valid_identities() -> None:
    lab = default_demo_lab()
    validate_lab_references(lab, load_reference_catalog())
    assert "Synthetic" in lab.description
    assert len(lab.capabilities) == 5
    stone = next(item for item in lab.capabilities if item.id == "cap_stone_chip")
    assert "23 °C only" in stone.declared_constraints
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

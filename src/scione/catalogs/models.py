"""Small, typed identity catalog and lab declarations for the local prototype.

Method associations describe what a test can be identified by. They never assert
that different methods are interchangeable or that a lab meets a customer spec.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CatalogError(ValueError):
    """Raised when a lab declaration references unknown or unrelated identities."""


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Alias(CatalogModel):
    text: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=35)


class TestMethod(CatalogModel):
    __test__ = False

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    identifier: str = Field(min_length=1)
    name: str = Field(min_length=1)
    edition: str | None = None
    aliases: list[Alias] = Field(default_factory=list)


class CanonicalTest(CatalogModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: list[Alias] = Field(default_factory=list)
    method_ids: list[str] = Field(default_factory=list)

    @field_validator("method_ids")
    @classmethod
    def unique_method_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Method IDs must not be repeated.")
        return value


class ReferenceCatalog(CatalogModel):
    id: str
    name: str
    version: int = Field(ge=1)
    synthetic: bool = True
    source_note: str = Field(min_length=1)
    tests: list[CanonicalTest]
    methods: list[TestMethod]

    @model_validator(mode="after")
    def consistent_identities(self) -> Self:
        test_ids = [item.id for item in self.tests]
        method_ids = [item.id for item in self.methods]
        if len(test_ids) != len(set(test_ids)) or len(method_ids) != len(set(method_ids)):
            raise ValueError("Catalog test IDs and method IDs must each be unique.")
        known_methods = set(method_ids)
        for item in self.tests:
            if set(item.method_ids) - known_methods:
                raise ValueError(f"Test {item.id!r} references an unknown method.")
        return self

    def test_by_id(self, test_id: str) -> CanonicalTest:
        for item in self.tests:
            if item.id == test_id:
                return item
        raise CatalogError(f"Unknown canonical test ID: {test_id!r}.")

    def method_by_id(self, method_id: str) -> TestMethod:
        for item in self.methods:
            if item.id == method_id:
                return item
        raise CatalogError(f"Unknown method ID: {method_id!r}.")


class LabCapability(CatalogModel):
    """A lab's own declaration, not verification or accreditation evidence."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    canonical_test_id: str = Field(min_length=1)
    method_ids: list[str] = Field(default_factory=list)
    declared_constraints: str | None = None
    notes: str | None = None
    price: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    pricing_basis: str | None = Field(default=None, min_length=1)

    @field_validator("method_ids")
    @classmethod
    def unique_method_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Method IDs must not be repeated.")
        return value

    @model_validator(mode="after")
    def complete_pricing(self) -> Self:
        if self.price is not None and (not self.currency or not self.pricing_basis):
            raise ValueError("A price requires both currency and pricing_basis.")
        return self


class LabProfile(CatalogModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    description: str = ""
    version: int = Field(default=1, ge=1)
    capabilities: list[LabCapability] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_capability_ids(self) -> Self:
        ids = [item.id for item in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("Lab capability IDs must be unique.")
        return self


def validate_lab_references(lab: LabProfile, catalog: ReferenceCatalog) -> None:
    """Check identity references without inferring support or method equivalence."""

    for capability in lab.capabilities:
        test = catalog.test_by_id(capability.canonical_test_id)
        for method_id in capability.method_ids:
            catalog.method_by_id(method_id)
            if method_id not in test.method_ids:
                raise CatalogError(f"Method {method_id!r} is not associated with test {test.id!r}.")

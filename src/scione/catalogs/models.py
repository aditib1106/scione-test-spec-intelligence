"""Small, typed identity catalog and lab declarations for the local prototype.

Method associations describe what a test can be identified by. They never assert
that different methods are interchangeable or that a lab meets a customer spec.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

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


class LabOperatingLimit(CatalogModel):
    """Explicit lab input for deterministic operating-condition comparison.

    Numeric values use the named canonical unit. Qualitative declarations are
    narrow, exact phrases, not permission to infer arbitrary semantic coverage.
    These describe what a lab can perform, not material pass/fail guarantees.
    """

    parameter: Literal[
        "temperature",
        "load",
        "spacing",
        "duration",
        "angle",
        "pressure",
        "cycles",
        "condition",
        "scope",
        "instruction",
    ]
    minimum: Decimal | None = Field(default=None, allow_inf_nan=False)
    maximum: Decimal | None = Field(default=None, allow_inf_nan=False)
    values: list[Decimal] = Field(default_factory=list)
    unit: str | None = None
    supported_values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_limits(self) -> Self:
        units = {
            "temperature": "°C",
            "load": "g",
            "spacing": "mm",
            "duration": "h",
            "angle": "°",
            "pressure": "bar",
            "cycles": "cycles",
        }
        if self.parameter in units:
            if self.unit != units[self.parameter]:
                raise ValueError(f"{self.parameter} limits must use {units[self.parameter]}.")
            if self.supported_values:
                raise ValueError("Numeric limits cannot also declare qualitative phrases.")
            if self.values and (self.minimum is not None or self.maximum is not None):
                raise ValueError("Choose either discrete numeric values or a numeric range.")
            if not self.values and self.minimum is None and self.maximum is None:
                raise ValueError("A numeric limit requires a bound or discrete values.")
            if any(not value.is_finite() for value in self.values):
                raise ValueError("Discrete numeric values must be finite.")
            numeric_values = [value for value in [self.minimum, self.maximum] if value is not None]
            if self.parameter != "temperature" and any(
                value < 0 for value in numeric_values + self.values
            ):
                raise ValueError("Only temperature limits may be negative.")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("Minimum must not exceed maximum.")
        else:
            if (
                self.unit is not None
                or self.minimum is not None
                or self.maximum is not None
                or self.values
            ):
                raise ValueError("Qualitative declarations only accept supported_values.")
            if not self.supported_values or any(
                not value.strip() for value in self.supported_values
            ):
                raise ValueError("A qualitative declaration requires nonblank supported phrases.")
        return self


class LabCapability(CatalogModel):
    """A lab's own declaration, not verification or accreditation evidence."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    canonical_test_id: str = Field(min_length=1)
    method_ids: list[str] = Field(default_factory=list)
    declared_constraints: str | None = None
    structured_limits: list[LabOperatingLimit] = Field(default_factory=list)
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

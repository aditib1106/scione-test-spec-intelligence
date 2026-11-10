"""Additive, source-preserving contracts for atomic requirements and comparisons."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from scione.numeric_literals import validate_numeric_complexity

NORMALIZER_VERSION = "atomic_requirements_v0_38"
ClaimKind = Literal["condition", "criterion", "reporting", "scope", "instruction"]


class AtomicClaim(BaseModel):
    """One source-stated requirement or lab declaration, in its own context.

    A criterion is a specimen acceptance threshold. On the lab side it is
    compared with the ability to measure/report that quantity, never with a
    prediction that the specimen will pass. Missing fields are not capability.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=160)
    parameter_id: str | None = Field(default=None, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    kind: ClaimKind = "condition"
    stage: str = Field(default="general", min_length=1, max_length=100)
    operator: Literal["=", "range", "<=", ">=", "<", ">", "one_of", "text", "required"] = "text"
    value: Decimal | None = Field(default=None, allow_inf_nan=False)
    minimum: Decimal | None = Field(default=None, allow_inf_nan=False)
    maximum: Decimal | None = Field(default=None, allow_inf_nan=False)
    tolerance: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    values: list[Decimal] = Field(default_factory=list)
    unit: str | None = Field(default=None, max_length=40)
    text_value: str | None = Field(default=None, max_length=2000)
    availability: Literal["supported", "unsupported", "unknown"] = "supported"
    raw_text: str = Field(min_length=1, max_length=8000)
    source_context: str | None = Field(default=None, max_length=8000)
    source_pages: list[int] = Field(default_factory=list)
    normalization_state: Literal["parsed", "unresolved", "reviewed"] = "parsed"
    configuration: str | None = Field(default=None, max_length=160)
    sequence: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def consistent_values(self) -> Self:
        for value in [self.value, self.minimum, self.maximum, self.tolerance, *self.values]:
            if value is not None:
                validate_numeric_complexity(value)
        if any(page < 1 for page in self.source_pages):
            raise ValueError("Source pages must be positive.")
        if any(not value.is_finite() for value in self.values):
            raise ValueError("Numeric values must be finite.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Minimum must not exceed maximum.")
        if self.operator == "range" and self.minimum is None and self.maximum is None:
            raise ValueError("A range needs a bound.")
        if self.operator in {"=", "<=", ">=", "<", ">"} and self.value is None:
            raise ValueError("A numeric operator needs a value.")
        if self.operator == "one_of" and not self.values:
            raise ValueError("A discrete requirement needs values.")
        if not self.parameter_id and self.normalization_state != "unresolved":
            raise ValueError("Unidentified parameters must remain unresolved.")
        return self

    @property
    def display_value(self) -> str:
        def number(value: Decimal) -> str:
            return (
                (format(value, "f").rstrip("0").rstrip(".") or "0")
                if "." in format(value, "f")
                else format(value, "f")
            )

        if self.operator == "range":
            low = number(self.minimum) if self.minimum is not None else "…"
            high = number(self.maximum) if self.maximum is not None else "…"
            text = f"{low}–{high}"
        elif self.operator == "one_of":
            text = " / ".join(number(value) for value in self.values)
        elif self.value is not None:
            prefix = "" if self.operator == "=" else f"{self.operator} "
            text = f"{prefix}{number(self.value)}"
            if self.tolerance is not None:
                text += f" ± {number(self.tolerance)}"
        else:
            text = self.text_value or self.raw_text
        return f"{text} {self.unit}" if self.unit else text


class NormalizedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    test_name: str
    normalizer_version: str = NORMALIZER_VERSION
    claims: list[AtomicClaim] = Field(default_factory=list)
    review_issues: list[str] = Field(default_factory=list)
    normalization_actions: list[dict] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def retain_legacy_empty_audit_shape(self, handler):
        data = handler(self)
        # Empty additive audit metadata must not invalidate the fingerprint of
        # an immutable older matching snapshot. Nonempty actions are hashed.
        if not self.normalization_actions:
            data.pop("normalization_actions", None)
        return data


class ClaimCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    parameter_id: str | None = None
    label: str
    kind: str
    stage: str = "general"
    unit: str | None = None
    requested: str
    declared: str | None = None
    status: Literal["supported", "gap", "unknown", "informational"]
    reason: str
    source_pages: list[int] = Field(default_factory=list)
    requirement_claim: AtomicClaim | None = None
    capability_claim_ids: list[str] = Field(default_factory=list)
    comparison_basis: Literal[
        "semantic_alignment", "deterministic_exact_fields", "deterministic_literal_text",
        "literal_descriptive_correspondence",
        "source_optional_route_policy",
    ] | None = None
    comparison_metadata: list[dict] = Field(default_factory=list)


class StructuredComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    status: Literal["fully_matched", "partially_matched", "not_matched", "needs_information"]
    checks: list[ClaimCheck]
    supported_count: int = 0
    gap_count: int = 0
    unknown_count: int = 0

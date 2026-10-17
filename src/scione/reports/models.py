"""Separate human matching choices, quote inputs, and immutable report snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scione.catalogs.models import LabCapability, LabProfile, ReferenceCatalog
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.specifications.store import RequirementReviewSet

MatchStatus = Literal[
    "in_house", "method_gap", "test_gap", "needs_review", "condition_gap", "excluded"
]
STATUS_LABELS: dict[str, str] = {
    "in_house": "Method listed in-house",
    "method_gap": "Method gap",
    "test_gap": "Not in lab catalog",
    "needs_review": "Needs matching review",
    "condition_gap": "Condition / scope gap",
    "excluded": "Excluded from scope",
}


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def multiply_amounts(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite source Decimals without rounding to the ambient context."""

    with localcontext() as context:
        context.prec = max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return left * right


def sum_amounts(left: Decimal, right: Decimal) -> Decimal:
    """Add finite draft amounts exactly, keeping small amounts beside large ones."""

    lower_exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
    highest_digit = max(left.adjusted(), right.adjusted())
    with localcontext() as context:
        context.prec = max(1, highest_digit - lower_exponent + 2)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return left + right


class MappingChoice(ReportModel):
    """A human identity choice is NOT an alteration of source extraction."""

    canonical_test_id: str | None = None
    selected_method_codes: list[str] | None = None
    capability_id: str | None = None
    capability_verdict: Literal["unreviewed", "supported", "unsupported"] = "unreviewed"
    lab_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    note: str = Field(min_length=1, max_length=4000)


class QuoteChoice(ReportModel):
    """Manual quantity and applicability; never inferred from candidate row counts."""

    quantity: Decimal = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    scope_and_basis_confirmed: Literal[True]
    lab_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    note: str = Field(min_length=1, max_length=4000)


class ReportWorkspace(ReportModel):
    document_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    run_id: str
    version: int = Field(default=0, ge=0)
    mappings: dict[str, MappingChoice] = Field(default_factory=dict)
    quantities: dict[str, QuoteChoice] = Field(default_factory=dict)


class QuoteLine(ReportModel):
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    unit_price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    pricing_basis: str = Field(min_length=1)
    subtotal: Decimal = Field(ge=0, allow_inf_nan=False)
    note: str


class GapLine(ReportModel):
    requirement: CustomerRequirement
    canonical_test_id: str | None = None
    canonical_test_name: str | None = None
    candidate_test_ids: list[str] = Field(default_factory=list)
    match_basis: str
    method_ids: list[str] = Field(default_factory=list)
    unknown_methods: list[str] = Field(default_factory=list)
    considered_method_codes: list[str] = Field(default_factory=list)
    capability: LabCapability | None = None
    lab_method_identifiers: list[str] = Field(default_factory=list)
    status: MatchStatus
    reasons: list[str] = Field(default_factory=list)
    extraction_review_state: Literal["ready", "confirmed", "needs_attention", "excluded"]
    extraction_review_reasons: list[str] = Field(default_factory=list)
    mapping_choice: MappingChoice | None = None
    quote: QuoteLine | None = None


class ReportSnapshot(ReportModel):
    schema_version: Literal["0.1"] = "0.1"
    report_id: str
    created_at: datetime
    status: Literal["draft"] = "draft"
    document_id: str
    original_name: str
    document_sha256: str
    source_run: CustomerExtractionRun
    source_review: RequirementReviewSet
    lab: LabProfile
    catalog: ReferenceCatalog
    workspace: ReportWorkspace
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    lines: list[GapLine]
    totals_by_currency: dict[str, Decimal] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        if not (
            self.document_id
            == self.source_run.document_id
            == self.source_review.document_id
            == self.workspace.document_id
        ):
            raise ValueError("Report source documents do not agree.")
        if not self.source_run.run_id == self.source_review.run_id == self.workspace.run_id:
            raise ValueError("Report source extraction runs do not agree.")
        source_ids = [item.id for item in self.source_run.extraction.requirements]
        if [line.requirement.id for line in self.lines] != source_ids:
            raise ValueError("Every extracted requirement must remain represented in source order.")
        totals: dict[str, Decimal] = {}
        for line in self.lines:
            quote = line.quote
            if quote is None:
                continue
            if line.status == "excluded" or line.extraction_review_state == "excluded":
                raise ValueError("Excluded requirements must not have a quote.")
            if quote.subtotal != multiply_amounts(quote.quantity, quote.unit_price):
                raise ValueError("Quote subtotal must equal the exact quantity × unit price.")
            totals[quote.currency] = sum_amounts(
                totals.get(quote.currency, Decimal("0")), quote.subtotal
            )
        if (
            any(not amount.is_finite() or amount < 0 for amount in self.totals_by_currency.values())
            or totals != self.totals_by_currency
        ):
            raise ValueError("Report currency totals must equal the exact quoted line subtotals.")
        return self

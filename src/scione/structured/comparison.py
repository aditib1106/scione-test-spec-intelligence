"""Compare explicit atomic claims; AI is not called and missing facts stay unknown."""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from scione.parameters import (
    contexts_match,
    evaluate_pair,
    generic_reporting_unavailable,
    normalize_claim,
    normalize_claims,
)
from scione.structured.models import (
    AtomicClaim,
    ClaimCheck,
    NormalizedRequirement,
    StructuredComparison,
)

_UNIT_FACTORS = {
    "°C": ("temperature", Decimal(1)),
    "h": ("duration", Fraction(1)),
    "min": ("duration", Fraction(1, 60)),
    "s": ("duration", Fraction(1, 3600)),
    "days": ("duration", Fraction(24)),
    "day": ("duration", Fraction(24)),
    "minutes": ("duration", Fraction(1, 60)),
    "hours": ("duration", Fraction(1)),
    "µm": ("length", Decimal(1)),
    "μm": ("length", Decimal(1)),
    "mm": ("length", Decimal(1000)),
    "cm": ("length", Decimal(10000)),
}


def _context_matches(request: AtomicClaim, declaration: AtomicClaim) -> bool:
    return contexts_match(request, declaration)


def _factor(request: AtomicClaim, declaration: AtomicClaim) -> Fraction | None:
    if request.unit is not None and request.unit == declaration.unit:
        return Fraction(1)
    # Counts can be expressed without repeating the parameter's own count label.
    # This does not make a missing physical unit (such as hours) comparable.
    count_units = {
        "cycles": {None, "cycle", "cycles"},
        "measurement_locations": {None, "location", "locations"},
    }
    units = count_units.get(request.parameter_id)
    if (
        request.parameter_id == declaration.parameter_id
        and units is not None
        and request.unit in units
        and declaration.unit in units
    ):
        return Fraction(1)
    first = _UNIT_FACTORS.get(request.unit or "")
    second = _UNIT_FACTORS.get(declaration.unit or "")
    if first and second and first[0] == second[0]:
        return Fraction(first[1]) / Fraction(second[1])
    return None


def _points(claim: AtomicClaim) -> tuple[Decimal | None, Decimal | None]:
    if claim.operator == "one_of":
        return min(claim.values), max(claim.values)
    if claim.minimum is not None or claim.maximum is not None:
        return claim.minimum, claim.maximum
    if claim.value is not None:
        tolerance = claim.tolerance or Decimal(0)
        return claim.value - tolerance, claim.value + tolerance
    return None, None


def _one(request: AtomicClaim, declaration: AtomicClaim) -> tuple[str, str]:
    typed = evaluate_pair(request, declaration)
    if typed is not None:
        return typed.status, typed.reason
    if declaration.availability == "unsupported":
        return "gap", "The lab explicitly declares this output or condition unavailable."
    if declaration.availability == "unknown" or declaration.normalization_state == "unresolved":
        return "unknown", "The lab has not declared a usable value for this requirement."
    if request.operator == "required":
        if (
            request.kind in {"reporting", "criterion"}
            and declaration.kind == "reporting"
            and declaration.operator not in {"required", "text"}
        ):
            return "supported", "The lab explicitly declares this measurable/reportable output."
        if declaration.operator in {"required", "text"}:
            left = (request.text_value or "").casefold().strip()
            right = (declaration.text_value or "").casefold().strip()
            if not left or left == right:
                return (
                    "supported",
                    "The requested item is explicitly included in the lab declaration.",
                )
        return (
            "unknown",
            "The declaration does not establish the requested item in the same context.",
        )
    if request.operator == "text":
        left = (request.text_value or request.raw_text).casefold().strip()
        right = (declaration.text_value or declaration.raw_text).casefold().strip()
        if left == right:
            return "supported", "The requested value matches the lab's explicit declaration."
        return "unknown", "The wording differs; a matching declaration has not been established."
    if declaration.operator in {"required", "text"}:
        return "unknown", "A numeric operating range or reportable output range is not recorded."
    if declaration.operator == "=" and declaration.tolerance and declaration.kind == "condition":
        return "unknown", "A nominal setting with tolerance does not establish a selectable range."
    factor = _factor(request, declaration)
    if factor is None:
        return "unknown", "The units or measurement scales do not establish a valid comparison."
    if request.operator in {"<", ">"}:
        return (
            "unknown",
            "A strict boundary needs the lab's measurement resolution or a reviewed limit.",
        )
    if request.operator in {"<=", ">="} and request.kind == "condition":
        return (
            "unknown",
            "The operational inequality does not specify a complete requested operating range.",
        )
    low, high = _points(request)
    low = Fraction(low) * factor if low is not None else None
    high = Fraction(high) * factor if high is not None else None
    cap_low, cap_high = _points(declaration)
    if low is None or high is None:
        return "unknown", "The requested range has an unspecified boundary."
    if cap_low is None and cap_high is None:
        return "unknown", "No comparable lab range is recorded."
    if declaration.operator == "range" and (cap_low is None or cap_high is None):
        if (cap_low is not None and low < cap_low) or (cap_high is not None and high > cap_high):
            return "gap", "The request exceeds a recorded lab range boundary."
        return "unknown", "One boundary of the lab's range is not recorded."
    if declaration.operator == "one_of":
        request_points = (
            [Fraction(value) * factor for value in request.values]
            if request.operator == "one_of"
            else [low, high]
        )
        covered = low == high or request.operator == "one_of"
        covered = covered and all(value in declaration.values for value in request_points)
    elif declaration.operator in {"<=", "<", ">=", ">"}:
        return (
            "unknown",
            "The lab's open-ended declaration needs a reviewed operating/reporting range.",
        )
    else:
        covered = (cap_low is None or low >= cap_low) and (cap_high is None or high <= cap_high)
    if covered:
        if request.kind == "criterion":
            return (
                "supported",
                "The lab can measure/report the requested threshold or scale; "
                "specimen pass/fail is not predicted.",
            )
        return (
            "supported",
            "The requested value or range is covered by this lab declaration in the same stage.",
        )
    return "gap", "The requested value or range lies outside the lab's declared capability."


def compare_requirement(
    normalized: NormalizedRequirement,
    capability_claims: list[AtomicClaim],
    method_status: Literal["supported", "gap", "unknown"] = "supported",
) -> StructuredComparison:
    """Compare one test without borrowing limits from incompatible configurations.

    Callers must choose a single capability configuration. If claims carry
    different configuration IDs, each requested claim must be satisfied within
    one common configuration, rather than mixing useful limits across setups.
    """
    method_reasons = {
        "supported": "The requested test/method is present in the selected lab declaration.",
        "gap": "The requested test/method is not listed by this lab.",
        "unknown": "The requested test/method still needs an identity decision.",
    }
    checks = [
        ClaimCheck(
            id=f"{normalized.requirement_id}_method",
            parameter_id="test_method",
            label="Test / method",
            kind="method",
            requested=normalized.test_name,
            status=method_status,
            reason=method_reasons[method_status],
        )
    ]
    # Work on canonical copies. Exported requirement/source claims retain their
    # original names, units and values for provenance and extraction evaluation.
    capability_claims = normalize_claims(capability_claims)
    selected_configs = {claim.configuration for claim in capability_claims if claim.configuration}
    operating_clauses = {
        claim.raw_text
        for claim in normalized.claims
        if claim.parameter_id in {"temperature", "duration", "relative_humidity", "cycles"}
        and claim.operator not in {"text", "required"}
    }
    for source_claim in normalized.claims:
        claim = normalize_claim(source_claim)
        check = ClaimCheck(
            id=claim.id,
            parameter_id=claim.parameter_id,
            label=claim.label,
            kind=claim.kind,
            stage=claim.stage,
            unit=claim.unit,
            requested=claim.display_value,
            status="unknown",
            reason="The lab has no declaration for this parameter in the requested stage.",
            source_pages=claim.source_pages,
            requirement_claim=source_claim,
        )
        workflow = (
            claim.kind == "instruction"
            and not any(
                value is not None
                for value in (claim.value, claim.minimum, claim.maximum, claim.tolerance)
            )
            and not claim.values
            and claim.raw_text not in operating_clauses
            and re.search(
                r"written approval|customer.*sign.off|deviations? before testing|"
                r"retain.*raw observations|report panel.level|photos? of the grids|"
                r"conceal.*failed panel|submit.*topcoat lots|from each lot.*coat|"
                r"repeatable.*position map|"
                r"assign.*baseline checks|dispute retention|may request.*ASTM|"
                r"not declaring.*interchangeable|ASTM route.*approved|"
                r"corresponding acceptance class",
                claim.raw_text,
                re.I,
            )
        )
        if workflow:
            check.status = "informational"
            check.reason = (
                "Preparation, evidence or approval task retained for the lab's workflow; "
                "this is not an automatic capability judgement."
            )
            checks.append(check)
            continue
        if claim.normalization_state == "unresolved" or not claim.parameter_id:
            check.reason = (
                "This source clause has been retained but has not been normalized reliably."
            )
            checks.append(check)
            continue
        candidates = [
            item
            for item in capability_claims
            if item.parameter_id == claim.parameter_id
            and _context_matches(claim, item)
            and (
                item.kind == claim.kind or (claim.kind == "criterion" and item.kind == "reporting")
            )
        ]
        if claim.configuration:
            candidates = [
                item
                for item in candidates
                if item.configuration == claim.configuration
                or generic_reporting_unavailable(claim, item)
            ]
        elif len(selected_configs) > 1:
            check.reason = (
                "Several lab configurations are present. Select one configuration before comparing."
            )
            checks.append(check)
            continue
        if candidates:
            assessments = [(item, *_one(claim, item)) for item in candidates]
            # Contradictory declarations need review, not a convenient positive.
            states = {state for _, state, _ in assessments}
            if "supported" in states and "gap" in states:
                check.status = "unknown"
                check.reason = "Lab declarations conflict for the same parameter and stage."
                check.declared = "; ".join(item.display_value for item, _, _ in assessments)
                check.capability_claim_ids = [item.id for item, _, _ in assessments]
            else:
                priority = {"supported": 0, "gap": 1, "unknown": 2}
                item, status, reason = min(assessments, key=lambda result: priority[result[1]])
                check.status = status
                check.reason = reason
                check.declared = item.display_value
                check.capability_claim_ids = [item.id]
        checks.append(check)
    counts = {
        state: sum(item.status == state for item in checks)
        for state in ("supported", "gap", "unknown")
    }
    if method_status == "gap":
        status = "not_matched"
    elif counts["gap"]:
        status = "partially_matched" if counts["supported"] else "not_matched"
    elif counts["unknown"]:
        status = "needs_information"
    else:
        status = "fully_matched"
    return StructuredComparison(
        requirement_id=normalized.requirement_id,
        status=status,
        checks=checks,
        supported_count=counts["supported"],
        gap_count=counts["gap"],
        unknown_count=counts["unknown"],
    )

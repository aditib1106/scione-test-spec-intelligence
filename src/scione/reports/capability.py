"""Conservative, offline comparison of stated operating requirements.

No language model, standard body text or pass/fail inference is used here.
Unknown prose remains unknown. An automatic positive result only means that
the explicitly stated operating requirements fit explicit lab declarations;
it never promises that the submitted material will pass acceptance criteria.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scione.catalogs.models import LabCapability, LabOperatingLimit
from scione.specifications.models import CustomerRequirement


class CapabilityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    requested: str
    declared: str | None = None
    status: Literal["supported", "gap", "unknown", "informational"]


class CapabilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["supported", "gap", "unknown"] = "unknown"
    reasons: list[str] = Field(default_factory=list)
    checks: list[CapabilityCheck] = Field(default_factory=list)


@dataclass(frozen=True)
class _Quantity:
    parameter: str
    low: Decimal
    high: Decimal
    source: str


_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_QUANTITY = re.compile(
    rf"(?P<low>{_NUMBER})\s*(?:(?:\bto\b|–|—|-)\s*(?P<high>{_NUMBER})\s*)?"
    r"(?P<unit>°\s*C|degrees?\s+C|kg|g|mm|cm|minutes?|mins?|hours?|h|s|seconds?|bar|cycles?|°)"
    r"(?![\w²/])",
    re.IGNORECASE,
)
_SECTION_ONLY = re.compile(r"(?:section\s*)?\d+(?:\.\d+)+(?:\s*[,;]\s*\d+(?:\.\d+)+)*", re.I)
_SOURCE_HEADINGS = {
    "3.1 appearance",
    "3.2 mechanical",
    "4.1 humidity, corrosion and thermal",
    "4.2 weathering",
    "4.3 chemical and fluid resistance",
    "5. repair and compatibility",
}
_RESTRICTIONS = re.compile(
    r"\b(?:only|except|unless|not\s+(?:declared|supported|available)|to\s+confirm|"
    r"requires?\s+(?:review|confirmation)|limited\s+to|cannot|can't|excluded|excluding|"
    r"unsupported|unavailable|up\s+to|at\s+(?:most|least)|max(?:imum)?|min(?:imum)?)\b|[<>≤≥±]",
    re.I,
)
_UNITS = {
    "°c": ("temperature", Decimal(1)),
    "degreec": ("temperature", Decimal(1)),
    "degreesc": ("temperature", Decimal(1)),
    "kg": ("load", Decimal(1000)),
    "g": ("load", Decimal(1)),
    "mm": ("spacing", Decimal(1)),
    "cm": ("spacing", Decimal(10)),
    "h": ("duration", Decimal(1)),
    "hour": ("duration", Decimal(1)),
    "hours": ("duration", Decimal(1)),
    "min": ("duration", Decimal(1) / Decimal(60)),
    "mins": ("duration", Decimal(1) / Decimal(60)),
    "minute": ("duration", Decimal(1) / Decimal(60)),
    "minutes": ("duration", Decimal(1) / Decimal(60)),
    "s": ("duration", Decimal(1) / Decimal(3600)),
    "second": ("duration", Decimal(1) / Decimal(3600)),
    "seconds": ("duration", Decimal(1) / Decimal(3600)),
    "bar": ("pressure", Decimal(1)),
    "cycle": ("cycles", Decimal(1)),
    "cycles": ("cycles", Decimal(1)),
    "°": ("angle", Decimal(1)),
}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("−", "-")
    return re.sub(r"\s+", " ", value).strip(" .;,\n")


def _parse(value: str, name: str = "") -> tuple[list[_Quantity], str]:
    text = _normalized(value)
    name = _normalized(name)
    # Bounds, lists with a shared unit, tolerances, repetitions and procedural
    # sequences need a richer contract. Never strip them into a false exact value.
    if re.search(r"[<>≤≥±]|\b(?:at least|up to|within|minimum|maximum)\b", text):
        return [], text
    matches = list(_QUANTITY.finditer(text))
    quantities = []
    residual_parts = []
    position = 0
    for match in matches:
        normalized_unit = re.sub(r"\s+", "", match["unit"]).casefold()
        parameter, factor = _UNITS[normalized_unit]
        if re.search(r"\d\s*[x×]\s*$", text[: match.start()]):
            # Repeated grit masses or panel matrices are not applied loads.
            # Keep them unresolved while checking independent temperatures.
            continue
        if parameter == "spacing" and not re.search(r"\bspacing\b|间距", text + " " + name):
            # A dolly diameter, grit size or fall height is not cutter spacing.
            continue
        low = Decimal(match["low"])
        high = Decimal(match["high"] or match["low"])
        with localcontext() as context:
            context.prec = max(len(match["low"]), len(match["high"] or ""), 28) + 12
            if normalized_unit in {"min", "mins", "minute", "minutes"}:
                low, high = low / Decimal(60), high / Decimal(60)
            elif normalized_unit in {"s", "second", "seconds"}:
                low, high = low / Decimal(3600), high / Decimal(3600)
            elif factor != 1:
                low, high = low * factor, high * factor
        if high < low:
            return [], text
        quantities.append(_Quantity(parameter, low, high, match.group()))
        residual_parts.append(text[position : match.start()])
        position = match.end()
    residual_parts.append(text[position:])
    residual = " ".join(residual_parts)
    # Remove only syntax and labels whose meaning is represented by a parsed
    # parameter. In particular never erase 'only', exclusions or an unknown medium.
    removable = ["at", "and", "for"]
    parameters = {item.parameter for item in quantities}
    if "load" in parameters:
        removable += ["test load", "load"]
    if "temperature" in parameters:
        removable += ["temperature", "conditioning temperature", "test temperature"]
    if "spacing" in parameters:
        removable += ["spacing"]
    if "duration" in parameters:
        removable += ["duration", "exposure", "exposure time"]
    if "angle" in parameters:
        removable += ["angle", "measurement angle"]
    if "pressure" in parameters:
        removable += ["pressure"]
    for word in sorted(removable, key=len, reverse=True):
        residual = re.sub(rf"\b{re.escape(word)}\b", " ", residual)
    residual = re.sub(r"[\s,;:/()]+", " ", residual).strip()
    return quantities, residual


def _fits(quantity: _Quantity, limit: LabOperatingLimit) -> bool:
    if limit.values:
        return quantity.low == quantity.high and quantity.low in limit.values
    return (limit.minimum is None or quantity.low >= limit.minimum) and (
        limit.maximum is None or quantity.high <= limit.maximum
    )


def _limit_label(limit: LabOperatingLimit) -> str:
    if limit.values:
        return ", ".join(f"{value:f}" for value in limit.values) + f" {limit.unit}"
    low = f"{limit.minimum:f}" if limit.minimum is not None else "unbounded"
    high = f"{limit.maximum:f}" if limit.maximum is not None else "unbounded"
    return f"{low} to {high} {limit.unit}"


def _limit_inside(limit: LabOperatingLimit, quantity: _Quantity) -> bool:
    if limit.values:
        return all(quantity.low <= value <= quantity.high for value in limit.values)
    return (
        limit.minimum is not None
        and limit.maximum is not None
        and limit.minimum >= quantity.low
        and limit.maximum <= quantity.high
    )


def _qualitative_limits(capability: LabCapability, parameter: str) -> set[str]:
    return {
        _normalized(value)
        for limit in capability.structured_limits
        if limit.parameter == parameter
        for value in limit.supported_values
    }


def _source_locator(value: str) -> bool:
    normalized = _normalized(value)
    return _SECTION_ONLY.fullmatch(normalized) is not None or normalized in _SOURCE_HEADINGS


def assess_capability(
    requirement: CustomerRequirement, capability: LabCapability
) -> CapabilityAssessment:
    """Compare operations only; caller must separately validate test/method identity.

    Missing declarations and unparsed customer clauses remain review items.
    Contradictory typed and free-text declarations cannot auto-confirm support.
    """

    checks: list[CapabilityCheck] = []
    reasons: list[str] = []
    conflicting = False
    constraints = capability.declared_constraints or ""
    free_quantities, _ = _parse(constraints)
    for lab_text in (constraints, capability.notes or ""):
        text_quantities, text_residual = _parse(lab_text)
        for quantity in text_quantities:
            limits = [
                item
                for item in capability.structured_limits
                if item.parameter == quantity.parameter
            ]
            conflict = limits and not any(_fits(quantity, item) for item in limits)
            if re.search(r"\bonly\b", lab_text, re.I):
                conflict = conflict or any(not _limit_inside(item, quantity) for item in limits)
            if conflict:
                conflicting = True
                checks.append(
                    CapabilityCheck(
                        label="Conflicting lab declarations",
                        requested=quantity.source,
                        declared="; ".join(_limit_label(item) for item in limits),
                        status="unknown",
                    )
                )
        # Unresolved existing restrictions must not vanish when typed inputs are
        # added, including operational restrictions recorded in the notes field.
        if _RESTRICTIONS.search(text_residual) and text_residual != "only":
            checks.append(
                CapabilityCheck(
                    label="Lab restriction needs clarification",
                    requested=lab_text,
                    declared=lab_text,
                    status="unknown",
                )
            )

    for condition in requirement.conditions:
        text = condition.raw_value
        if condition.unit and not re.search(re.escape(condition.unit), text, re.I):
            text += f" {condition.unit}"
        if condition.context:
            text += f"; {condition.context}"
        if _normalized(text) in _qualitative_limits(capability, "condition"):
            checks.append(
                CapabilityCheck(
                    label=condition.name,
                    requested=text,
                    declared=text,
                    status="supported",
                )
            )
            continue
        quantities, residual = _parse(text, condition.name)
        for quantity in quantities:
            limits = [
                item
                for item in capability.structured_limits
                if item.parameter == quantity.parameter
            ]
            if not limits:
                status = "unknown"
                declared = None
            else:
                status = "supported" if any(_fits(quantity, limit) for limit in limits) else "gap"
                declared = "; ".join(_limit_label(limit) for limit in limits)
                # A broad new typed range must not override a narrower old
                # free-text '23 °C only' declaration.
                free_for_parameter = [
                    item for item in free_quantities if item.parameter == quantity.parameter
                ]
                if status == "supported" and re.search(r"\bonly\b", constraints, re.I):
                    if free_for_parameter and not any(
                        quantity.low >= item.low and quantity.high <= item.high
                        for item in free_for_parameter
                    ):
                        conflicting = True
                        status = "unknown"
                        declared += f"; existing restriction: {constraints}"
            checks.append(
                CapabilityCheck(
                    label=quantity.parameter.title(),
                    requested=quantity.source,
                    declared=declared,
                    status=status,
                )
            )
        if residual or not quantities:
            checks.append(
                CapabilityCheck(
                    label=condition.name,
                    requested=text,
                    declared="No exact structured operating declaration covers this full clause.",
                    status="unknown",
                )
            )

    for parameter, values in (
        ("scope", requirement.applicability),
        ("instruction", requirement.special_instructions),
    ):
        declarations = _qualitative_limits(capability, parameter)
        for value in values:
            if _source_locator(value):
                continue
            checks.append(
                CapabilityCheck(
                    label="Customer scope" if parameter == "scope" else "Special instruction",
                    requested=value,
                    declared=value if _normalized(value) in declarations else None,
                    status="supported" if _normalized(value) in declarations else "unknown",
                )
            )
    if requirement.acceptance_criteria:
        checks.append(
            CapabilityCheck(
                label="Material acceptance criteria",
                requested="; ".join(item.raw_value for item in requirement.acceptance_criteria),
                declared=(
                    "Pass/fail will be measured during testing, not predicted by this comparison."
                ),
                status="informational",
            )
        )

    operational = [item for item in checks if item.status != "informational"]
    if conflicting:
        status = "unknown"
        reasons.append(
            "Typed operating limits conflict with existing lab text; clarify the declaration."
        )
    elif any(item.status == "gap" for item in operational):
        status = "gap"
        reasons.append(
            "At least one stated operating requirement is outside the lab's declared limits."
        )
    elif operational and all(item.status == "supported" for item in operational):
        status = "supported"
        reasons.append(
            "All explicitly stated operating conditions fit the lab's structured declarations."
        )
    else:
        status = "unknown"
        reasons.append(
            "Some operating conditions or scope are not covered by explicit lab declarations."
        )
    return CapabilityAssessment(status=status, reasons=reasons, checks=checks)

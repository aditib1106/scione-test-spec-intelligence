"""Evaluate an explicit rectangular dimension pair against two declared axes."""

import re
from decimal import Decimal
from fractions import Fraction

from scione.parameters import normalize_claim
from scione.parameters.units import resolve_unit
from scione.structured.models import ClaimCheck

_PAIR = re.compile(r"\s*(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|in)\s*", re.I)


def compound_dimensions_check(request, declarations, *, source):
    """A label-free rectangle may fit in either orientation; no scientific inference.

    Labels, ranges and source values remain unchanged. Missing/unknown axes and
    conflicting configurations do not produce a verdict. This handles one
    familiar compound value, not arbitrary prose containing unrelated numbers.
    """
    if request.parameter_id not in {"panel_dimensions", "specimen_dimensions"}:
        return None
    if request.normalization_state == "unresolved" or len(declarations) != 2:
        return None
    if request.kind not in {"condition", "scope"} or request.operator not in {"text", "required"}:
        return None
    if not request.text_value or not (match := _PAIR.fullmatch(request.text_value)):
        return None
    if not any(
        tuple(Decimal(found[i]) for i in (1, 2)) == tuple(Decimal(match[i]) for i in (1, 2))
        and resolve_unit(found[3]) == resolve_unit(match[3])
        for found in _PAIR.finditer(request.raw_text)
    ):
        return None
    from scione.source_text import citation_contains

    if not request.source_pages or not set(request.source_pages) <= {
        e.page for e in source.evidence if citation_contains(e.quote, request.raw_text)
    }:
        return None
    axes = {normalize_claim(d).parameter_id: d for d in declarations}
    expected = ({"panel_length", "panel_width"}, {"specimen_length", "specimen_width"})
    if set(axes) not in expected:
        return None
    if len({d.configuration for d in declarations}) != 1:
        return None
    if any(d.configuration and d.configuration != request.configuration for d in declarations):
        return None
    unit = resolve_unit(match[3])
    values = [Fraction(Decimal(match[i])) * unit.factor for i in (1, 2)]
    limits = []
    for d in declarations:
        u = resolve_unit(d.unit)
        if (
            not u
            or u.dimension != "length"
            or d.operator != "range"
            or d.normalization_state == "unresolved"
            or d.availability != "supported"
        ):
            return None
        limits.append(
            (
                Fraction(d.minimum) * u.factor if d.minimum is not None else None,
                Fraction(d.maximum) * u.factor if d.maximum is not None else None,
            )
        )

    def fits(points):
        return all(
            (low is None or low <= p) and (high is None or p <= high)
            for p, (low, high) in zip(points, limits, strict=True)
        )

    supported = fits(values) or fits(list(reversed(values)))
    return ClaimCheck(
        id=request.id,
        parameter_id=request.parameter_id,
        label=request.label,
        kind=request.kind,
        stage=request.stage,
        requested=request.display_value,
        declared="; ".join(f"{d.label}: {d.display_value}" for d in declarations),
        status="supported" if supported else "gap",
        reason="The source rectangular dimensions fit the lab's two explicit axis capacities."
        if supported
        else "The rectangle exceeds the lab's declared axis capacities in either orientation.",
        source_pages=list(request.source_pages),
        requirement_claim=request.model_copy(deep=True),
        capability_claim_ids=[d.id for d in declarations],
        comparison_basis="semantic_alignment",
        comparison_metadata=[{"rule": "rectangular_axis_capacity", "numeric_evaluation": True}],
    )

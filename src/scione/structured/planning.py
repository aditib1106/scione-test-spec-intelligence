"""Keep explicit submission/allocation quantities separate from capability limits."""

from __future__ import annotations

import re
from decimal import Decimal

from scione.numeric_literals import NUMBER, decimal_literal
from scione.structured.models import AtomicClaim

_WORDS = dict(
    zip(
        "one two three four five six seven eight nine ten".split(),
        range(1, 11),
        strict=True,
    )
)


def _text(raw: str) -> str:
    for word, value in _WORDS.items():
        raw = re.sub(rf"\b{word}\b", str(value), raw, flags=re.I)
    return " ".join(raw.strip().rstrip(".").split())


def _count(raw: str) -> Decimal | None:
    try:
        value = decimal_literal(raw)
    except ValueError:
        return None
    return value if value >= 0 and value == value.to_integral_value() else None


def planning_quantities(raw: str) -> list[dict]:
    """Read complete closed planning clauses, never partial operating prose."""
    text = _text(raw)
    if submission := re.fullmatch(
        rf"Submit (?P<count>{NUMBER}) (?P<scope>(?:independently mixed )?"
        r"(?:(?:topcoat|coating|sample|test) )?lots?)",
        text,
        re.I,
    ):
        count = _count(submission["count"])
        if count is None:
            return []
        return [
            {
                "parameter_id": "submitted_lot_count",
                "label": "Submitted lots",
                "value": count,
                "unit": "lots",
                "source_context": submission["scope"],
            }
        ]
    if not re.match(r"assign\s+", text, re.I):
        return []
    parts = re.split(r",\s*|\s+and\s+", re.sub(r"^assign\s+", "", text, flags=re.I))
    if not 2 <= len(parts) <= 8:
        return []
    result = []
    for part in parts:
        allocation = re.fullmatch(
            rf"(?P<count>{NUMBER}) to (?P<scope>[A-Za-z][A-Za-z -]{{1,60}})",
            part,
        )
        if allocation is None:
            return []
        if not re.fullmatch(
            r"baseline checks|initial checks|thermal cycling|dispute retention|"
            r"retained samples|retention samples|reserve samples|retest|storage|controls",
            allocation["scope"],
            re.I,
        ):
            return []
        count = _count(allocation["count"])
        if count is None:
            return []
        result.append(
            {
                "parameter_id": "sample_allocation",
                "label": "Sample allocation",
                "value": count,
                "unit": None,
                "source_context": allocation["scope"],
            }
        )
    return result


def is_planning_instruction(claim: AtomicClaim) -> bool:
    """Only an exact parsed planning quantity receives informational treatment."""
    return bool(
        claim.kind in {"instruction", "condition"}
        and claim.operator == "="
        and claim.normalization_state != "unresolved"
        and any(
            item["parameter_id"] == claim.parameter_id
            and item["value"] == claim.value
            and (
                item["unit"] == claim.unit
                or (item["parameter_id"] == "sample_allocation" and claim.unit in {None, "panels"})
            )
            and _planning_context(item["source_context"], claim.source_context)
            for item in planning_quantities(claim.raw_text)
        )
    )


def _planning_context(scope: str, context: str | None) -> bool:
    """A planning label may include only explicit allocation/per-lot syntax."""
    if not context:
        return False
    text = _text(context).casefold()
    wanted = _text(scope).casefold()
    if wanted not in text:
        return False
    rest = text.replace(wanted, "", 1)
    return not re.sub(r"\ballocation\b|\bper lot\b|\b(?:from|for) each lot\b|[,;\s]", "", rest)


def canonical_planning_parameter(claim: AtomicClaim) -> str | None:
    """Reclassify an exact source count, never its value, unit or quoted facts.

    A generic specimen count may describe submitted lots or a sample-plan
    allocation. Complete literal grammar and its own context establish that
    distinction; arbitrary operating counts and mixed clauses remain untouched.
    """
    if (
        claim.kind not in {"instruction", "condition"}
        or claim.operator != "="
        or claim.value is None
        or claim.parameter_id
        not in {
            "specimen_count",
            "panel_count",
            "sample_count",
            "submitted_lot_count",
            "sample_allocation",
            "lot_count",
        }
        or claim.minimum is not None
        or claim.maximum is not None
        or claim.tolerance is not None
        or claim.values
        or claim.configuration is not None
        or claim.sequence is not None
    ):
        return None
    candidates = [
        item
        for item in planning_quantities(claim.raw_text)
        if item["value"] == claim.value
        and _planning_context(item["source_context"], claim.source_context)
        and (
            item["unit"] == claim.unit
            or (item["parameter_id"] == "sample_allocation" and claim.unit in {None, "panels"})
        )
    ]
    return candidates[0]["parameter_id"] if len(candidates) == 1 else None

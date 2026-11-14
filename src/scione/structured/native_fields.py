"""A typed native atom may already represent a split scalar/unit text field."""

from __future__ import annotations

import re

from scione.numeric_literals import NUMBER
from scione.parameters.units import resolve_unit
from scione.structured.lexical_grounding import source_quantities, source_unit_name
from scione.structured.models import AtomicClaim

_SCALAR = re.compile(
    rf"\s*(?P<operator><=|>=|≤|≥|<|>)?\s*{NUMBER}"
    rf"(?:\s*(?:±\s*{NUMBER}|(?:-|to)\s*{NUMBER}))?\s*", re.I,
)


def represented_custom_scalar_field(
    raw: str, unit: str | None, kind: str, context: str | None, atoms: list[AtomicClaim]
) -> bool:
    """Deduplicate a bare field only against one already grounded native atom.

    Unknown units are literal identities, never converted or fuzzy matched.
    An explicit field boundary must agree; when the field omits its boundary,
    the existing source-checked native operator is retained. Arbitrary prose,
    partial extra quantities, wrong units and ambiguous native atoms stay out.
    """
    shape = _SCALAR.fullmatch(raw)
    if not shape or unit is None or resolve_unit(unit) is not None:
        return False
    fields = source_quantities(f"{raw} {unit}", additional_unit=unit)
    if not fields or any(row.value is None or row.ambiguity for row in fields):
        return False
    explicit = {"≤": "<=", "≥": ">="}.get(shape["operator"], shape["operator"])
    values = {row.value for row in fields}
    candidates = []
    for claim in atoms:
        if (
            claim.normalization_state == "unresolved" or claim.kind != kind
            or not claim.parameter_id or claim.unit != unit
            or (context and context != claim.source_context)
            or (explicit and explicit != claim.operator)
        ):
            continue
        payload = {value for value in (
            claim.value, claim.minimum, claim.maximum, claim.tolerance, *claim.values,
        ) if value is not None}
        if payload != values:
            continue
        source = [row for row in source_quantities(
            claim.raw_text, additional_unit=unit,
        ) if row.unit == source_unit_name(unit)]
        expressions = {row.expression_span for row in source}
        if len(expressions) == 1 and {row.value for row in source} == values:
            candidates.append(claim)
    return len(candidates) == 1

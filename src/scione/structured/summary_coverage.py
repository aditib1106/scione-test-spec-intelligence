"""Prove closed scalar summary rows repeat retained, source-owned native facts.

This deduplicates legacy parser output, never establishes laboratory support.
Every original field and native claim remains in the extraction artifact.
"""

import re
from decimal import Decimal

from scione.parameters.registry import resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.source_text import citation_contains, citation_text
from scione.structured.source_clauses import source_clauses

NUMBER = r"[+-]?\d+(?:\.\d+)?"


def _layout(text):
    return re.sub(r"[\s|:：]", "", citation_text(text)).casefold().rstrip(".")


def _owned_rows(clause, requirement):
    return [
        evidence
        for evidence in requirement.evidence
        if any(_layout(part) == _layout(clause) for part in source_clauses(evidence.quote))
    ]


def _owned_native(claim, requirement):
    return bool(
        claim.source_pages
        and set(claim.source_pages)
        <= {e.page for e in requirement.evidence if citation_contains(e.quote, claim.raw_text)}
    )


def represented_scalar_summary(clause, kind, requirement, native):
    """A strict summary grammar and one matching native scalar own all its words."""
    if kind != "criterion" or not (rows := _owned_rows(clause, requirement)):
        return None
    text = citation_text(clause).strip()
    candidates = [
        c
        for c in native
        if c.normalization_state != "unresolved"
        and c.kind == "criterion"
        and c.configuration is None
        and c.sequence is None
        and _owned_native(c, requirement)
    ]

    def exact(parameter, operator, unit, value=None, minimum=None, maximum=None, stage="general"):
        family = [
            c for c in candidates if (d := resolve_definition(c.parameter_id)) and d.id == parameter
        ]
        same = [
            c
            for c in family
            if c.stage == stage
            and c.operator == operator
            and canonical_unit_name(c.unit) == canonical_unit_name(unit)
            and c.value == value
            and c.minimum == minimum
            and c.maximum == maximum
            and c.tolerance is None
            and not c.values
        ]
        # Another native configuration/phase of this property can make the
        # short summary ambiguous. No convenient row is selected in that case.
        return same[0] if len(family) == len(same) == 1 else None

    owners = []
    rule = None
    if m := re.fullmatch(
        rf"Topcoat (?:dry[- ]?)?film thickness\s*[:|]?\s*({NUMBER})\s*[-–]\s*"
        rf"({NUMBER})\s*([µμ]m)\s+on (?:each|every) panel[.]?",
        text,
        re.I,
    ):
        c = exact("topcoat_thickness", "range", m[3], minimum=Decimal(m[1]), maximum=Decimal(m[2]))
        if (
            c
            and re.search(r"\bon (?:every|each) panel\b", c.raw_text, re.I)
            and (
                c.source_context is None
                or re.fullmatch(
                    r"(?:(?:separate )?topcoat thickness\s*;\s*)?on (?:every|each) panel",
                    c.source_context,
                    re.I,
                )
            )
        ):
            owners = [c]
            rule = "owned_topcoat_scalar_summary"
    elif m := re.fullmatch(
        rf"({NUMBER})\s*°\s+(?:reflected|specular) gloss\s*[:|]?\s*({NUMBER})\s*[-–]\s*"
        rf"({NUMBER})\s*GU\s+initial[.]?",
        text,
        re.I,
    ):
        c = exact(
            "gloss", "range", "GU", minimum=Decimal(m[2]), maximum=Decimal(m[3]), stage="initial"
        )
        angles = [
            a
            for a in native
            if a.parameter_id == "measurement_angle"
            and a.kind == "condition"
            and a.normalization_state != "unresolved"
            and a.stage == "measurement"
            and a.operator == "="
            and a.value == Decimal(m[1])
            and a.tolerance is None
            and canonical_unit_name(a.unit) == "°"
            and a.configuration is None
            and a.sequence is None
            and _owned_native(a, requirement)
        ]
        if (
            c
            and len(angles) == 1
            and re.fullmatch(
                r"arithmetic mean(?:\s*;\s*as delivered)?", c.source_context or "", re.I
            )
        ):
            owners = [c, *angles]
            rule = "owned_initial_gloss_scalar_summary"
    elif m := re.fullmatch(rf"(>=|≥)\s*({NUMBER})\s*%\s+site retention[.]?", text, re.I):
        c = exact("gloss_retention", ">=", "%", value=Decimal(m[2]), stage="after_cycle")
        if (
            c
            and re.search(r"\beach (?:marked )?(?:location|site)\b", c.raw_text, re.I)
            and any(re.search(r"\bgloss\b", e.quote, re.I) for e in rows)
            and re.fullmatch(
                r"each (?:marked )?(?:location|site)\s*;\s*of its own (?:original|baseline) value",
                c.source_context or "",
                re.I,
            )
        ):
            owners = [c]
            rule = "owned_site_retention_scalar_summary"
    if not owners:
        return None
    return {
        "rule": rule,
        "field": "duplicate_summary",
        "source_quote": clause,
        "source_pages": sorted({e.page for e in rows}),
        "retained_claim_ids": [c.id for c in owners],
        "source_values_preserved": True,
    }


def same_named_scalar_row(clause, printed):
    """Only whitespace/table delimiters may differ in an already-proved row."""
    return _layout(clause) == _layout(printed)

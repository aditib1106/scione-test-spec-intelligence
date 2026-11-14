"""Closed presentation/count-basis proofs, independent of any test family."""

import re

from scione.parameters import resolve_definition
from scione.source_text import citation_contains
from scione.structured.source_clauses import context_is_source_grounded


def _owned(claim, source):
    return bool(
        claim.source_pages
        and set(claim.source_pages)
        <= {
            evidence.page
            for evidence in source.evidence
            if citation_contains(evidence.quote, claim.raw_text)
        }
    )


def _basis(context):
    if not context:
        return None
    text = " ".join(context.casefold().split()).rstrip(".")
    match = re.fullmatch(
        r"(?:(?:from|for|on) )?(?:each|every|per) "
        r"(?P<unit>lot|batch|panel|specimen)",
        text,
    )
    return f"per {match['unit']}" if match else None


def count_basis_pair(first, second, source):
    left, right = resolve_definition(first.parameter_id), resolve_definition(second.parameter_id)
    if not (
        left
        and right
        and left.id == right.id
        and left.id in {"panel_count", "specimen_count", "measurement_locations"}
        and _owned(first, source)
        and first.source_context
        and citation_contains(first.raw_text, first.source_context)
    ):
        return first, second, []
    basis = _basis(first.source_context)
    if not basis or basis != _basis(second.source_context):
        return first, second, []
    actions = [
        {
            "field": "source_context",
            "side": side,
            "source": claim.source_context,
            "canonical": basis,
            "rule": "closed_count_basis_alias",
        }
        for side, claim in (("source", first), ("declaration", second))
        if claim.source_context != basis
    ]
    return (
        first.model_copy(update={"source_context": basis}),
        second.model_copy(update={"source_context": basis}),
        actions,
    )


def measurement_map_pair(first, second, source):
    """Preserve an explicitly declared repeatable map and the same per-panel basis."""
    definitions = [resolve_definition(c.parameter_id) for c in (first, second)]
    if not (
        all(d and d.id == "measurement_locations" for d in definitions)
        and _owned(first, source)
        and first.source_context
        and citation_contains(first.raw_text, first.source_context)
        and second.source_context
        and second.availability == "supported"
        and second.normalization_state != "unresolved"
        and not re.search(r"\b(?:not|no|without|except|unless)\b", first.raw_text, re.I)
        and re.search(r"\brepeatable\b[^.;]{0,60}\bmap\b", first.raw_text, re.I)
    ):
        return first, second, []
    scope = re.compile(
        r"(?:(?:repeatable\s+(?:(?:measurement|position)\s+)?map)\s+)?"
        r"on\s+(?:each|every)\s+panel[.]?",
        re.I,
    )
    if not all(scope.fullmatch(c.source_context.strip()) for c in (first, second)):
        return first, second, []
    actions = [
        {
            "field": "source_context",
            "side": side,
            "source": c.source_context,
            "canonical": "per panel; repeatable map",
            "rule": "source_owned_repeatable_measurement_map",
        }
        for side, c in (("source", first), ("declaration", second))
    ]
    return (
        first.model_copy(update={"source_context": "per panel; repeatable map"}),
        second.model_copy(update={"source_context": "per panel; repeatable map"}),
        actions,
    )


def initial_phase_pair(first, second, source):
    """An owned 'as delivered' decorator repeats the explicitly matched initial stage."""
    if not (
        first.stage == second.stage == "initial"
        and _owned(first, source)
        and first.source_context
        and context_is_source_grounded(first.source_context, first.raw_text, first.stage)
        and (definition := resolve_definition(first.parameter_id))
        and definition.rule == "reporting_range"
    ):
        return first, second, []
    parts = [p.strip() for p in re.split(r"[;,]", first.source_context)]
    if parts.count("as delivered") != 1:
        return first, second, []
    remaining = "; ".join(p for p in parts if p != "as delivered") or None
    if remaining != second.source_context:
        return first, second, []
    return (
        first.model_copy(update={"source_context": remaining}),
        second,
        [
            {
                "field": "source_context",
                "side": "source",
                "source": first.source_context,
                "canonical": remaining,
                "rule": "owned_initial_phase_decorator",
            }
        ],
    )


_PURPOSE = {
    "customer evidence package",
    "reporting evidence",
    "reporting evidence for release review",
    "for release review",
}
_CONDITIONAL = re.compile(r"\b(?:not|no|except|unless|if|only|without)\b|\d", re.I)


def report_directive_pair(first, second, source):
    """The exact same positive reporting body can omit an imperative/purpose.

    Every requested output word remains in the literal equality. This cannot
    compare a specimen criterion to a reporting service, infer a missing output,
    or translate an unknown phrase into a claimed service.
    """
    reporting = first.kind == second.kind == "reporting"
    operational = first.kind == second.kind and first.kind in {"condition", "scope"}
    if not (
        (reporting or operational)
        and first.operator in {"text", "required"}
        and second.operator in {"text", "required"}
        and first.text_value
        and second.text_value
        and _owned(first, source)
        and all(
            value is None
            for value in (
                first.value,
                first.minimum,
                first.maximum,
                first.tolerance,
                second.value,
                second.minimum,
                second.maximum,
                second.tolerance,
            )
        )
        and not first.values
        and not second.values
        and not _CONDITIONAL.search(first.text_value + " " + second.text_value)
    ):
        return first, second, []
    contexts = [
        " ".join((claim.source_context or "").casefold().split()).rstrip(".")
        for claim in (first, second)
    ]
    if any(context and (not reporting or context not in _PURPOSE) for context in contexts):
        return first, second, []

    def body(value):
        value = " ".join(value.split()).rstrip(".;")
        prefix = (
            r"^(?:Report|Record|Provide)\s+"
            if reporting
            else r"^(?:We\s+(?:want|require)\s+(?:(?:a|an|the)\s+)?|Please\s+(?:use|perform)\s+)"
        )
        return re.sub(prefix, "", value, count=1, flags=re.I)

    if body(first.text_value) != body(second.text_value):
        return first, second, []
    actions = []
    for side, claim in (("source", first), ("declaration", second)):
        if claim.source_context:
            actions.append(
                {
                    "field": "source_context",
                    "side": side,
                    "source": claim.source_context,
                    "canonical": None,
                    "rule": "literal_reporting_body_and_purpose",
                }
            )
    if second.text_value != first.text_value:
        actions.append(
            {
                "field": "text_value",
                "side": "declaration",
                "source": second.text_value,
                "canonical": first.text_value,
                "rule": "literal_reporting_body_and_purpose",
            }
        )
    return (
        first.model_copy(update={"source_context": None}),
        second.model_copy(update={"source_context": None, "text_value": first.text_value}),
        actions,
    )

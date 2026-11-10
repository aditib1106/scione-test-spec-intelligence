"""Evidence that two numeric atoms refer to one literal source occurrence.

This module does not approve model stage/context metadata, remove residual
restrictions, infer parameter meaning, convert units or make capability decisions.
A proof allows a caller to retain one quantity with any necessary metadata review
rather than add another weaker numeric copy of exactly that source occurrence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scione.parameters.registry import normalize_stage, resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.source_text import (
    canonicalize_citation_apostrophes,
    citation_contains,
    citation_text,
)
from scione.structured.lexical_grounding import SourceQuantity, source_quantities
from scione.structured.models import AtomicClaim


@dataclass(frozen=True)
class NumericOccurrenceProof:
    """Offsets are zero-based, end-exclusive in the original ``source_text``.

    ``metadata_flags`` are deliberately separate from quantity identity. Callers
    must not turn this source-occurrence proof into approval of unproved metadata
    or use the quote span as a mask that erases neighboring source restrictions.
    """

    fallback_id: str
    supplied_id: str
    source_pages: tuple[int, ...]
    fallback_quote_span: tuple[int, int]
    supplied_quote_span: tuple[int, int]
    expression_span: tuple[int, int]
    number_spans: tuple[tuple[int, int], ...]
    unit_span: tuple[int, int]
    canonical_unit: str
    metadata_flags: tuple[str, ...]

    @property
    def requires_metadata_review(self) -> bool:
        return bool(self.metadata_flags)


def _canonical_source_with_offsets(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Map citation v2 normalization to original source character offsets.

    French apostrophe canonicalization is length-preserving. Whitespace still
    uses its existing explicit spans, including conservative CJK wrapping joins.
    """
    pieces: list[str] = []
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for gap in re.finditer(r"\s+", text):
        for index in range(cursor, gap.start()):
            pieces.append(text[index])
            offsets.append((index, index + 1))
        if gap.start() and gap.end() < len(text):
            # Reuse the citation contract: only whitespace adjacent to CJK is
            # joined. Digits, signs and Latin identifier boundaries survive.
            local = text[gap.start() - 1 : gap.end() + 1]
            if citation_text(local) == text[gap.start() - 1] + " " + text[gap.end()]:
                pieces.append(" ")
                offsets.append(gap.span())
        cursor = gap.end()
    for index in range(cursor, len(text)):
        pieces.append(text[index])
        offsets.append((index, index + 1))
    return canonicalize_citation_apostrophes("".join(pieces)), offsets


def _unique_quote_span(source: str, quote: str) -> tuple[int, int] | None:
    canonical, offsets = _canonical_source_with_offsets(source)
    needle = citation_text(quote)
    if not needle or not citation_contains(source, quote):
        return None
    spans = []
    for found in re.finditer(re.escape(needle), canonical):
        start, end = found.span()
        # The citation helper preserves Latin/scientific token boundaries.
        if citation_contains(canonical[max(0, start - 2) : end + 2], needle):
            spans.append((offsets[start][0], offsets[end - 1][1]))
    return spans[0] if len(spans) == 1 else None


def _payload(claim: AtomicClaim) -> tuple:
    return (
        claim.kind,
        claim.operator,
        claim.value,
        claim.minimum,
        claim.maximum,
        claim.tolerance,
        tuple(claim.values),
        canonical_unit_name(claim.unit),
        claim.availability,
    )


def _matches_expression(claim: AtomicClaim, quantities: list[SourceQuantity]) -> bool:
    if not quantities or any(
        row.value is None or row.ambiguity or row.operator_ambiguity for row in quantities
    ):
        return False
    if any(row.unit != canonical_unit_name(claim.unit) for row in quantities):
        return False
    if claim.operator == "range":
        return (
            claim.minimum is not None
            and claim.maximum is not None
            and claim.value is None
            and claim.tolerance is None
            and len(quantities) == 2
            and all(row.role == "range_endpoint" for row in quantities)
            and [row.value for row in quantities] == [claim.minimum, claim.maximum]
        )
    if claim.operator not in {"=", "<=", ">=", "<", ">"} or claim.value is None:
        return False
    values = [row for row in quantities if row.role == "value"]
    tolerances = [row for row in quantities if row.role == "tolerance"]
    if len(values) != 1 or len(values) + len(tolerances) != len(quantities):
        return False
    if values[0].value != claim.value:
        return False
    if values[0].operator not in ({"=", None} if claim.operator == "=" else {claim.operator}):
        return False
    if claim.tolerance is None:
        return not tolerances
    return len(tolerances) == 1 and tolerances[0].value == claim.tolerance and claim.operator == "="


def _metadata_flags(
    fallback: AtomicClaim, supplied: AtomicClaim, source: str, quote_span: tuple[int, int]
) -> tuple[str, ...]:
    """Expose metadata questions without resolving them by model paraphrase."""
    flags: set[str] = set()
    local = source[slice(*quote_span)]
    stages = [normalize_stage(claim.stage) for claim in (fallback, supplied)]
    if stages[0] != stages[1]:
        flags.add("stage_differs")
    if stages[1] != "general" and not citation_contains(
        local.casefold(), stages[1].replace("_", " ").casefold()
    ):
        flags.add("supplied_stage_not_literal")
    for field in ("source_context", "configuration"):
        value = getattr(supplied, field)
        previous = getattr(fallback, field)
        if previous and previous != value:
            flags.add(f"{field}_differs")
        if value and not citation_contains(local, value):
            flags.add(f"supplied_{field}_not_literal")
    if fallback.sequence is not None and fallback.sequence != supplied.sequence:
        flags.add("sequence_differs")
    if supplied.sequence is not None and not re.search(
        rf"\bstep\s*{supplied.sequence}(?!\d)", local, re.I
    ):
        flags.add("supplied_sequence_not_literal")
    return tuple(sorted(flags))


def prove_same_numeric_occurrence(
    fallback: AtomicClaim,
    supplied: AtomicClaim,
    *,
    source_text: str,
    source_pages: list[int] | tuple[int, ...],
) -> NumericOccurrenceProof | None:
    """Prove one same-unit, same-role, same-payload literal numeric occurrence.

    Both inputs must already be accepted numeric atoms. Each quotation and its
    matching quantity expression must locate uniquely inside this source clause.
    A broad quote containing two identical values is ambiguous: a caller cannot
    assign one duplicate fallback to the desired occurrence without source offsets.
    Stronger stage, sequence, configuration or context are flagged separately.
    Curated parameter aliases may identify the same quantity. Unfamiliar custom
    identities cannot replace a known parser parameter merely because their
    numbers agree. No neighboring restrictions are consumed.
    """
    pages = tuple(sorted(set(source_pages)))
    first_definition = resolve_definition(fallback.parameter_id)
    second_definition = resolve_definition(supplied.parameter_id)
    if (
        not pages
        or any(page < 1 for page in pages)
        or not set(pages) <= set(fallback.source_pages)
        or not set(pages) <= set(supplied.source_pages)
        or fallback.normalization_state == "unresolved"
        or supplied.normalization_state == "unresolved"
        or not fallback.parameter_id
        or not supplied.parameter_id
        or (
            fallback.parameter_id != supplied.parameter_id
            and not (
                first_definition is not None
                and second_definition is not None
                and first_definition.id == second_definition.id
            )
        )
        or not fallback.unit
        or not supplied.unit
        or fallback.availability != "supported"
        or supplied.availability != "supported"
        or _payload(fallback) != _payload(supplied)
        or fallback.text_value is not None
        or supplied.text_value is not None
        or fallback.values
        or supplied.values
    ):
        return None
    fallback_span = _unique_quote_span(source_text, fallback.raw_text)
    supplied_span = _unique_quote_span(source_text, supplied.raw_text)
    if fallback_span is None or supplied_span is None:
        return None
    expressions: dict[tuple[int, int], list[SourceQuantity]] = {}
    for row in source_quantities(source_text, context=source_text, additional_unit=supplied.unit):
        expressions.setdefault(row.expression_span, []).append(row)
    locations = []
    for quote_span in (fallback_span, supplied_span):
        matching = [
            (span, rows)
            for span, rows in expressions.items()
            if quote_span[0] <= span[0]
            and span[1] <= quote_span[1]
            and _matches_expression(supplied, rows)
        ]
        if len(matching) != 1:
            return None
        locations.append(matching[0])
    if locations[0][0] != locations[1][0]:
        return None
    span, rows = locations[0]
    return NumericOccurrenceProof(
        fallback_id=fallback.id,
        supplied_id=supplied.id,
        source_pages=pages,
        fallback_quote_span=fallback_span,
        supplied_quote_span=supplied_span,
        expression_span=span,
        number_spans=tuple(row.number_span for row in rows),
        unit_span=rows[0].unit_span,
        canonical_unit=rows[0].unit,
        metadata_flags=_metadata_flags(fallback, supplied, source_text, supplied_span),
    )

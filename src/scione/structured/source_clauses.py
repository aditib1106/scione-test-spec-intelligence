"""Source-local clause boundaries; never assign a stage across separate sentences."""

from __future__ import annotations

import hashlib
import re

from scione.parameters.registry import normalize_stage, resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.source_text import citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import source_quantities
from scione.structured.models import AtomicClaim

_ABBREVIATION = re.compile(
    r"\b(?:e\.g|i\.e|cf|vs|dr|mr|mrs|ms|no|fig|eq|sect|rev|vol|pp?)\.$", re.I
)
_REFERENTIAL_START = re.compile(
    r"(?:the\s+(?:latter|former|same)|it\b|these\b|those\b|this\b)", re.I
)


def source_clauses(raw: str) -> list[str]:
    """Split explicit sentences while retaining local anaphoric continuations.

    Values, abbreviations and quoted text remain verbatim. A continuation such
    as "The latter must ..." keeps its preceding source sentence, since a
    parser must not guess the antecedent after severing it. This is source
    segmentation, not an assertion that every clause is independently usable.
    """
    chunks = re.split(
        r"[;；\n]+|,\s*(?=(?:with\s+a\s+)?(?:\d+[- ]minute\s+)?"
        r"room.temperature\s+recovery)",
        raw,
        flags=re.I,
    )
    result = []
    for chunk in chunks:
        start = 0
        for boundary in re.finditer(r"[。！？]|(?<=[.!?])\s+(?=[A-Z\u3400-\u9fff])", chunk):
            # CJK marks are delimiters themselves; Latin marks precede the
            # whitespace delimiter. In either case preserve their source text.
            end = boundary.end() if boundary[0] in "。！？" else boundary.start()
            preceding = chunk[start:end]
            following = chunk[boundary.end():].lstrip()
            if _ABBREVIATION.search(preceding.rstrip()) or _REFERENTIAL_START.match(following):
                continue
            if preceding.strip():
                result.append(preceding.strip())
            start = boundary.end()
        if chunk[start:].strip():
            result.append(chunk[start:].strip())
    return result


def context_word_spans(phrase: str, source: str) -> list[tuple[int, int]]:
    """Locate an ordered context projection; never consume intervening words.

    "Arithmetic mean, as delivered" may be recorded around a numeric range.
    This identifies its own words only. A negative, material, extra number or
    other qualifier between them is left outside the returned spans.
    """
    words = list(re.finditer(r"\w+(?:[-']\w+)*", phrase))
    if not words or len(words) > 16:
        return []
    cursor = 0
    spans = []
    for word in words:
        matches = list(re.finditer(
            rf"(?<!\w){re.escape(word[0])}(?!\w)", source[cursor:], re.I,
        ))
        if len(matches) != 1:
            return []
        match = matches[0]
        spans.append((cursor + match.start(), cursor + match.end()))
        cursor += match.end()
    return spans


def context_is_source_grounded(phrase: str, source: str, stage: str) -> bool:
    """Recognize literal/ordered context words and one explicit cure relation.

    This proves source ownership for deduplication, not lab equivalence. The
    claim's original scope remains intact for comparison and audit.
    """
    phrase = citation_text(phrase).casefold()
    source = citation_text(source).casefold()
    if not phrase:
        return True
    if context_word_spans(phrase, source):
        return True
    cure = bool(
        stage == "curing"
        and phrase == "cure reference at actual metal temperature"
        and re.search(r"\bactual metal temperature\b", source)
        and re.search(r"\bcure reference\b", source)
        and not re.search(r"\b(?:not|no)\s+(?:the\s+)?cure reference\b", source)
    )
    retention = re.fullmatch(
        r"each (location|site|panel) relative to its own (original|baseline) value", phrase
    )
    return cure or bool(
        stage == "after_cycle" and retention
        and re.search(rf"\beach {retention[1]}\b.*\bretain\b", source)
        and re.search(rf"\bof its own {retention[2]} value\b", source)
    )


def context_owned_word_spans(phrase: str, source: str, stage: str) -> list[tuple[int, int]]:
    """Mask only a proven context's unique words, never its whole source quote."""
    located = context_word_spans(phrase, source)
    if located or not context_is_source_grounded(phrase, source, stage):
        return located
    # The explicit cure/retention relationships above can reorder nouns or
    # express 'relative to' with 'of'. Preserve every other source word.
    spans = []
    for word in set(re.findall(r"\w+", phrase)):
        matches = list(re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", source, re.I))
        if len(matches) == 1:
            spans.append(matches[0].span())
    return spans


def ordered_thermal_cycle_proof(claim: AtomicClaim, source: CustomerRequirement) -> dict | None:
    if (
        normalize_stage(claim.stage) not in {"exposure", "thermal_cycle"}
        or claim.normalization_state == "unresolved"
        or not (definition := resolve_definition(claim.parameter_id))
        or definition.id not in {"temperature", "duration", "cycles"}
        or (definition.id != "cycles" and claim.sequence is None)
    ):
        return None
    needle = citation_text(claim.raw_text)
    for index, evidence in enumerate(source.evidence, 1):
        if evidence.page not in claim.source_pages:
            continue
        text = citation_text(evidence.quote)
        found = list(re.finditer(re.escape(needle), text))
        if len(found) != 1:
            continue
        start, end = found[0].span()
        boundaries = [
            match.end() for match in re.finditer(r"[;。!?]|(?<!\d)\.(?!\d)", text[:start])
        ]
        left = max([0, *boundaries])
        following = re.search(r"[;。!?]|(?<!\d)\.(?!\d)", text[end:])
        right = end + following.start() if following else len(text)
        clause = text[left:right]
        if not re.search(r"\bcycles?\s+between\b|\bthermal[- ]cycl", clause, re.I) or re.search(
            r"\bexcept\b|\bunless\b|\bnot\s+applic|除外|不适用", clause, re.I
        ):
            continue
        temperatures = [
            item
            for item in source_quantities(clause, context=clause)
            if item.unit in {"°C", "°F"}
            and item.role == "value"
            and item.value is not None
            and item.ambiguity is None
        ]
        if definition.id == "cycles":
            counts = [
                item for item in source_quantities(clause, context=clause)
                if item.unit == "cycles" and item.value == claim.value
                and item.ambiguity is None and item.role == "value"
                and start - left <= item.expression_span[0]
                and item.expression_span[1] <= end - left
            ]
            if len(temperatures) == 2 and len(counts) == 1 and claim.sequence is None:
                return {
                    "source_page": evidence.page, "evidence_index": index,
                    "source_quote_sha256": hashlib.sha256(evidence.quote.encode()).hexdigest(),
                    "verified_cycle_count": str(claim.value),
                }
            continue
        inside = [
            position
            for position, item in enumerate(temperatures, 1)
            if start - left <= item.expression_span[0] and item.expression_span[1] <= end - left
        ]
        selected = claim.sequence
        if len(temperatures) != 2 or selected not in {1, 2}:
            continue
        if inside != [selected]:
            # A complete two-step quotation can prove its own ordering. Bind
            # the supplied scalar to the selected step's literal temperature,
            # or to the first duration immediately following that temperature.
            # Never use a repeated bare-number quote or guess a hidden step.
            if inside != [1, 2] or claim.operator != "=" or claim.tolerance is not None:
                continue
            owned = temperatures[selected - 1]
            if definition.id == "duration":
                next_start = temperatures[1].expression_span[0] if selected == 1 else len(clause)
                durations = [item for item in source_quantities(clause, context=clause)
                             if item.unit in {"h", "min", "s", "days"}
                             and item.role == "value" and item.ambiguity is None
                             and owned.expression_span[1] <= item.expression_span[0] < next_start]
                if not durations:
                    continue
                owned = durations[0]
            if (owned.value != claim.value
                    or canonical_unit_name(owned.unit) != canonical_unit_name(claim.unit)):
                continue
        if inside == [selected] or inside == [1, 2]:
            current = temperatures[selected - 1].value
            other = temperatures[2 - selected].value
            return {
                "source_page": evidence.page,
                "evidence_index": index,
                "source_quote_sha256": hashlib.sha256(evidence.quote.encode()).hexdigest(),
                "verified_sequence": claim.sequence,
                "relative_phase": "cold" if current < other else "hot" if current > other else None,
            }
    return None


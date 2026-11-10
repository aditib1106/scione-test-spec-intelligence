"""Recover omitted literal citations for existing, locally owned source atoms.

This adds provenance only. It cannot verify parameter identity, numeric parsing,
scope, stage or lab support. Ambiguous ownership and detached fragments remain
unchanged for review. Source offsets are zero-based, end-exclusive on one page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from scione.schemas import IngestedDocument
from scione.specifications.models import (
    CustomerAtomGroundingAction,
    CustomerExtraction,
    CustomerRequirement,
    RequirementEvidence,
)

MAX_ANCHOR_GAP = 768
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_BARE_CODE = re.compile(r"[A-Za-z0-9_+./:#@-]+")
_UNINFORMATIVE = frozenset(
    {
        "c",
        "f",
        "k",
        "g",
        "kg",
        "mg",
        "n",
        "kn",
        "pa",
        "kpa",
        "mpa",
        "bar",
        "m",
        "mm",
        "cm",
        "nm",
        "µm",
        "μm",
        "in",
        "s",
        "h",
        "min",
        "gu",
        "rh",
        "second",
        "seconds",
        "minute",
        "minutes",
        "hour",
        "hours",
        "day",
        "days",
        "celsius",
        "fahrenheit",
        "kelvin",
        "degree",
        "degrees",
        "at",
        "for",
        "to",
        "and",
        "or",
        "the",
        "than",
        "less",
        "more",
        "greater",
        "equal",
        "not",
        "must",
        "required",
        "exactly",
        "maximum",
        "minimum",
        "none",
        "same",
        "pass",
        "秒",
        "分钟",
        "小时",
        "摄氏度",
        "华氏度",
        "毫米",
        "厘米",
        "微米",
    }
)
_TEST_HEADING = re.compile(
    r"(?m)^\s*(?:Test|TEST|测试|试验|Prüfung)\s*[:：]?\s+[A-Z0-9][\w-]{0,30}\s*$"
)


@dataclass(frozen=True)
class _Span:
    page: int
    start: int
    end: int


def _matches(text: str, fragment: str) -> list[tuple[int, int]]:
    """Exact case/symbols with wrapping whitespace as the sole flexibility."""
    words = fragment.split()
    if not words:
        return []
    first = r"(?<![A-Za-z0-9_])" if words[0][0].isascii() and words[0][0].isalnum() else ""
    last = r"(?![A-Za-z0-9_])" if words[-1][-1].isascii() and words[-1][-1].isalnum() else ""
    expression = re.compile(
        r"(?=(" + first + r"\s+".join(re.escape(word) for word in words) + last + r"))"
    )
    matches = [(match.start(1), match.end(1)) for match in expression.finditer(text)]
    if matches or not re.search(r"[\u3400-\u9fff]", fragment):
        return matches
    # A Chinese PDF may wrap inside one word while the extracted atom joins
    # those characters, or vice versa. Reuse the citation contract's exact
    # canonical character mapping; only a unique located span is recoverable.
    # No fuzzy word replacement, scientific normalization or guessed join.
    from scione.structured.source_occurrences import _unique_quote_span

    span = _unique_quote_span(text, fragment)
    return [span] if span is not None else []


def _document_matches(pages: dict[int, str], fragment: str) -> list[_Span]:
    return [
        _Span(page, start, end)
        for page, text in pages.items()
        for start, end in _matches(text, fragment)
    ]


def _raw_fields(requirement: CustomerRequirement) -> list[str]:
    return [
        *(value.raw_value for value in requirement.conditions),
        *(value.raw_value for value in requirement.acceptance_criteria),
        *requirement.applicability,
        *requirement.special_instructions,
    ]


def _meaningful(fragment: str) -> bool:
    compact = "".join(fragment.split())
    if len(compact) < 5:
        return False
    # An unlabelled reading or identifier cannot prove which property it
    # describes, even if it occurs only once in the document.
    if _BARE_CODE.fullmatch(fragment.strip()) and (
        any(character.isdigit() for character in fragment) or fragment.isupper()
    ):
        return False
    return any(
        len(word) >= 2 and word.casefold() not in _UNINFORMATIVE for word in _WORD.findall(fragment)
    )


def _overlap(first: _Span, second: _Span) -> bool:
    return first.page == second.page and first.start < second.end and second.start < first.end


def _valid_quotes(requirement: CustomerRequirement, pages: dict[int, str]) -> list[_Span]:
    quotes = []
    for evidence in requirement.evidence:
        if evidence.page not in pages:
            continue
        found = _matches(pages[evidence.page], evidence.quote)
        if len(found) == 1:
            quotes.append(_Span(evidence.page, *found[0]))
    return quotes


def _identity_values(requirement: CustomerRequirement) -> list[str]:
    return [
        requirement.original_test_name,
        *(value.code for value in requirement.requested_methods),
    ]


def _identity_is_anchored(
    requirement: CustomerRequirement,
    requirements: list[CustomerRequirement],
    pages: dict[int, str],
    quotes: list[_Span],
) -> bool:
    others = {
        " ".join(value.split())
        for other in requirements
        if other.id != requirement.id
        for value in _identity_values(other)
    }
    for value in _identity_values(requirement):
        if len("".join(value.split())) < 5 or " ".join(value.split()) in others:
            continue
        matches = _document_matches(pages, value)
        if len(matches) == 1 and any(
            quote.page == matches[0].page
            and quote.start <= matches[0].start
            and matches[0].end <= quote.end
            for quote in quotes
        ):
            return True
    return False


def _locally_owned(
    span: _Span,
    requirement: CustomerRequirement,
    quotes: list[_Span],
    foreign: list[_Span],
    pages: dict[int, str],
) -> bool:
    """Require nearby same-parent evidence without crossing another test."""
    for quote in quotes:
        if quote.page != span.page:
            continue
        gap = max(0, quote.start - span.end, span.start - quote.end)
        if gap > MAX_ANCHOR_GAP:
            continue
        corridor = _Span(span.page, min(span.start, quote.start), max(span.end, quote.end))
        if any(_overlap(corridor, other) for other in foreign):
            continue
        boundaries = [
            _Span(span.page, match.start(), match.end())
            for match in _TEST_HEADING.finditer(pages[span.page])
            if " ".join(match.group().split()) != " ".join(requirement.original_test_name.split())
        ]
        if not any(_overlap(corridor, heading) for heading in boundaries):
            return True
    return False


def enrich_atom_citations(
    extraction: CustomerExtraction, document: IngestedDocument
) -> tuple[CustomerExtraction, list[CustomerAtomGroundingAction]]:
    """Add exact citations to already emitted atoms without changing their facts.

    Ownership needs a unique parent test-name or requested-method anchor in
    valid existing evidence; each candidate page also needs valid parent
    evidence nearby. Preparation may precede a method section, so local evidence
    can appear on either side. Other parents' source identities, quotes and raw
    fields prevent borrowing a neighboring test's fact. A source-only citation
    is not independent confirmation that the model interpreted it correctly.
    """
    enriched = extraction.model_copy(deep=True)
    pages = {page.page_number: page.text for page in document.pages}
    original_quotes = {row.id: _valid_quotes(row, pages) for row in extraction.requirements}
    actions: dict[tuple[str, int, int, int], CustomerAtomGroundingAction] = {}
    for requirement in enriched.requirements:
        quotes = original_quotes[requirement.id]
        if not _identity_is_anchored(requirement, extraction.requirements, pages, quotes):
            continue
        fields = _raw_fields(requirement)
        others = [row for row in extraction.requirements if row.id != requirement.id]
        foreign = [
            *[span for other in others for span in original_quotes[other.id]],
            *[
                span
                for other in others
                for value in _identity_values(other)
                for span in _document_matches(pages, value)
            ],
        ]
        for claim in requirement.atomic_claims:
            raw = claim.raw_text
            if not _meaningful(raw) or not any(_matches(value, raw) for value in fields):
                continue
            found = _document_matches(pages, raw)
            if len(found) != 1:
                continue
            span = found[0]
            if re.search(r"\r?\n\s*\r?\n", pages[span.page][span.start : span.end]):
                # Wrapping one phrase is different from joining detached
                # paragraphs or table labels to a later reading.
                continue
            if span.page not in claim.source_pages or span.page not in {
                quote.page for quote in quotes
            }:
                continue
            if any(_matches(value, raw) for other in others for value in _raw_fields(other)):
                continue
            key = (requirement.id, span.page, span.start, span.end)
            if key in actions:
                if claim.id not in actions[key].claim_ids:
                    actions[key].claim_ids.append(claim.id)
                continue
            if any(
                quote.page == span.page and quote.start <= span.start and span.end <= quote.end
                for quote in quotes
            ):
                continue
            if not _locally_owned(span, requirement, quotes, foreign, pages):
                continue
            citation = pages[span.page][span.start : span.end]
            try:
                evidence = RequirementEvidence(page=span.page, quote=citation)
                action = CustomerAtomGroundingAction(
                    requirement_id=requirement.id,
                    claim_ids=[claim.id],
                    page=span.page,
                    citation=citation,
                    source_start=span.start,
                    source_end=span.end,
                )
            except ValidationError:
                # Never truncate a literal span to fit the evidence contract.
                continue
            requirement.evidence.append(evidence)
            actions[key] = action
    return enriched, list(actions.values())

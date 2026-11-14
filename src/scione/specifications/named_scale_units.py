"""Recover an explicitly printed named scale, never a remembered unit.

The first supported case is a colour-difference limit whose body omits the
metric but whose unambiguous same-page summary prints Delta-E-ab.  Other units,
metrics, multiple setups and competing owners deliberately stay unresolved.
Original atoms are never edited: enrichment adds literal evidence, and a
normalizer may use the separately returned proof on a comparison/review copy.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from hashlib import sha256

from scione.numeric_literals import NUMBER, decimal_literal, source_numbers
from scione.parameters.registry import resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains, citation_text
from scione.specifications.models import (
    CustomerAtomGroundingAction,
    CustomerExtraction,
    CustomerRequirement,
    CustomerUnitCitationAction,
    RequirementEvidence,
)
from scione.structured.models import AtomicClaim

_IDENTITY = r"(?:\bcolou?r\s+(?:difference|drift)|色差)"
_SUMMARY_ROW = re.compile(
    rf"(?:master[ -]panel\s+)?{_IDENTITY}\s*(?:[|:：]\s*)?"
    rf"(?P<unit>Δ\s*E\s*\*?\s*ab)\s*"
    rf"(?P<operator><=|>=|≤|≥|<|>|=)\s*(?P<value>{NUMBER})\s*[.]?",
    re.I,
)
_BODY_BOUND = re.compile(
    rf"(?P<operator><=|>=|≤|≥|<|>|=|"
    r"(?:may|must|shall|should)\s+not\s+exceed|not\s+exceed(?:ing)?|"
    r"at\s+most|at\s+least|no\s+less\s+than|no\s+greater\s+than|"
    r"不大于|不得超过|不超过|不小于|不得低于|至少)\s*"
    rf"(?P<value>{NUMBER})",
    re.I,
)
_UNSUPPORTED_CONTEXT = re.compile(
    r"\b(?:except|unless|instead|rather|rejection|reject|review|trigger|"
    r"initial|aged|aging|after|before|during|conditional|alternative|optional|if|"
    r"not\s+(?:true|applicable|correct|valid))\b|"
    r"\bnot\s+(?:not|required|requested)\b|"
    r"\bdo\s+not\s+(?:apply|use|measure)\b|"
    r"\b(?:not|no)\s+(?:(?:a|the|this)\s+)?colou?r\s+difference\b|"
    r"CIE\s*DE\s*2000|CIE\s*94|Δ\s*E\s*(?:00|94)|"
    r"除非|除外|例外|初始|老化|之后|之前|触发|拒收",
    re.I,
)


def _layout_glyph_view(text: str) -> tuple[str, list[int]]:
    """Expand Latin typography ligatures with a physical-character offset map.

    Full NFKC would also rewrite superscript digits and scientific identifiers.
    Restrict compatibility expansion to the seven Latin presentation ligatures
    emitted by common PDF fonts. Digits, signs, symbols and case stay literal.
    """
    characters = []
    positions = []
    for index, character in enumerate(text):
        normalized = (
            unicodedata.normalize("NFKC", character)
            if 0xFB00 <= ord(character) <= 0xFB06
            else character
        )
        characters.extend(normalized)
        positions.extend([index] * len(normalized))
    return "".join(characters), positions


def _operator(value: str) -> str:
    value = " ".join(value.casefold().split())
    if value in {"≤", "不大于", "不得超过", "不超过", "at most", "no greater than"}:
        return "<="
    if value in {"≥", "不小于", "不得低于", "至少", "at least", "no less than"}:
        return ">="
    if "exceed" in value:
        return "<="
    return value


def _colour_criterion(claim: AtomicClaim) -> bool:
    definition = resolve_definition(claim.parameter_id)
    return bool(definition and definition.id == "color_difference" and claim.kind == "criterion")


def _eligible_body(claim: AtomicClaim, requirement: CustomerRequirement) -> bool:
    label = resolve_definition(claim.label)
    if not (
        _colour_criterion(claim)
        and label
        and label.id == "color_difference"
        and (claim.unit is None or canonical_unit_name(claim.unit) == "ΔE*ab")
        and claim.value is not None
        and claim.operator in {"=", "<=", ">=", "<", ">"}
        and claim.tolerance is None
        and not claim.values
        and claim.minimum is None
        and claim.maximum is None
        and claim.stage in {"general", "measurement"}
        and claim.configuration is None
        and claim.sequence is None
        and claim.availability == "supported"
        and claim.normalization_state != "unresolved"
        and len(set(claim.source_pages)) == 1
        and re.search(_IDENTITY, _layout_glyph_view(claim.raw_text)[0], re.I)
        and not _UNSUPPORTED_CONTEXT.search(" ".join((claim.raw_text, claim.source_context or "")))
    ):
        return False
    raw = citation_text(_layout_glyph_view(claim.raw_text)[0])
    if source_numbers(raw) != {claim.value}:
        return False
    bounds = {
        (_operator(match["operator"]), decimal_literal(match["value"]))
        for match in _BODY_BOUND.finditer(raw)
    }
    if bounds != {(claim.operator, claim.value)}:
        return False
    # The summary cannot distinguish several phases/configurations in one row.
    if sum(_colour_criterion(item) for item in requirement.atomic_claims) != 1:
        return False
    return True


def _parse_row(text: str) -> tuple[str, Decimal] | None:
    found = _SUMMARY_ROW.fullmatch(_layout_glyph_view(text)[0].strip())
    return (_operator(found["operator"]), decimal_literal(found["value"])) if found else None


def _literal_spans(text: str, fragment: str) -> list[tuple[int, int]]:
    literal, positions = _layout_glyph_view(text)
    words = _layout_glyph_view(fragment)[0].split()
    first = r"(?<![A-Za-z0-9_])" if words[0][0].isascii() and words[0][0].isalnum() else ""
    last = r"(?![A-Za-z0-9_])" if words[-1][-1].isascii() and words[-1][-1].isalnum() else ""
    expression = re.compile(
        r"(?=(" + first + r"\s+".join(re.escape(part) for part in words) + last + r"))"
    )
    spans = []
    for match in expression.finditer(literal):
        start, end = match.span(1)
        if (start and positions[start] == positions[start - 1]) or (
            end < len(positions) and positions[end - 1] == positions[end]
        ):
            continue  # A quote cannot begin/end inside one expanded glyph.
        spans.append((positions[start], positions[end - 1] + 1))
    return spans


def _physical_parent_covers(
    physical: str, page: int, body: tuple[int, int], requirement: CustomerRequirement
) -> bool:
    """Every body character is covered by owned physical quotes or wrapping space.

    A model may split one sentence into two evidence entries. This accepts their
    exact physical union, not a guessed join across intervening words or rows.
    """
    spans = []
    for evidence in requirement.evidence:
        if evidence.page != page:
            continue
        found = _literal_spans(physical, evidence.quote)
        if len(found) != 1:
            continue
        start, end = found[0]
        if start < body[1] and end > body[0]:
            spans.append((max(start, body[0]), min(end, body[1])))
    cursor = body[0]
    for start, end in sorted(spans):
        if start > cursor and physical[cursor:start].strip():
            return False
        cursor = max(cursor, end)
    return cursor >= body[1]


def physical_named_scale_unit_evidence(
    claim: AtomicClaim,
    requirement: CustomerRequirement,
    document: IngestedDocument,
    *,
    requirements: list[CustomerRequirement] | None = None,
) -> dict | None:
    """Recheck a proposed unit cue against the actual complete physical source.

    API callers can pass every effective requirement to prevent a human-added
    competing owner from turning a unitless field into an automatic unit guess.
    A literal table quote alone is insufficient: the body, physical parent,
    scalar/operator, named scale and page must all agree uniquely.
    """
    if not _eligible_body(claim, requirement):
        return None
    page = claim.source_pages[0]
    pages = {item.page_number: item.text for item in document.pages}
    physical = pages.get(page)
    if physical is None:
        return None
    owners = {
        row.id
        for row in (requirements if requirements is not None else [requirement])
        if any(_colour_criterion(atom) and page in atom.source_pages for atom in row.atomic_claims)
    }
    if owners != {requirement.id}:
        return None
    body = _literal_spans(physical, claim.raw_text)
    if len(body) != 1:
        return None
    if not _physical_parent_covers(physical, page, body[0], requirement):
        return None
    matches = []
    named_rows = 0
    offset = 0
    for line in physical.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if re.search(_IDENTITY, _layout_glyph_view(content)[0], re.I) and re.search(
            r"Δ\s*E|delta\s*e|CIE\s*DE|CIE\s*94", content, re.I
        ):
            named_rows += 1
        if _parse_row(content) == (claim.operator, claim.value):
            citation = content.strip()
            start = offset + len(content) - len(content.lstrip())
            matches.append((citation, start, start + len(citation)))
        offset += len(line)
    if len(matches) != 1 or named_rows != 1:
        return None
    citation, start, end = matches[0]
    return {
        "rule": "source_named_scale_summary_unit",
        "policy_version": "source_named_scale_citation_v1",
        "requirement_id": requirement.id,
        "document_sha256": document.sha256,
        "source_claim_id": claim.id,
        "field": "unit",
        "source": claim.unit,
        "canonical": "ΔE*ab",
        "source_page": page,
        "source_quote": citation,
        "source_start": start,
        "source_end": end,
        "body_quote": claim.raw_text,
        "body_source_quote": physical[body[0][0] : body[0][1]],
        "body_source_start": body[0][0],
        "body_source_end": body[0][1],
        "source_quote_sha256": sha256(citation.encode()).hexdigest(),
        "operator": claim.operator,
        "value": str(claim.value),
    }


def enrich_named_scale_citations(
    extraction: CustomerExtraction, document: IngestedDocument
) -> tuple[CustomerExtraction, list[CustomerUnitCitationAction | CustomerAtomGroundingAction]]:
    """Add a unique physical summary row for one already located source fact.

    Only the evidence list changes. No native atom value, unit, raw quote, role
    or identity is repaired here. Every actual returned span is retained for
    auditing; a competing colour owner or repeated summary prevents recovery.
    """
    enriched = extraction.model_copy(deep=True)
    actions = []
    for requirement in enriched.requirements:
        for claim in requirement.atomic_claims:
            proof = physical_named_scale_unit_evidence(
                claim, requirement, document, requirements=extraction.requirements
            )
            if proof is None:
                continue
            citation, page = proof["source_quote"], proof["source_page"]
            if not any(
                evidence.page == page and citation_contains(evidence.quote, claim.raw_text)
                for evidence in requirement.evidence
            ):
                # A physically contiguous sentence can be split across two
                # owned evidence entries. Retain its exact physical union too.
                requirement.evidence.append(
                    RequirementEvidence(
                        page=page,
                        quote=proof["body_source_quote"],
                    )
                )
                actions.append(
                    CustomerAtomGroundingAction(
                        requirement_id=requirement.id,
                        claim_ids=[claim.id],
                        page=page,
                        citation=proof["body_source_quote"],
                        source_start=proof["body_source_start"],
                        source_end=proof["body_source_end"],
                    )
                )
            if any(
                evidence.page == page and citation_contains(evidence.quote, citation)
                for evidence in requirement.evidence
            ):
                continue
            evidence = RequirementEvidence(page=page, quote=citation)
            action = CustomerUnitCitationAction(
                requirement_id=requirement.id,
                claim_ids=[claim.id],
                page=page,
                citation=citation,
                source_start=proof["source_start"],
                source_end=proof["source_end"],
                source_unit="ΔE*ab",
                source_operator=claim.operator,
                source_value=str(claim.value),
                body_quote=claim.raw_text,
            )
            requirement.evidence.append(evidence)
            actions.append(action)
    return enriched, actions

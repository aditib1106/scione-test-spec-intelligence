"""A complete source quotation can be usable prose without a scalar parser.

This path never repairs an invalid quantity, treats a shaved residual as a
requirement, or predicts a specimen passes. It creates comparison copies only.
"""

from __future__ import annotations

import re

from scione.numeric_literals import source_numbers
from scione.parameters import resolve_definition
from scione.source_text import citation_contains, citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import source_quantities
from scione.structured.models import AtomicClaim

_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"deux|trois|quatre|cinq|six|sept|huit|neuf|dix|eins|zwei|drei|vier|fünf|"
    r"sechs|sieben|acht|neun|zehn|uno|dos|tres|cuatro|cinco|seis|siete|ocho|"
    r"nueve|diez)\b|[零〇一二两三四五六七八九十百千万亿]",
    re.I,
)


def literal_descriptive_claim(
    claim: AtomicClaim, source: CustomerRequirement
) -> AtomicClaim | None:
    """Make a detached text-comparison view of one wholly located clause.

    A reporting instruction may describe a requested service. Qualitative
    acceptance criteria are excluded: matching text does not prove that a
    specimen will meet a visual acceptance requirement. Numbers and count
    words are excluded; code-like resource names retain exact identity checks
    through the ordinary wording guard.
    """
    if (
        claim.normalization_state != "unresolved"
        or claim.parameter_id is not None
        or claim.kind not in {"condition", "scope", "reporting"}
        or claim.operator != "text"
        or claim.availability == "unsupported"
        or any(
            value is not None
            for value in (
                claim.value,
                claim.minimum,
                claim.maximum,
                claim.tolerance,
            )
        )
        or claim.values
        or claim.unit is not None
        or not claim.text_value
        or citation_text(claim.text_value) != citation_text(claim.raw_text)
        or source_numbers(claim.raw_text)
        or source_quantities(claim.raw_text, context=claim.raw_text)
        or _NUMBER_WORDS.search(claim.raw_text)
        or not claim.source_pages
    ):
        return None
    located = {
        evidence.page
        for evidence in source.evidence
        if citation_contains(evidence.quote, claim.raw_text)
    }
    if not set(claim.source_pages) <= located:
        return None
    return claim.model_copy(
        deep=True,
        update={
            "parameter_id": "verbatim_source_clause",
            "normalization_state": "parsed",
            "availability": "supported",
        },
    )


def literal_comparison_claim(claim: AtomicClaim, source: CustomerRequirement) -> AtomicClaim | None:
    """Recognize a wholly owned custom service statement with a recorded ID.

    Unknown IDs alone are not equal. A bare category, known quantity or shaved
    fragment cannot receive this literal whole-statement recovery.
    """
    direct = literal_descriptive_claim(claim, source)
    if direct is not None:
        return direct
    if (
        claim.normalization_state == "unresolved"
        or not claim.parameter_id
        or resolve_definition(claim.parameter_id) is not None
        or claim.operator != "text"
        or not claim.text_value
        or len(re.findall(r"\b\w+\b", claim.raw_text)) < 3
        or not re.match(
            r"^(?:report|record|provide|we\s+(?:want|require)|please\s+(?:use|perform))\b",
            claim.raw_text,
            re.I,
        )
        or citation_text(claim.text_value).rstrip(".;")
        != citation_text(claim.raw_text).rstrip(".;")
    ):
        return None
    return literal_descriptive_claim(
        claim.model_copy(
            update={
                "parameter_id": None,
                "normalization_state": "unresolved",
                "text_value": claim.raw_text,
            }
        ),
        source,
    )

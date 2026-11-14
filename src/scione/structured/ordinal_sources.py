"""Tie a compact ordinal summary to an explicitly printed allowed-value list.

No scale resolution or external standard content is assumed. The body itself
must enumerate exactly the model's values for the explicitly named ISO route.
"""

import re
from decimal import Decimal

from scione.parameters import resolve_definition
from scione.source_text import citation_contains, citation_text


def owned_ordinal_enumeration(claim, requirement):
    definition = resolve_definition(claim.parameter_id)
    if not (
        definition
        and definition.id == "adhesion_class"
        and claim.kind == "criterion"
        and claim.operator == "one_of"
        and len(claim.values) == 2
        and len(set(claim.values)) == 2
        and claim.value is None
        and claim.minimum is None
        and claim.maximum is None
        and claim.tolerance is None
        and claim.unit in {None, "class"}
        and claim.source_pages
    ):
        return None
    raw = citation_text(claim.raw_text)
    summary = re.search(r"\bISO\s+class\s+(\d)\s*[-–]\s*(\d)(?![\d.])", raw, re.I)
    if not summary or {Decimal(summary[1]), Decimal(summary[2])} != set(claim.values):
        return None
    if re.search(r"\b(?:not|no|except|unless|optional|alternative)\b", raw, re.I):
        return None
    primary = [
        method
        for method in requirement.requested_methods
        if "optional" not in (method.role or "").casefold()
        and "alternative" not in (method.role or "").casefold()
    ]
    if len(primary) != 1 or not re.match(r"ISO\s*\d", primary[0].code, re.I):
        return None
    owned = [evidence for evidence in requirement.evidence if evidence.page in claim.source_pages]
    if not any(citation_contains(evidence.quote, claim.raw_text) for evidence in owned):
        return None
    found = []
    for evidence in owned:
        text = citation_text(evidence.quote)
        for match in re.finditer(
            r"\b(?:if\s+(?:the\s+)?)?ISO\s+(?:procedure|route|method)\s+"
            r"(?:is\s+used\s*[,;:]?\s*)?(?:accepts?\s+)?only\s+class\s+"
            r"(?P<one>\d)\s+or\s+(?P<two>\d)\s+(?:is|are)\s+acceptable\b",
            text,
            re.I,
        ):
            found.append(
                (evidence.page, match.group(), {Decimal(match["one"]), Decimal(match["two"])})
            )
    if not found or any(values != set(claim.values) for _, _, values in found):
        return None
    return {
        "rule": "owned_ordinal_body_enumeration",
        "source_claim_id": claim.id,
        "field": "operator_grounding",
        "operator": claim.operator,
        "values": [str(value) for value in claim.values],
        "source_page": found[0][0],
        "source_quote": found[0][1],
        "summary_quote": claim.raw_text,
        "no_scale_resolution_assumed": True,
    }

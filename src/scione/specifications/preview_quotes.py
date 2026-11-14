"""Select a field's literal evidence without asking a model to locate it."""

from typing import Literal

from scione.source_text import citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim

PreviewField = Literal["name", "method", "condition", "criterion"]


def parameter_preview_quotes(
    requirement: CustomerRequirement, page: int, claim: AtomicClaim
) -> tuple[list[str], str]:
    """Locate a parameter only inside its own test's recorded source span."""
    parent = [item.quote for item in requirement.evidence if item.page == page]
    if (
        page in claim.source_pages
        and claim.raw_text.strip()
        and any(citation_text(claim.raw_text) in citation_text(quote) for quote in parent)
    ):
        return [claim.raw_text], "parameter"
    return field_preview_quotes(requirement, page, None)


def field_preview_quotes(
    requirement: CustomerRequirement, page: int, field: PreviewField | None
) -> tuple[list[str], str]:
    """Only select text contained in this item's source evidence on this page.

    Translated names, unquoted fields or empty fields fall back to the item's
    evidence. The PDF renderer still rejects ambiguous/nonliteral locations.
    """
    parent = list(dict.fromkeys(e.quote for e in requirement.evidence if e.page == page))
    if field is None or not parent:
        return parent, "item" if parent else "none"
    candidates = []
    if field == "name":
        candidates.append(requirement.original_test_name)
    elif field == "method":
        candidates.extend(method.code for method in requirement.requested_methods)
    else:
        values = requirement.conditions if field == "condition" else requirement.acceptance_criteria
        candidates.extend(value.raw_value for value in values)
        candidates.extend(
            atom.raw_text
            for atom in requirement.atomic_claims
            if atom.kind in ({"condition"} if field == "condition" else {"criterion", "reporting"})
            and (not atom.source_pages or page in atom.source_pages)
        )
    quotes = list(
        dict.fromkeys(
            text
            for text in candidates
            if text.strip()
            and any(citation_text(text) in citation_text(evidence) for evidence in parent)
        )
    )
    return (quotes[:100], "field") if quotes else (parent, "item")

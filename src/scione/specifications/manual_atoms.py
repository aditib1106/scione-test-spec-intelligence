"""Validate explicit parameter corrections without rewriting model evidence."""

from scione.numeric_literals import source_numbers
from scione.parameters.units import canonical_unit_name
from scione.source_text import citation_contains, citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import (
    grounded_source_numbers,
    source_quantities,
    source_unit_name,
)
from scione.structured.normalization import known_source_fact_problem


def validate_parameter_edits(
    previous: CustomerRequirement | None, edited: CustomerRequirement, *, source_unit_proofs=None
) -> None:
    """Check only changed atoms; an unrelated edit cannot certify old errors.

    Unfamiliar properties remain permissible source data. Normalization and
    matching still decide whether they can be compared; a human edit is not a
    shortcut around scientific checks or an independent lab declaration.
    """
    identifiers = [claim.id for claim in edited.atomic_claims]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Each detailed parameter needs its own identifier.")
    original = {claim.id: claim for claim in previous.atomic_claims} if previous else {}
    for claim in edited.atomic_claims:
        if claim == original.get(claim.id):
            continue
        pages = set(claim.source_pages)
        if not pages or any(
            not any(
                evidence.page == page and citation_contains(evidence.quote, claim.raw_text)
                for evidence in edited.evidence
            )
            for page in pages
        ):
            raise ValueError(
                f"{claim.label}: keep a source quotation and page belonging to this test. "
                "Update the test's source evidence if its quotation is missing."
            )
        numbers = {
            value
            for value in (claim.value, claim.minimum, claim.maximum, claim.tolerance)
            if value is not None
        } | set(claim.values)
        if claim.unit:
            stated = {
                quantity.value
                for quantity in source_quantities(claim.raw_text, additional_unit=claim.unit)
                if quantity.value is not None
                and quantity.ambiguity is None
                and quantity.unit == source_unit_name(claim.unit)
            }
        else:
            # A genuinely unitless custom value may stay unresolved, but its
            # literal number must still occur in the owned source. Physical
            # identity, units and scope remain separate normalizer checks.
            stated = grounded_source_numbers(claim.raw_text) | source_numbers(
                citation_text(claim.raw_text)
            )
        proof = (source_unit_proofs or {}).get(claim.id)
        document_unit = bool(
            proof
            and proof.get("source_claim_id") == claim.id
            and canonical_unit_name(proof.get("canonical")) == canonical_unit_name(claim.unit)
            and proof.get("body_quote") == claim.raw_text
            and proof.get("operator") == claim.operator
            and str(claim.value) == proof.get("value")
            and claim.value is not None
            and claim.minimum is None
            and claim.maximum is None
            and claim.tolerance is None
            and not claim.values
            and set(claim.source_pages) == {proof.get("source_page")}
        )
        if numbers and not numbers <= stated and not document_unit:
            raise ValueError(
                f"{claim.label}: the entered number is not in this parameter's source quotation. "
                "Use the number and unit printed in the PDF."
            )
        if problem := known_source_fact_problem(claim, edited):
            raise ValueError(f"{claim.label}: {problem}")

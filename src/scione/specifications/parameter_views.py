"""Build document-verified derived views without changing saved source records."""

from scione.parameters.units import canonical_unit_name
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.specifications.named_scale_units import physical_named_scale_unit_evidence
from scione.structured import normalize_requirement


def physical_unit_proofs(
    requirement: CustomerRequirement,
    document: IngestedDocument,
    requirements: list[CustomerRequirement],
) -> dict[str, dict]:
    """Revalidate both a missing scale and an explicitly retained derived scale.

    The latter supports editing a previously projected field. A different unit
    is never replaced or accepted by reconstructing a unitless candidate.
    """
    proofs = {}
    for claim in requirement.atomic_claims:
        if claim.unit is not None and canonical_unit_name(claim.unit) != "ΔE*ab":
            continue
        candidate = claim.model_copy(update={"unit": None})
        proof = physical_named_scale_unit_evidence(
            candidate, requirement, document, requirements=requirements
        )
        if proof:
            proofs[claim.id] = {**proof, "source": claim.unit}
    return proofs


def physical_parameter_views(
    requirements: list[CustomerRequirement], document: IngestedDocument
) -> tuple[dict[str, CustomerRequirement], dict[str, dict[str, dict]]]:
    """Attach only rechecked physical evidence to detached runtime requirements."""
    views, proofs = {}, {}
    for requirement in requirements:
        row_proofs = physical_unit_proofs(requirement, document, requirements)
        view = requirement.model_copy(deep=True)
        for proof in row_proofs.values():
            # The physical helper can prove adjacent original evidence pieces
            # form one complete body span. Retain that exact span as well as
            # the unit row on this detached view, never the archived extraction.
            for quote in (proof.get("body_source_quote"), proof["source_quote"]):
                if quote and not any(
                    evidence.page == proof["source_page"]
                    and citation_contains(evidence.quote, quote)
                    for evidence in view.evidence
                ):
                    view.evidence.append(
                        RequirementEvidence(page=proof["source_page"], quote=quote)
                    )
        views[view.id], proofs[view.id] = view, row_proofs
    return views, proofs


def physical_normalized_requirements(
    requirements: list[CustomerRequirement], document: IngestedDocument
):
    views, proofs = physical_parameter_views(requirements, document)
    return [
        normalize_requirement(views[row.id], source_unit_proofs=proofs[row.id])
        for row in requirements
    ]

"""Build document-verified derived views without changing saved source records."""

import json
from copy import deepcopy
from functools import lru_cache

from scione.parameters.units import canonical_unit_name
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.conditioning_facts import complete_conditioning_facts
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerRequirement,
    RequirementEvidence,
)
from scione.specifications.named_scale_units import physical_named_scale_unit_evidence
from scione.specifications.section_scopes import apply_section_scopes
from scione.specifications.table_cell_grounding import ground_table_cell_citations
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


def _physical_parameter_views(
    requirements: list[CustomerRequirement], document: IngestedDocument, *,
    pdf_bytes: bytes | None = None,
) -> tuple[dict[str, CustomerRequirement], dict[str, dict[str, dict]]]:
    """Attach only rechecked physical evidence to detached runtime requirements."""
    from scione.specifications.atom_citations import enrich_atom_citations

    # Replay citation-only recovery on detached views as well as new runs.
    # Older artifacts keep their original evidence lists and provider output.
    derivations = []
    source_requirements = requirements
    if pdf_bytes is not None:
        source_requirements, scope_actions = apply_section_scopes(
            source_requirements, document, pdf_bytes
        )
        derivations.extend(scope_actions)
        source_requirements, cell_actions = ground_table_cell_citations(
            source_requirements, document, pdf_bytes
        )
        derivations.extend(cell_actions)
    completed = []
    for requirement in source_requirements:
        requirement, actions = complete_conditioning_facts(requirement, document)
        completed.append(requirement)
        derivations.extend(actions)
    source_view = CustomerExtraction(
        schema_version="0.1", document=CustomerDocumentMetadata(), requirements=completed
    )
    cited, _actions = enrich_atom_citations(source_view, document)
    views, proofs = {}, {}
    for requirement in cited.requirements:
        row_proofs = physical_unit_proofs(requirement, document, cited.requirements)
        row_actions = [action for action in derivations
                       if action["requirement_id"] == requirement.id]
        if row_actions:
            row_proofs["__source_derivations__"] = {"actions": row_actions}
        view = requirement.model_copy(deep=True)
        for proof in row_proofs.values():
            if "source_quote" not in proof:
                continue
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


@lru_cache(maxsize=8)
def _cached_source_views(requirement_json: str, document_json: str, pdf_bytes: bytes):
    """Pure source data only: no lab, catalog, account or session enters this cache."""
    return _physical_parameter_views(
        [CustomerRequirement.model_validate(item) for item in json.loads(requirement_json)],
        IngestedDocument.model_validate_json(document_json), pdf_bytes=pdf_bytes,
    )


def physical_parameter_views(
    requirements: list[CustomerRequirement], document: IngestedDocument, *,
    pdf_bytes: bytes | None = None,
) -> tuple[dict[str, CustomerRequirement], dict[str, dict[str, dict]]]:
    """Reuse exact small source derivations, always returning detached copies.

    The caller still reads its authorized PDF before entering here. Complete
    source facts/text/bytes form the key, so edits cannot reuse another version.
    Large PDFs bypass the bounded memory cache; nothing is persisted to disk.
    """
    if pdf_bytes is None or len(pdf_bytes) > 1_000_000:
        return _physical_parameter_views(requirements, document, pdf_bytes=pdf_bytes)
    requirement_json = json.dumps(
        [row.model_dump(mode="json") for row in requirements],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    document_json = document.model_dump_json(exclude_computed_fields=True)
    if len(requirement_json) + len(document_json) + len(pdf_bytes) > 2_000_000:
        return _physical_parameter_views(requirements, document, pdf_bytes=pdf_bytes)
    return deepcopy(_cached_source_views(
        requirement_json, document_json, pdf_bytes,
    ))


def physical_normalized_requirements(
    requirements: list[CustomerRequirement], document: IngestedDocument, *,
    pdf_bytes: bytes | None = None,
):
    views, proofs = physical_parameter_views(requirements, document, pdf_bytes=pdf_bytes)
    return [
        normalize_requirement(
            views[row.id], source_unit_proofs=proofs[row.id], native_first=True
        )
        for row in requirements
    ]

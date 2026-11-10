"""Attach native provenance to an effective, derived parameter review view."""

from scione.source_text import citation_contains, citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured import normalize_requirement
from scione.structured.normalization import _contextualize, _intrinsic_stage
from scione.structured.source_clauses import ordered_thermal_cycle_proof


def _review_origin_shape(native, requirement, normalized, unit_proof=None):
    """Replay only the normalizer's presentation metadata transforms.

    Matching an origin must include phase, unit, context and pages. A shared
    quotation containing before/after facts is not permission to edit both.
    These transforms neither validate values nor change the stored native atom.
    """
    candidate = native
    if unit_proof and unit_proof.get("source_claim_id") == native.id:
        candidate = candidate.model_copy(update={"unit": unit_proof["canonical"]})
    if not candidate.source_context:
        contexts = {
            value.context
            for kind, values in (
                ("condition", requirement.conditions),
                ("criterion", requirement.acceptance_criteria),
            )
            for value in values
            if value.context
            and kind == candidate.kind
            and citation_contains(value.raw_value, candidate.raw_text)
        }
        if len(contexts) == 1:
            candidate = _contextualize(candidate, next(iter(contexts)))
    # Invalid/canonicalized identities affect intrinsic stages in the actual
    # normalizer. Use its recorded identity rather than guessing another one.
    count_unit = {"cycles": "cycles", "measurement_locations": "locations"}.get(native.parameter_id)
    if count_unit and candidate.unit is None:
        candidate = candidate.model_copy(update={"unit": count_unit})
    candidate = _intrinsic_stage(
        candidate.model_copy(
            update={
                "parameter_id": normalized.parameter_id,
            }
        )
    )
    if ordered_thermal_cycle_proof(candidate, requirement):
        candidate = candidate.model_copy(update={"stage": "thermal_cycle"})
    return candidate


def concise_catalog_name(requirement: CustomerRequirement, catalog) -> str | None:
    """Presentation label only, when explicit primary codes name one catalog family.

    This is not a capability or equivalence decision. Unknown/composite identities
    keep the customer's own label, and the source name is always retained.
    """
    primary = [
        method
        for method in requirement.requested_methods
        if not any(
            term in (method.role or "").casefold()
            for term in ("optional", "alternative", "preparation", "exposure", "assessment")
        )
    ]
    if not primary:
        return None
    candidates = set()
    for method in primary:
        exact = {
            item.id
            for item in catalog.methods
            if item.identifier.casefold().strip() == method.code.casefold().strip()
        }
        if not exact:
            return None
        families = {test.id for test in catalog.tests if exact & set(test.method_ids)}
        if len(families) != 1:
            return None
        candidates.update(families)
    return catalog.test_by_id(next(iter(candidates))).name if len(candidates) == 1 else None


def parameter_review_view(
    requirement: CustomerRequirement, presenter, catalog=None, *, source_unit_proofs=None
) -> dict:
    normalized = normalize_requirement(requirement, source_unit_proofs=source_unit_proofs)
    rows = []
    shape = (
        "label",
        "kind",
        "stage",
        "operator",
        "value",
        "minimum",
        "maximum",
        "tolerance",
        "values",
        "text_value",
        "configuration",
        "sequence",
        "unit",
        "source_context",
    )
    for claim in normalized.claims:
        origins = []
        if claim.id.startswith(f"{requirement.id}_claim_"):
            for native in requirement.atomic_claims:
                if citation_text(native.raw_text) != citation_text(claim.raw_text):
                    continue
                candidate = _review_origin_shape(
                    native, requirement, claim, (source_unit_proofs or {}).get(native.id)
                )
                if all(getattr(candidate, key) == getattr(claim, key) for key in shape) and set(
                    native.source_pages
                ) == set(claim.source_pages):
                    origins.append(native.id)
        rows.append({**presenter(claim), "source_atom_ids": origins})
    return {
        "claims": rows,
        "display_test_name": concise_catalog_name(requirement, catalog) if catalog else None,
        "normalization_actions": normalized.normalization_actions,
    }

"""Remove repeated atomic rows from model input without trimming source facts.

This is a wire-format projection, not retrieval, chunking, or summarization.
The full immutable snapshot and exact transmitted request remain run artifacts.
"""

from __future__ import annotations

from scione.matching.models import MatchingInput
from scione.structured.models import AtomicClaim

MATCHING_PROJECTION_VERSION = "authoritative_atoms_v0_2"


class MatchingProjectionError(ValueError):
    """A source atom would be lost by projecting an inconsistent snapshot."""


def _retains_source_fact(source: AtomicClaim, normalized: AtomicClaim) -> bool:
    # Grounding may intentionally change identity/state/availability, or attach
    # a more specific context/stage. It must not change the quoted source facts.
    fields = (
        "kind",
        "operator",
        "value",
        "minimum",
        "maximum",
        "tolerance",
        "values",
        "text_value",
        "raw_text",
        "sequence",
        "configuration",
    )
    if any(getattr(source, field) != getattr(normalized, field) for field in fields):
        return False
    if source.source_context and source.source_context != normalized.source_context:
        return False
    if source.stage != "general" and source.stage != normalized.stage:
        return False
    if source.unit != normalized.unit:
        count_units = {"cycles": "cycles", "measurement_locations": "locations"}
        if not (source.unit is None and count_units.get(source.parameter_id) == normalized.unit):
            return False
    return set(source.source_pages) <= set(normalized.source_pages)


def _assert_source_facts_retained(
    source: list[AtomicClaim], authoritative: list[AtomicClaim]
) -> None:
    for claim in source:
        if not any(_retains_source_fact(claim, target) for target in authoritative):
            raise MatchingProjectionError(
                "The matching snapshot does not retain a source atom in its authoritative "
                "normalized claims. Rebuild the snapshot before requesting semantic matching."
            )


def project_matching_input(snapshot: MatchingInput) -> MatchingInput:
    """Return a detached MatchingInput with one authoritative atomic view.

    Preserve all canonical correspondence candidates and all raw fields/evidence.
    Source atomic rows have competing IDs and may have intentionally downgraded
    identities after grounding. Remove that alternative view only after checking
    its source facts survive in normalized/candidate claims. An inconsistent
    external snapshot fails before inference instead of discarding source facts.
    The ordinary MatchingInput schema can still parse the serialized request.
    """
    for row in snapshot.requirements:
        _assert_source_facts_retained(row.requirement.atomic_claims, row.normalized.claims)
    for row in snapshot.capabilities:
        _assert_source_facts_retained(row.capability.atomic_claims, row.claims)
    data = snapshot.model_dump(mode="json")
    for row in data["requirements"]:
        row["requirement"]["atomic_claims"] = []
    for row in data["capabilities"]:
        row["capability"]["atomic_claims"] = []
    return MatchingInput.model_validate(data)

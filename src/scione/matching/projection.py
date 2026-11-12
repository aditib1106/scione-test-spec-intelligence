"""Remove repeated atomic rows from model input without trimming source facts.

This is a wire-format projection, not retrieval, chunking, or summarization.
The full immutable snapshot and exact transmitted request remain run artifacts.
"""

from __future__ import annotations

from scione.matching.models import MatchingInput
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim
from scione.structured.source_clauses import ordered_thermal_cycle_proof

MATCHING_PROJECTION_VERSION = "authoritative_atoms_compact_v0_6"


class MatchingProjectionError(ValueError):
    """A source atom would be lost by projecting an inconsistent snapshot."""


def _retains_source_fact(
    source: AtomicClaim,
    normalized: AtomicClaim,
    requirement: CustomerRequirement | None = None,
    normalization_actions: list[dict] | None = None,
) -> bool:
    # Grounding may intentionally change identity/state/availability, or attach
    # a more specific context/stage. It must not change the quoted source facts.
    if requirement is not None and normalization_actions:
        from scione.structured.normalization import _typed_literal_projection

        # The normalizer can represent one complete, source-quoted numeric
        # literal (for example text "60°") as typed value 60/unit °. Recompute
        # its existing closed proof instead of treating that lossless repair
        # as a changed fact. A claimed audit action alone is insufficient.
        literal = _typed_literal_projection(source)
        if literal is not None and any(
            action.get("rule") == "source_proved_numeric_text_literal"
            and action.get("source_claim_id") == source.id
            and action.get("original_operator") == source.operator
            and action.get("original_text_value") == source.text_value
            and action.get("original_quote_preserved") is True
            and action.get("canonical_operator") == literal.operator
            and action.get("canonical_unit") == literal.unit
            for action in normalization_actions
        ):
            source = literal
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
        # One explicit source-owned thermal cycle may canonicalize the broad
        # 'exposure' label. Every scalar, quotation, sequence and other scope
        # field above must still match; arbitrary model stages remain invalid.
        proved_cycle = bool(
            requirement is not None
            and source.stage == "exposure"
            and normalized.stage == "thermal_cycle"
            and ordered_thermal_cycle_proof(source, requirement)
        )
        if not proved_cycle:
            return False
    if source.unit != normalized.unit:
        count_units = {"cycles": "cycles", "measurement_locations": "locations"}
        named_scale = False
        if requirement is not None:
            from scione.structured.normalization import _named_scale_unit_proof

            for action in normalization_actions or []:
                if (
                    action.get("rule") == "source_named_scale_summary_unit"
                    and action.get("source_claim_id") == source.id
                    and action.get("canonical") == normalized.unit
                    and _named_scale_unit_proof(source, requirement, {source.id: action})
                ):
                    named_scale = True
                    break
        if not (
            named_scale
            or (source.unit is None and count_units.get(source.parameter_id) == normalized.unit)
        ):
            return False
    return set(source.source_pages) <= set(normalized.source_pages)


def _assert_source_facts_retained(
    source: list[AtomicClaim],
    authoritative: list[AtomicClaim],
    requirement: CustomerRequirement | None = None,
    normalization_actions: list[dict] | None = None,
) -> None:
    for claim in source:
        if not any(
            _retains_source_fact(claim, target, requirement, normalization_actions)
            for target in authoritative
        ):
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
        _assert_source_facts_retained(
            row.requirement.atomic_claims,
            row.normalized.claims,
            row.requirement,
            row.normalized.normalization_actions,
        )
    for row in snapshot.capabilities:
        _assert_source_facts_retained(row.capability.atomic_claims, row.claims)
    data = snapshot.model_dump(mode="json")
    for row in data["requirements"]:
        row["requirement"]["atomic_claims"] = []
        # These are software proof/audit records, often containing entire
        # duplicate clause payloads. Retention has just been verified using
        # them; the model needs the resulting facts, not our parser logs.
        # The immutable input snapshot and artifact retain every action.
        row["normalized"]["normalization_actions"] = []
    for row in data["capabilities"]:
        row["capability"]["atomic_claims"] = []
    return MatchingInput.model_validate(data)

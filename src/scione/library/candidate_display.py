"""Display bounded, unconfirmed suggestions without using them as verdict inputs."""

from scione.matching.models import SemanticMatchingRun


def candidate_lab_entries(
    attempt: SemanticMatchingRun | None, requirement_id: str, capability_id: str | None
) -> dict[str, list[dict]]:
    """Read actual values from the captured, selected lab entry only.

    The raw proposal is used for presentation, never compiled into support. A
    rejected correspondence can still explain why an existing entry needs review
    rather than telling the user that no lab value was recorded at all.
    """
    if attempt is None or attempt.status != "completed" or attempt.proposal is None:
        return {}
    proposal = next(
        (row for row in attempt.proposal.items if row.requirement_id == requirement_id), None
    )
    if proposal is None or proposal.capability_id != capability_id or capability_id is None:
        return {}
    capability = next(
        (row for row in attempt.input_snapshot.capabilities if row.capability.id == capability_id),
        None,
    )
    if capability is None:
        return {}
    declared = {claim.id: claim for claim in capability.claims}
    return {
        link.requirement_claim_id: [
            {
                "claim_id": identifier,
                "label": declared[identifier].label,
                "value": declared[identifier].display_value,
                "stage": declared[identifier].stage,
                "context": declared[identifier].source_context,
                "confirmed": False,
            }
            for identifier in link.capability_claim_ids
            if identifier in declared
        ]
        for link in proposal.claim_alignments
    }

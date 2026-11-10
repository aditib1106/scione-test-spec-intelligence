"""Request-specific keys make exact input coverage part of the JSON grammar.

The adapter inserts IDs from keys without inventing omitted rows. Raw provider
text and its parsed keyed object remain untouched in the attempt artifact.
"""

from __future__ import annotations

from copy import deepcopy

from scione.matching.models import MatchingInput

KEYED_WIRE_VERSION = "matching_keyed_v3"


class KeyedWireError(ValueError):
    """The provider did not return exactly the required keyed records."""


def keyed_output_schema(snapshot: MatchingInput, array_schema: dict) -> dict:
    definitions = deepcopy(array_schema["$defs"])

    def without_claim_id(value):
        if isinstance(value, list):
            return [without_claim_id(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: without_claim_id(item) for key, item in value.items()}
        if "properties" in result:
            result["properties"].pop("requirement_claim_id", None)
            result["required"] = [
                key for key in result.get("required", []) if key != "requirement_claim_id"
            ]
        return result

    definitions = without_claim_id(definitions)
    # ItemAlignment is represented by fixed request-specific keys below.
    definitions.pop("ItemAlignment", None)
    items = {}
    capabilities = {candidate.capability.id: candidate for candidate in snapshot.capabilities}

    def nullable_choice(values):
        return (
            {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}
            if values
            else {"type": "null"}
        )

    def bounded_links(value, identifiers):
        if isinstance(value, list):
            return [bounded_links(item, identifiers) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: bounded_links(item, identifiers) for key, item in value.items()}
        if "capability_claim_ids" in result.get("properties", {}):
            result["properties"]["capability_claim_ids"]["items"] = {
                "type": "string",
                "enum": identifiers,
            }
        return result

    for index, row in enumerate(snapshot.requirements):
        allowed_claims = list(
            dict.fromkeys(
                claim.id
                for identifier in row.capability_ids
                for claim in capabilities[identifier].claims
            )
        )
        definition_name = f"SourceRowAlignment{index}"
        definitions[definition_name] = (
            bounded_links(definitions["ClaimAlignment"], allowed_claims)
            if allowed_claims
            else deepcopy(definitions["UnresolvedClaimAlignment"])
        )
        claims = {
            claim.id: {"$ref": f"#/$defs/{definition_name}"} for claim in row.normalized.claims
        }
        items[row.requirement.id] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "identity_status": {"type": "string", "enum": ["aligned", "unknown", "ambiguous"]},
                "canonical_test_id": nullable_choice(list(row.catalog_test_ids)),
                "capability_id": nullable_choice(list(row.capability_ids)),
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                "claim_alignments": {
                    "type": "object",
                    "properties": claims,
                    "required": list(claims),
                    "additionalProperties": False,
                },
            },
            "required": [
                "identity_status",
                "canonical_test_id",
                "capability_id",
                "reason",
                "claim_alignments",
            ],
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "enum": ["0.1"]},
            "items": {
                "type": "object",
                "properties": items,
                "required": list(items),
                "additionalProperties": False,
            },
            "review_issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema_version", "items", "review_issues"],
        "$defs": definitions,
    }


def decode_keyed_output(payload: dict, snapshot: MatchingInput) -> dict:
    """Adapt a complete keyed object to the canonical lists, without repair."""
    items = payload.get("items") if isinstance(payload, dict) else None
    expected = {row.requirement.id for row in snapshot.requirements}
    if not isinstance(items, dict) or set(items) != expected:
        raise KeyedWireError(
            "The keyed response must contain every source requirement exactly once."
        )
    converted = []
    for source in snapshot.requirements:
        identifier = source.requirement.id
        item = items[identifier]
        if not isinstance(item, dict) or "requirement_id" in item:
            raise KeyedWireError("A keyed requirement cannot contain a competing requirement ID.")
        claims = item.get("claim_alignments")
        expected_claims = {claim.id for claim in source.normalized.claims}
        if not isinstance(claims, dict) or set(claims) != expected_claims:
            raise KeyedWireError("The keyed response must contain every source claim exactly once.")
        alignments = []
        for claim in source.normalized.claims:
            link = claims[claim.id]
            if not isinstance(link, dict) or "requirement_claim_id" in link:
                raise KeyedWireError("A keyed claim cannot contain a competing claim ID.")
            alignments.append({**deepcopy(link), "requirement_claim_id": claim.id})
        converted.append(
            {**deepcopy(item), "requirement_id": identifier, "claim_alignments": alignments}
        )
    return {**deepcopy(payload), "items": converted}

"""Request-specific keys make exact input coverage part of the JSON grammar.

The adapter inserts IDs from keys without inventing omitted rows. Raw provider
text and its parsed keyed object remain untouched in the attempt artifact.
"""

from __future__ import annotations

import json
from copy import deepcopy

from scione.matching.models import MatchingInput

KEYED_WIRE_VERSION = "matching_keyed_v5"
_LEGACY_BRIDGES = frozenset({"context_equivalence", "value_equivalence", "joint_scope_equivalence"})


class KeyedWireError(ValueError):
    """The provider did not return exactly the required keyed records."""


def keyed_output_schema(snapshot: MatchingInput, array_schema: dict) -> dict:
    definitions = deepcopy(array_schema["$defs"])

    def without_fields(value, fields):
        if isinstance(value, list):
            return [without_fields(item, fields) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: without_fields(item, fields) for key, item in value.items()}
        if "properties" in result:
            for name in fields:
                result["properties"].pop(name, None)
            result["required"] = [key for key in result.get("required", []) if key not in fields]
        return result

    definitions = without_fields(definitions, {"requirement_claim_id", *_LEGACY_BRIDGES})
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

    def bounded_links(value, identifiers, source_ids, id_ref, source_ref):
        if isinstance(value, list):
            return [
                bounded_links(item, identifiers, source_ids, id_ref, source_ref) for item in value
            ]
        if not isinstance(value, dict):
            return value
        result = {
            key: bounded_links(item, identifiers, source_ids, id_ref, source_ref)
            for key, item in value.items()
        }
        if "capability_claim_ids" in result.get("properties", {}):
            result["properties"]["capability_claim_ids"]["items"] = {
                **({"$ref": f"#/$defs/{id_ref}"} if identifiers else {"type": "string"}),
            }
        if "represented_by_source_ids" in result.get("properties", {}):
            result["properties"]["represented_by_source_ids"]["items"] = {
                **({"$ref": f"#/$defs/{source_ref}"} if source_ids else {"type": "string"}),
            }
            if not source_ids:
                result["properties"]["represented_by_source_ids"]["maxItems"] = 0
        return result

    def require_scope_assessment(value):
        if isinstance(value, list):
            return [require_scope_assessment(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: require_scope_assessment(item) for key, item in value.items()}
        properties = result.get("properties", {})
        if properties.get("relation", {}).get("enum") == ["same_parameter"]:
            properties["scope_relation"] = {"type": "string", "enum": ["equivalent", "covered"]}
        return result

    def native_roles(value):
        if isinstance(value, list):
            return [result for item in value if (result := native_roles(item)) is not None]
        if not isinstance(value, dict):
            return value
        result = {key: native_roles(item) for key, item in value.items()}
        role = result.get("properties", {}).get("context_role", {})
        if "enum" in role:
            allowed = [v for v in role["enum"] if v in {"sample_plan", "project_workflow"}]
            if not allowed:
                return None
            role["enum"] = allowed
        return result

    def deduplicate(value):
        if isinstance(value, list):
            return [deduplicate(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: deduplicate(item) for key, item in value.items()}
        if "anyOf" in result:
            unique = {json.dumps(item, sort_keys=True): item for item in result["anyOf"]}
            result["anyOf"] = list(unique.values())
        return result

    def text_values(value, text_ref, numeric_ref, has_text, has_numeric):
        if isinstance(value, list):
            return [
                text_values(item, text_ref, numeric_ref, has_text, has_numeric) for item in value
            ]
        if not isinstance(value, dict):
            return value
        result = {
            k: text_values(v, text_ref, numeric_ref, has_text, has_numeric)
            for k, v in value.items()
        }
        props = result.get("properties", {})
        if props.get("relation", {}).get("enum") != ["same_parameter"]:
            return result
        choices = []
        for ref, relation in ((text_ref, "text"), (numeric_ref, "numeric")):
            if (relation == "text" and not has_text) or (relation == "numeric" and not has_numeric):
                continue
            choice = deepcopy(result)
            choice["properties"]["capability_claim_ids"] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {"$ref": f"#/$defs/{ref}"},
            }
            choice["properties"]["value_relation"] = (
                {"type": "string", "enum": ["equivalent", "covered", "incompatible"]}
                if relation == "text"
                else {"type": "null"}
            )
            choices.append(choice)
        if choices:
            return {"anyOf": choices}
        props["relation"] = {"type": "string", "enum": ["unknown"]}
        props["capability_claim_ids"] = {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 0,
        }
        props["scope_relation"] = {"type": "null"}
        props["value_relation"] = {"type": "null"}
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
        id_ref, source_ref = f"LabClaimId{index}", f"NativeClaimId{index}"
        source_ids = [claim.id for claim in row.requirement.atomic_claims]
        if allowed_claims:
            definitions[id_ref] = {"type": "string", "enum": allowed_claims}
        if source_ids:
            definitions[source_ref] = {"type": "string", "enum": source_ids}
        definitions[definition_name] = require_scope_assessment(
            bounded_links(
                definitions["ClaimAlignment"]
                if allowed_claims
                else definitions["UnresolvedClaimAlignment"],
                allowed_claims,
                source_ids,
                id_ref,
                source_ref,
            )
        )
        if {claim.id for claim in row.normalized.claims} <= {
            claim.id for claim in row.requirement.atomic_claims
        }:
            # Native scientific facts cannot become generic administrative
            # notes. The two explicit workflow roles preserve the quantities
            # and instructions separately from machine capability judgments.
            definitions[definition_name] = without_fields(
                native_roles(definitions[definition_name]), {"represented_by_source_ids"}
            )
        definitions[definition_name] = deduplicate(definitions[definition_name])
        all_claims = [
            claim for identifier in row.capability_ids for claim in capabilities[identifier].claims
        ]
        text_ids = [
            claim.id
            for claim in all_claims
            if claim.text_value and claim.operator in {"text", "required"}
        ]
        numeric_ids = [
            claim.id for claim in all_claims if claim.operator not in {"text", "required"}
        ]
        text_ref, numeric_ref = f"TextLabClaimId{index}", f"NumericLabClaimId{index}"
        if text_ids:
            definitions[text_ref] = {"type": "string", "enum": list(dict.fromkeys(text_ids))}
        if numeric_ids:
            definitions[numeric_ref] = {"type": "string", "enum": list(dict.fromkeys(numeric_ids))}
        text_definition = f"SourceTextAlignment{index}"
        definitions[text_definition] = deduplicate(
            text_values(
                definitions[definition_name],
                text_ref,
                numeric_ref,
                bool(text_ids),
                bool(numeric_ids),
            )
        )
        claims = {}
        for claim in row.normalized.claims:
            is_text = (
                claim.operator in {"text", "required"}
                and claim.text_value
                and claim.parameter_id not in {"panel_dimensions", "specimen_dimensions"}
            )
            selected_definition = text_definition if is_text else definition_name
            claims[claim.id] = {"$ref": f"#/$defs/{selected_definition}"}
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
    for unused in ("ProposedEquivalence", "ClaimAlignment", "UnresolvedClaimAlignment"):
        definitions.pop(unused, None)
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
            if set(link) & _LEGACY_BRIDGES:
                raise KeyedWireError("Compact semantic coverage does not accept legacy bridges.")
            alignments.append(
                {
                    "context_equivalence": None,
                    "value_equivalence": None,
                    "joint_scope_equivalence": None,
                    "context_role": None,
                    "represented_by_source_ids": [],
                    **deepcopy(link),
                    "requirement_claim_id": claim.id,
                }
            )
        converted.append(
            {**deepcopy(item), "requirement_id": identifier, "claim_alignments": alignments}
        )
    return {**deepcopy(payload), "items": converted}

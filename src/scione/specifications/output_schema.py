"""Compact extraction wire constraints that Python validators cannot advertise.

This module only shapes a JSON Schema before an explicitly requested inference.
It never parses customer text, repairs model values, or calls a provider.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def constrain_compact_extraction_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Add operator/values relationships to an already-strict compact schema.

    The local model's after-validator is not included in Pydantic JSON Schema.
    Full object ``anyOf`` branches let native structured generation see numeric
    arity rules, rather than discovering a broken response after it was billed.
    Local validation must still run: source grounding, context and provider
    enforcement are independent of these shape constraints.

    Text/required claims have no numeric values, because expanding those claims
    would otherwise discard that array. Textual alternative sets are retained
    as the existing explicit unresolved-source path, never guessed into numbers.
    """
    result = deepcopy(schema)
    try:
        definition = result["$defs"]["CompactAtomicClaim"]
        properties = definition["properties"]
        # Local Decimal fields accept exact strings on artifact reload. Model
        # generation still emits JSON numbers, parsed losslessly by the adapter.
        values = {**properties["values"], "items": {"type": "number"}}
        text_value = properties["text_value"]
        non_null_text = next(
            option for option in text_value["anyOf"] if option.get("type") == "string"
        )
    except (KeyError, StopIteration, TypeError) as exc:
        raise ValueError("Expected the strict compact extraction schema.") from exc

    def branch(
        operators: list[str],
        minimum: int,
        maximum: int | None,
        *,
        textual_alternatives: bool = False,
    ) -> dict[str, Any]:
        constrained = deepcopy(definition)
        constrained["properties"]["operator"] = {"type": "string", "enum": operators}
        constrained["properties"]["values"] = {**deepcopy(values), "minItems": minimum}
        if maximum is not None:
            constrained["properties"]["values"]["maxItems"] = maximum
        if textual_alternatives:
            constrained["properties"]["text_value"] = deepcopy(non_null_text)
        # CompactAtomicClaim.numeric_shape also checks every physical page.
        constrained["properties"]["source_pages"]["items"]["minimum"] = 1
        return constrained

    result["$defs"]["CompactAtomicClaim"] = {
        "anyOf": [
            branch(["="], 1, 2),
            branch(["range"], 2, 2),
            branch(["<=", ">=", "<", ">"], 1, 1),
            branch(["text", "required"], 0, 0),
            branch(["one_of"], 1, None),
            branch(["one_of"], 0, 0, textual_alternatives=True),
        ]
    }
    return result

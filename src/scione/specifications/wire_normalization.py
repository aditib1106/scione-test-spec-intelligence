"""Remove empty optional list entries without changing any substantive source fact."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from scione.specifications.models import CustomerWireFormatAction


def normalize_compact_containers(
    payload: dict[str, Any], *, call_index: int
) -> tuple[dict[str, Any], list[CustomerWireFormatAction]]:
    """Keep malformed values for validation; discard only whitespace-only text.

    This is deliberately not JSON repair, value coercion, source correction or
    partial-record acceptance. Evidence, methods and atoms are never dropped.
    The provider response and parsed response passed in remain unchanged.
    """
    result = deepcopy(payload)
    actions: list[CustomerWireFormatAction] = []

    def clean(owner: dict, field: str, path: str) -> None:
        values = owner.get(field)
        if not isinstance(values, list):
            return
        kept = []
        for index, value in enumerate(values):
            if isinstance(value, str) and not value.strip():
                actions.append(
                    CustomerWireFormatAction(path=f"calls[{call_index}].{path}[{index}]")
                )
            else:
                kept.append(value)
        owner[field] = kept

    clean(result, "review_issues", "review_issues")
    for container in ("requirements", "shared_requirements"):
        rows = result.get(container)
        if isinstance(rows, list):
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    for field in ("conditions", "criteria", "scope", "instructions"):
                        clean(row, field, f"{container}[{index}].{field}")
    return result, actions

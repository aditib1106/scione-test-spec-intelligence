"""Parse model JSON without rounding source quantities through binary floats."""

from __future__ import annotations

import json
from typing import Any

from scione.numeric_literals import decimal_literal


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("Non-finite JSON number.")


def _exact_integer(text: str) -> int:
    decimal_literal(text)
    return int(text)


def loads_exact(text: str) -> Any:
    """Retain decimal/exponent lexemes as Decimal and integers as exact ints.

    Providers retain the original response separately. Decimal serialization in
    typed artifacts uses strings; this parser never rewrites the source text.
    """
    return json.loads(
        text,
        parse_float=decimal_literal,
        parse_int=_exact_integer,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_nonfinite,
    )

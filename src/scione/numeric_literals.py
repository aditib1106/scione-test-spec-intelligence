"""Shared source-number syntax for lexical grounding, without value inference."""

from __future__ import annotations

import re
from decimal import Decimal

# This grammar recognizes decimal and contiguous e/E notation, not arbitrary
# arithmetic or multiplication-by-ten notation. Unknown forms remain reviewable.
NUMBER_LITERAL = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
# A sentence-final period is punctuation, not another decimal component. Reject
# continuation by another digit/exponent or a dot followed by numeric syntax so
# malformed '1.2.3' / '1.0.e3' cannot expose a plausible numeric prefix.
NUMBER_END = r"(?![\deE]|\.[\d.eE])"
NUMBER = NUMBER_LITERAL + NUMBER_END
# Do not start inside identifiers, decimal fractions or exponent tails. An
# ordinary range separator can still precede a positive endpoint (20-26).
# The caret checks prevent reading '-3 mm' out of unsupported '1×10^-3 mm'.
NUMBER_START = r"(?<![A-Za-z0-9_.^])(?<![A-Za-z_^][+-])"
_SOURCE_NUMBERS = re.compile(NUMBER_START + NUMBER)

MAX_NUMERIC_DIGITS = 1000
MAX_ADJUSTED_EXPONENT = 1000
# Leave room for a decimal point, leading fractional zeros and exponent syntax,
# while rejecting pathological JSON lexemes before constructing a Decimal.
MAX_NUMERIC_LITERAL_CHARACTERS = 4096


def validate_numeric_complexity(value: Decimal) -> Decimal:
    """Bound representation cost without rounding a source quantity.

    These are software limits, not laboratory capability limits. A value outside
    them must fail validation instead of expanding enormous fixed-point strings
    or Fraction numerators/denominators during display and comparison.
    """
    if not value.is_finite():
        raise ValueError("Numeric values must be finite.")
    if len(value.as_tuple().digits) > MAX_NUMERIC_DIGITS:
        raise ValueError("Numeric values exceed the supported precision budget.")
    if abs(value.adjusted()) > MAX_ADJUSTED_EXPONENT:
        raise ValueError("Numeric values exceed the supported exponent budget.")
    return value


def decimal_literal(text: str) -> Decimal:
    """Parse one bounded numeric lexeme exactly, never through a float."""
    if len(text) > MAX_NUMERIC_LITERAL_CHARACTERS:
        raise ValueError("Numeric literal exceeds the supported size budget.")
    return validate_numeric_complexity(Decimal(text))


def source_numbers(text: str) -> set[Decimal]:
    """Return source-stated literal values without unit/stage interpretation.

    Callers normalize Unicode punctuation first and independently check units
    and scope. No rounding, arithmetic or conversion occurs here.
    """
    return {Decimal(match.group()) for match in _SOURCE_NUMBERS.finditer(text)}

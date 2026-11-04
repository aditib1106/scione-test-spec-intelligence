"""Source-preserving normalization and explicit lab-capability comparisons.

Function imports are lazy so legacy extraction contracts can reference the
additive AtomicClaim schema without a circular dependency.
"""

from scione.structured.models import (
    AtomicClaim,
    ClaimCheck,
    NormalizedRequirement,
    StructuredComparison,
)

__all__ = [
    "AtomicClaim",
    "ClaimCheck",
    "NormalizedRequirement",
    "StructuredComparison",
    "normalize_requirement",
    "normalize_capability",
    "compare_requirement",
]


def normalize_requirement(*args, **kwargs):
    from scione.structured.normalization import normalize_requirement as implementation

    return implementation(*args, **kwargs)


def normalize_capability(*args, **kwargs):
    from scione.structured.normalization import normalize_capability as implementation

    return implementation(*args, **kwargs)


def compare_requirement(*args, **kwargs):
    from scione.structured.comparison import compare_requirement as implementation

    return implementation(*args, **kwargs)

"""Explicit pilot normalization rules, not universal semantic model judgments.

Original names, fluid media, methods and conditions remain in the extraction.
These rules identify a family only; they do not approve capability or quantify
tests. In particular, a bare medium such as water is not a chemical-test alias.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scione.catalogs.models import ReferenceCatalog
    from scione.specifications.models import CustomerRequirement

_SOURCE_DEFINED_TESTS = frozenset(
    {
        "thermal_cycling",
        "heat_aging",
        "environmental_etch",
        "recoat_sanded",
        "recoat_unsanded",
        "low_bake_refinish",
        "spot_polish",
        "pipe_loop_circulation",
        "trim_tapes_adhesives",
        "transport_protection_film",
    }
)
_CHEMICAL_MEDIA = frozenset(
    {
        "deionized water",
        "sulfuric acid 1 %",
        "sodium hydroxide 1 %",
        "pancreatin 50 % (bird droppings)",
        "tree resin",
        "gasoline e10",
        "diesel, washer fluid, coolant",
        "brake fluid dot 4",
    }
)
_CHEMICAL_SECTIONS = ("chemical and fluid resistance", "chemical / fluid resistance")


def normalized_name(value: str) -> str:
    """Normalize presentation differences only, not substance or method meaning."""

    text = unicodedata.normalize("NFKC", value).casefold()
    for dash in "‐‑‒–—−-":
        text = text.replace(dash, " ")
    return " ".join(text.split())


def scoped_pilot_family(requirement: CustomerRequirement, catalog: ReferenceCatalog) -> str | None:
    """Recognize explicitly curated media under a source-stated chemical section.

    A matching section is necessary, and the row must state a procedure or
    operating conditions. Nothing is inferred from the medium by itself. The
    selected procedure is resolved separately, so gradient oven never becomes
    ISO 2812-5 merely because they share a test family.
    """

    if catalog.id != "ms_pnt_410_pilot":
        return None
    if normalized_name(requirement.original_test_name) not in _CHEMICAL_MEDIA:
        return None
    sections = [
        normalized_name(value)
        for value in [
            *requirement.applicability,
            *(item.section for item in requirement.evidence if item.section),
        ]
    ]
    if not any(section in text for text in sections for section in _CHEMICAL_SECTIONS):
        return None
    if not requirement.conditions and not requirement.requested_methods:
        return None
    if "chemical_resistance" not in {test.id for test in catalog.tests}:
        return None
    return "chemical_resistance"


def is_source_defined_procedure(
    requirement: CustomerRequirement, catalog: ReferenceCatalog
) -> bool:
    """Identify a curated custom procedure with source-stated operating conditions.

    This helper does not validate citations and does not infer a missing method.
    Callers must independently validate source evidence. An exact formal-test
    name with an omitted method is deliberately not enough to return True.
    """

    if requirement.requested_methods or not requirement.conditions or not requirement.evidence:
        return False
    if scoped_pilot_family(requirement, catalog) == "chemical_resistance":
        return True
    name = normalized_name(requirement.original_test_name)
    return any(
        test.id in _SOURCE_DEFINED_TESTS
        and name in {normalized_name(test.name), *(normalized_name(x.text) for x in test.aliases)}
        for test in catalog.tests
    )

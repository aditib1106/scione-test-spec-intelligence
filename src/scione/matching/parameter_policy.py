"""Use an existing parameter policy symmetrically after a semantic ID link.

This does not discover a correspondence. The caller must first validate an
explicit same-parameter link. Only comparison copies receive the known ID;
the source snapshot and its values, roles, scope and provenance stay intact.
"""

import re

from scione.parameters import normalize_claim, resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.source_text import citation_contains
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import source_quantities, source_unit_name
from scione.structured.models import AtomicClaim

_HUMIDITY_PREFIX = re.compile(
    r"(?:\brelative[ -]+humidity|\bRH|相对湿度)\s*(?:(?:is|at|of|为|是)\s*|[:：=]\s*)?$",
    re.I,
)
_NEGATED_OR_QUALIFIED = re.compile(
    r"\b(?:not|no|never|without|cannot|except|unless)\b|\b(?:rather than|instead of)\b|"
    r"不|非|无|除外",
    re.I,
)


def source_proved_humidity_unit(
    claim: AtomicClaim, source: CustomerRequirement
) -> tuple[AtomicClaim, dict | None]:
    """Recover a literal RH unit on a comparison copy, never from '%' alone.

    A model can retain the exact humidity values but emit '%' instead of '% RH'.
    The registered source label, owned quotation, pages, complete numeric shape
    and local RH expression must all agree. This is not a fraction-to-humidity
    conversion and cannot invent units for an unfamiliar property or lab input.
    """
    definition = resolve_definition(claim.parameter_id)
    label = resolve_definition(claim.label)
    if not (
        definition
        and definition.id == "relative_humidity"
        and label
        and label.id == "relative_humidity"
        and canonical_unit_name(claim.unit) == "%"
        and claim.kind == "condition"
        and claim.normalization_state != "unresolved"
        and claim.availability == "supported"
        and claim.source_pages
        and not _NEGATED_OR_QUALIFIED.search(claim.raw_text)
    ):
        return claim, None
    owned_pages = {
        evidence.page
        for evidence in source.evidence
        if citation_contains(evidence.quote, claim.raw_text)
    }
    if not set(claim.source_pages) <= owned_pages:
        return claim, None
    numbers = {
        value
        for value in (claim.value, claim.minimum, claim.maximum, *claim.values)
        if value is not None
    }
    if not numbers:
        return claim, None
    groups = {}
    for quantity in source_quantities(claim.raw_text):
        groups.setdefault(quantity.expression_span, []).append(quantity)
    for span, quantities in groups.items():
        if any(row.ambiguity or row.operator_ambiguity for row in quantities):
            continue
        units = {row.unit for row in quantities}
        prefix = claim.raw_text[: span[0]]
        explicit_rh = units == {source_unit_name("% RH")}
        named_percent = units == {source_unit_name("%")} and _HUMIDITY_PREFIX.search(prefix)
        if not (explicit_rh or named_percent):
            continue
        values = {row.value for row in quantities if row.role != "tolerance"}
        tolerances = {row.value for row in quantities if row.role == "tolerance"}
        if values != numbers or tolerances != (
            {claim.tolerance} if claim.tolerance is not None else set()
        ):
            continue
        source_operators = {row.operator for row in quantities if row.operator is not None}
        if source_operators and source_operators != {claim.operator}:
            continue
        if not source_operators and claim.operator != "=":
            continue
        action = {
            "field": "unit",
            "source": claim.unit,
            "canonical": "% RH",
            "rule": "source_proved_relative_humidity_unit",
            "source_pages": sorted(set(claim.source_pages)),
            "source_quote": claim.raw_text,
            "source_expression": claim.raw_text[slice(*span)],
        }
        return claim.model_copy(deep=True, update={"unit": "% RH"}), action
    return claim, None


class ParameterPolicyConflict(ValueError):
    """A selected policy contradicts another explicit, registered source label."""


def comparison_pair(
    requested: AtomicClaim, declared: AtomicClaim
) -> tuple[AtomicClaim, AtomicClaim]:
    """Retain either side's registered rule without choosing between conflicts.

    A translated/custom requested ID must not discard a linked declaration's
    resolution, reporting-scale or offset-temperature rule. Two different known
    definitions are left untouched so the compatibility guard can reject them.
    When neither ID is known, retain the existing conservative custom policy.
    """
    first = resolve_definition(normalize_claim(requested).parameter_id)
    second = resolve_definition(normalize_claim(declared).parameter_id)
    if first and second and first.id != second.id:
        return requested.model_copy(deep=True), declared.model_copy(deep=True)
    definition = first or second
    identifier = definition.id if definition else requested.parameter_id
    if definition:
        for source in (requested, declared):
            label = resolve_definition(source.label)
            generic_temperature = bool(
                label
                and label.id == "temperature"
                and definition.id in {"water_temperature", "air_temperature"}
            )
            if label and label.id != definition.id and not generic_temperature:
                raise ParameterPolicyConflict(
                    "The selected parameter policy conflicts with an explicit registered label."
                )
    # A recorded material/resource restriction is still an operational demand
    # when one editor calls it applicability and another calls it a condition.
    # This narrow naming distinction never bridges acceptance to reportability
    # or changes numeric execution roles/stages.
    operational = bool(
        definition
        and definition.rule in {"category", "required_set", "boolean_presence"}
        and {requested.kind, declared.kind} <= {"scope", "condition"}
    )
    return (
        requested.model_copy(
            deep=True,
            update={
                "parameter_id": identifier,
                "kind": "condition" if operational else requested.kind,
            },
        ),
        declared.model_copy(
            deep=True,
            update={
                "parameter_id": identifier,
                "kind": "condition" if operational else declared.kind,
            },
        ),
    )

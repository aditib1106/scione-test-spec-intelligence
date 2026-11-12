"""Derived atoms for one closed, explicitly located conditioning sentence.

This completes an already attached condition, never discovers tests or decides
applicability. Original requirements and provider artifacts remain unchanged.
"""

from __future__ import annotations

import hashlib
import re

from scione.ingestion.method_inventory import literal_method_designations
from scione.parameters import resolve_definition
from scione.parameters.registry import normalize_stage
from scione.parameters.units import resolve_unit
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains, citation_text
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.lexical_grounding import source_quantities
from scione.structured.models import AtomicClaim

CONDITIONING_FACT_POLICY_VERSION = "literal_conditioning_atoms_v1"
_SENTENCE = re.compile(
    r"Conditioning(?:\s*:\s*|\s+)(?P<duration>.+?)\s+at\s*(?P<temperature>.+?)"
    r"(?:\s*,\s*|\s+and\s*)(?P<relative_humidity>.+)", re.I,
)
_QUALIFIED = re.compile(
    r"\b(?:no|not|never|without|cannot|except|unless|excluding|instead|rather)\b", re.I,
)
_LABELS = {
    "duration": "Conditioning duration", "temperature": "Temperature",
    "relative_humidity": "Relative humidity",
}


def _quantity(expression: str, parameter: str) -> dict | None:
    """Reuse the lexical quantity parser; accept only one complete expression."""
    expression = expression.strip()
    quantities = source_quantities(
        expression, additional_unit="C" if parameter == "temperature" else None,
    )
    if not quantities or any(
        row.value is None or row.ambiguity or row.operator_ambiguity for row in quantities
    ):
        return None
    if {row.expression_span for row in quantities} != {(0, len(expression))}:
        return None
    units = {row.unit for row in quantities}
    if len(units) != 1:
        return None
    unit = next(iter(units))
    if parameter == "relative_humidity":
        if unit != "% RH":
            return None
    elif parameter == "temperature" and unit == "C":
        # Only the explicitly positioned temperature expression permits this
        # compact Celsius spelling. The untouched quotation retains its 'C'.
        unit = "°C"
    else:
        resolved = resolve_unit(unit)
        expected = "time" if parameter == "duration" else parameter
        if resolved is None or resolved.dimension != expected:
            return None
    values = [row for row in quantities if row.role == "value"]
    tolerances = [row for row in quantities if row.role == "tolerance"]
    endpoints = [row for row in quantities if row.role == "range_endpoint"]
    if endpoints:
        if (len(endpoints) != 2 or values or tolerances
                or any(row.operator != "range" for row in endpoints)
                or endpoints[0].value > endpoints[1].value):
            return None
        return {
            "operator": "range", "minimum": endpoints[0].value,
            "maximum": endpoints[1].value, "unit": unit,
        }
    if len(values) != 1 or len(tolerances) > 1 or any(
        row.operator not in {None, "="} for row in quantities
    ):
        return None
    if tolerances and tolerances[0].value < 0:
        return None
    return {
        "operator": "=", "value": values[0].value,
        "tolerance": tolerances[0].value if tolerances else None, "unit": unit,
    }


def _closed_facts(clause: str) -> dict[str, dict] | None:
    if _QUALIFIED.search(clause):
        return None
    body = clause.strip().removesuffix(".").rstrip()
    reference = re.search(r"\s*\(([^()]*)\)$", body)
    if reference:
        designations = literal_method_designations(reference[1])
        if len(designations) != 1 or citation_text(reference[1]) != citation_text(designations[0]):
            return None
        body = body[:reference.start()].rstrip()
    sentence = _SENTENCE.fullmatch(body)
    if sentence is None:
        return None
    facts = {parameter: _quantity(sentence[parameter], parameter) for parameter in _LABELS}
    return facts if all(facts.values()) else None


def _source_location(requirement, clause, document):
    pages = {page.page_number: page.text for page in document.pages}
    claimed = [item for item in requirement.evidence if citation_contains(item.quote, clause)]
    if claimed and any(
        item.page not in pages or not citation_contains(pages[item.page], item.quote)
        or _QUALIFIED.search(item.quote)
        for item in claimed
    ):
        return None  # A wrong claimed page cannot be repaired by searching another page.
    eligible = {item.page for item in claimed} if claimed else set(pages)
    words = clause.split()
    expression = re.compile(r"(?=(" + r"\s+".join(re.escape(word) for word in words) + r"))")
    locations = [
        (page, match.start(1), match.end(1), pages[page][match.start(1):match.end(1)])
        for page in sorted(eligible)
        for match in expression.finditer(pages[page])
        if not pages[page][:match.start(1)].rsplit("\n", 1)[-1].strip()
        and not pages[page][match.end(1):].split("\n", 1)[0].strip()
    ]
    if len(locations) != 1:
        return None
    return (*locations[0], "requirement_evidence" if claimed else "unique_document_literal")


def _same_fact(first: AtomicClaim, second: AtomicClaim) -> bool:
    dimension = {
        "relative_humidity": "humidity", "duration": "time",
    }.get(second.parameter_id, second.parameter_id)
    left = resolve_unit(first.unit, dimension=dimension)
    right = resolve_unit(second.unit, dimension=dimension)
    return bool(
        left and right and (left.dimension, left.factor, left.offset)
        == (right.dimension, right.factor, right.offset)
        and first.operator == second.operator
        and (first.value, first.minimum, first.maximum, first.tolerance, first.values)
        == (second.value, second.minimum, second.maximum, second.tolerance, second.values)
        and first.source_context == second.source_context
        and first.configuration == second.configuration and first.sequence == second.sequence
        and first.normalization_state != "unresolved" and first.availability == "supported"
    )


def complete_conditioning_facts(
    requirement: CustomerRequirement, document: IngestedDocument
) -> tuple[CustomerRequirement, list[dict]]:
    """Return a detached requirement plus provenance for added literal atoms.

    Every quantity must already occur in an existing condition and in a complete
    physical source span. Existing same-stage facts are retained; any conflicting
    conditioning fact blocks completion of that sentence instead of overwriting
    it. Unrecognized syntax/qualifiers remain for the normal source-review audit.
    """
    derived = requirement.model_copy(deep=True)
    actions = []
    for index, condition in enumerate(requirement.conditions, 1):
        facts = _closed_facts(condition.raw_value)
        if facts is None:
            continue
        location = _source_location(requirement, condition.raw_value, document)
        if location is None:
            continue
        page, start, end, quote, locating = location
        proposed = []
        for parameter, fields in facts.items():
            digest = hashlib.sha256(
                f"{requirement.id}\n{page}\n{citation_text(quote)}\n{parameter}".encode()
            ).hexdigest()[:20]
            proposed.append(AtomicClaim(
                id=f"{requirement.id[:100]}_conditioning_{digest}",
                parameter_id=parameter, label=_LABELS[parameter], kind="condition",
                stage="conditioning", raw_text=quote, source_pages=[page], **fields,
            ))
        additions = []
        conflict = False
        for atom in proposed:
            existing = [item for item in derived.atomic_claims if item.kind == "condition"
                        and normalize_stage(item.stage) == "conditioning"
                        and (definition := (
                            resolve_definition(item.parameter_id) or resolve_definition(item.label)
                        )) is not None
                        and definition.id == atom.parameter_id]
            if existing:
                if not all(_same_fact(item, atom) for item in existing):
                    conflict = True
                    break
            else:
                additions.append(atom)
        if conflict or not additions or any(
            item.id == atom.id for item in derived.atomic_claims for atom in additions
        ):
            continue
        derived.atomic_claims.extend(additions)
        evidence = RequirementEvidence(page=page, quote=quote)
        if evidence not in derived.evidence:
            derived.evidence.append(evidence)
        actions.append({
            "policy_version": CONDITIONING_FACT_POLICY_VERSION,
            "action": "added_source_conditioning_atoms", "requirement_id": requirement.id,
            "condition_index": index, "claim_ids": [atom.id for atom in additions],
            "source_page": page, "source_start": start, "source_end": end,
            "source_quote": quote, "location_kind": locating,
            "applicability_basis": "existing_requirement_condition",
            "original_values_preserved": True,
        })
    return derived, actions

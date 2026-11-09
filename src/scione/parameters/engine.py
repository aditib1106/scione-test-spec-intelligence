"""Auditable rules for already-extracted facts, with conservative unknown states.

These functions do not extract facts from prose or ask a language model for a
verdict. Semantic correspondence must be approved before numeric evaluation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from scione.structured.models import AtomicClaim

from .registry import (
    REGISTRY_VERSION,
    ParameterDefinition,
    fold,
    normalize_category,
    normalize_stage,
    resolve_definition,
)
from .units import Unit, canonical_unit_name, convert_value, resolve_unit


@dataclass(frozen=True)
class PairEvaluation:
    status: Literal["supported", "gap", "unknown"]
    reason: str
    rule_id: str


_TEMPERATURE_SUBJECT_PATTERNS = {
    "water": (
        r"\bwater\s+(?:temp(?:erature)?)\b|水温(?:度)?|试验用水温度|"
        r"\bwater\s+(?:at\s+)?[+-]?\d+(?:\.\d+)?\s*°\s*[CF]\b|"
        r"\d+(?:\.\d+)?\s*°\s*[CF]\s+(?:test\s+)?water\b"
    ),
    "air": (
        r"\b(?:air|ambient)\s+(?:temp(?:erature)?)\b|空气温度|气温|"
        r"\bair\s+(?:at\s+)?[+-]?\d+(?:\.\d+)?\s*°\s*[CF]\b|"
        r"\d+(?:\.\d+)?\s*°\s*[CF]\s+air\b"
    ),
    "specimen": r"\b(?:specimen|sample|metal)\s+temperature\b|试样温度|金属温度",
    "oven": r"\boven\s+(?:temperature|setting|setpoint)\b|炉温|烘箱温度",
}


def _temperature_subjects(claim: AtomicClaim) -> set[str]:
    """Find only explicit subject phrases, never infer a medium from test name.

    The older parser loses a source-stated water/air subject when assigning the
    generic temperature ID. Narrow lexical evidence prevents cross-medium
    support without turning every water-jet test's temperature into water.
    A source quote naming multiple media cannot safely associate its numbers.
    """
    unit = resolve_unit(claim.unit)
    if unit is None or unit.dimension != "temperature":
        return set()
    text = " ".join((claim.label, claim.source_context or "", claim.raw_text))
    subjects = {
        subject
        for subject, pattern in _TEMPERATURE_SUBJECT_PATTERNS.items()
        if re.search(pattern, text, flags=re.I)
    }
    for subject, aliases in _SUBJECTS.items():
        if fold(claim.source_context) in {fold(alias) for alias in aliases}:
            subjects.add(subject)
    return subjects


def normalize_claim(claim: AtomicClaim) -> AtomicClaim:
    """Return a copy, changing only approved naming aliases and presentation.

    Source text, context, pages, source numbers, and unresolved states survive.
    Unknown custom IDs never become a known ID merely because labels resemble
    one. An explicit subject in a generic temperature label can specialize it.
    """
    definition = resolve_definition(claim.parameter_id)
    label_definition = resolve_definition(claim.label)
    explicit_subjects = _temperature_subjects(claim)
    if (
        definition is not None
        and definition.id == "temperature"
        and label_definition is not None
        and label_definition.id in {"water_temperature", "air_temperature"}
    ):
        definition = label_definition
    elif definition is not None and definition.id == "temperature" and len(explicit_subjects) == 1:
        subject = next(iter(explicit_subjects))
        if subject in {"water", "air"}:
            definition = resolve_definition(f"{subject}_temperature")
    stage = normalize_stage(claim.stage)
    if stage == "general" and definition and definition.default_stage == "measurement":
        stage = "measurement"
    text = claim.text_value
    if text is not None and definition and definition.value_kind == "category":
        text = normalize_category(definition.id, text)
    return claim.model_copy(
        update={
            "parameter_id": definition.id if definition else claim.parameter_id,
            "unit": canonical_unit_name(claim.unit),
            "stage": stage,
            "text_value": text,
        },
        deep=True,
    )


def normalize_claims(claims: list[AtomicClaim]) -> list[AtomicClaim]:
    return [normalize_claim(claim) for claim in claims]


def normalization_evidence(claim: AtomicClaim) -> dict:
    """Metadata for artifacts without placing implementation fields in facts."""
    normalized = normalize_claim(claim)
    fields = ("parameter_id", "stage", "unit", "text_value")
    return {
        "registry_version": REGISTRY_VERSION,
        "claim_id": claim.id,
        "changes": {
            field: {"source": getattr(claim, field), "canonical": getattr(normalized, field)}
            for field in fields
            if getattr(claim, field) != getattr(normalized, field)
        },
        "semantic_inference": False,
    }


def _literal(value: str | None) -> str:
    """Retain meaningful punctuation in unfamiliar identifiers and contexts."""
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


_SUBJECTS = {
    "water": {"water", "test water", "water temperature", "水", "水温", "试验用水"},
    "air": {"air", "ambient air", "air temperature", "空气", "空气温度", "气温"},
    "specimen": {"specimen", "test specimen", "sample", "试样", "样品"},
    "topcoat": {"topcoat", "top coat", "面漆"},
}


def _context(claim: AtomicClaim, definition: ParameterDefinition | None) -> tuple[str | None, str]:
    subject = definition.subject if definition else None
    explicit_subjects = _temperature_subjects(claim)
    if len(explicit_subjects) > 1 or (
        subject and explicit_subjects and subject not in explicit_subjects
    ):
        return "__ambiguous_subject__", ""
    if explicit_subjects:
        subject = next(iter(explicit_subjects))
    context = _literal(claim.source_context)
    if not context or normalize_stage(context) == claim.stage:
        return subject, ""
    for candidate, aliases in _SUBJECTS.items():
        if fold(context) in {fold(alias) for alias in aliases}:
            # Contradictory explicit subjects remain a distinct context.
            return (
                (candidate, "")
                if subject in {None, candidate}
                else ("__ambiguous_subject__", context)
            )
    material = normalize_category("specimen_material", context)
    return subject, _literal(material)


def generic_reporting_unavailable(request: AtomicClaim, declaration: AtomicClaim) -> bool:
    """A global explicit reporting negative may cover a qualified request.

    This asymmetric exception cannot establish positive capability. A negative
    for a particular material, medium, step, or configuration is not global.
    Unknown custom output semantics and unresolved source facts remain deferred.
    """
    request, declaration = normalize_claim(request), normalize_claim(declaration)
    first, second = (
        resolve_definition(request.parameter_id),
        resolve_definition(declaration.parameter_id),
    )
    if (
        first is None
        or second is None
        or first.id != second.id
        or request.kind not in {"criterion", "reporting"}
        or declaration.kind != "reporting"
        or declaration.availability != "unsupported"
        or request.normalization_state == "unresolved"
        or declaration.normalization_state == "unresolved"
        or declaration.configuration is not None
        or declaration.sequence is not None
    ):
        return False
    requested_context, declared_context = _context(request, first), _context(declaration, second)
    if (
        "__ambiguous_subject__" in {requested_context[0], declared_context[0]}
        or requested_context[0] != declared_context[0]
        or declared_context[1]
    ):
        return False
    # A legacy prose parser may have failed to populate source_context. Do not
    # upgrade an explicitly qualified source negative into a global negative.
    # Unfamiliar prose remains reviewable; this is deliberately a conservative
    # veto rather than an attempt to parse every possible scientific qualifier.
    if re.search(r"\b(?:on|for|with|under|using)\b|仅|只限|针对|用于", declaration.raw_text, re.I):
        return False
    return request.stage == declaration.stage or (
        first.rule == "reporting_range" and declaration.stage == "general"
    )


def contexts_match(request: AtomicClaim, declaration: AtomicClaim) -> bool:
    """Keep stage, specimen/medium, sequence, and selected setup separate."""
    request, declaration = normalize_claim(request), normalize_claim(declaration)
    if generic_reporting_unavailable(request, declaration):
        return True
    if request.sequence != declaration.sequence:
        return False
    if request.configuration and request.configuration != declaration.configuration:
        return False
    first, second = (
        resolve_definition(request.parameter_id),
        resolve_definition(declaration.parameter_id),
    )
    first_context, second_context = _context(request, first), _context(declaration, second)
    if (
        declaration.kind == "reporting"
        and declaration.availability == "unsupported"
        and not second_context[1]
        and re.search(
            r"\b(?:on|for|with|under|using)\b|仅|只限|针对|用于", declaration.raw_text, re.I
        )
    ):
        # A matching absence of structured context cannot erase a restriction
        # still present in legacy source prose. Ask for that scope to be entered.
        return False
    if "__ambiguous_subject__" in {first_context[0], second_context[0]}:
        return False
    if first_context != second_context:
        return False
    if request.stage == declaration.stage:
        return True
    # An intrinsic output scale is not an exposure setting. A generic scale may
    # establish reportability at a stage whose execution is checked separately.
    return bool(
        first
        and first.rule == "reporting_range"
        and request.kind == "criterion"
        and declaration.kind == "reporting"
        and declaration.stage == "general"
    )


def _result(status: str, reason: str, rule: str) -> PairEvaluation:
    return PairEvaluation(status, reason, f"{REGISTRY_VERSION}:{rule}")


def _entries(claim: AtomicClaim, definition: ParameterDefinition) -> set[str]:
    text = claim.text_value or ""
    return {
        _literal(normalize_category(definition.id, item))
        for item in re.split(r"[;；\n]+", text)
        if item.strip()
    }


def _categorical(
    request: AtomicClaim, declaration: AtomicClaim, definition: ParameterDefinition
) -> PairEvaluation:
    left, right = _entries(request, definition), _entries(declaration, definition)
    if not left or not right:
        return _result(
            "unknown",
            "Explicit category or resource entries have not been recorded.",
            definition.rule,
        )
    if left <= right:
        return _result(
            "supported",
            "Every requested category or resource has an explicit equivalent lab declaration.",
            definition.rule,
        )
    return _result(
        "unknown",
        "The lab has not declared every requested category or resource; "
        "absence does not establish inability.",
        definition.rule,
    )


def _units(
    request: AtomicClaim, declaration: AtomicClaim, definition: ParameterDefinition
) -> tuple[Unit, Unit] | None:
    difference = definition.rule == "lower_is_better"
    first = resolve_unit(request.unit, dimension=definition.dimension, difference=difference)
    second = resolve_unit(declaration.unit, dimension=definition.dimension, difference=difference)
    if first is None or second is None or first.dimension != second.dimension:
        return None
    return first, second


def _bounds(claim: AtomicClaim, unit: Unit) -> tuple[Fraction | None, Fraction | None]:
    if claim.operator == "one_of":
        return min(convert_value(value, unit) for value in claim.values), max(
            convert_value(value, unit) for value in claim.values
        )
    if claim.operator == "range":
        return (
            convert_value(claim.minimum, unit) if claim.minimum is not None else None,
            convert_value(claim.maximum, unit) if claim.maximum is not None else None,
        )
    if claim.value is not None:
        tolerance = claim.tolerance or 0
        return convert_value(claim.value - tolerance, unit), convert_value(
            claim.value + tolerance, unit
        )
    return None, None


def _resolution(
    request: AtomicClaim, declaration: AtomicClaim, first: Unit, second: Unit
) -> PairEvaluation:
    if request.operator not in {"<=", "<", "="} or request.value is None or request.tolerance:
        return _result(
            "unknown",
            "Resolution or uncertainty needs an explicit maximum permitted magnitude.",
            "lower_is_better",
        )
    if declaration.operator not in {"=", "<=", "range"}:
        return _result(
            "unknown",
            "The lab has not declared a usable worst-case measurement magnitude.",
            "lower_is_better",
        )
    low, high = _bounds(declaration, second)
    limit = convert_value(request.value, first)
    if high is None or high < 0 or limit < 0 or (low is not None and low < 0):
        return _result(
            "unknown",
            "Measurement magnitudes must be nonnegative with a stated upper bound.",
            "lower_is_better",
        )
    meets = high < limit if request.operator == "<" else high <= limit
    if meets:
        return _result(
            "supported",
            "The declared measurement resolution or uncertainty is at least as fine as requested.",
            "lower_is_better",
        )
    # An upper bound alone does not prove the instrument cannot do better.
    if declaration.operator == "<=" or (
        declaration.operator == "range" and (low is None or low <= limit)
    ):
        return _result(
            "unknown",
            "The declared bound does not establish whether the requested "
            "measurement magnitude is attainable.",
            "lower_is_better",
        )
    return _result(
        "gap",
        "The declared measurement resolution or uncertainty is coarser than requested.",
        "lower_is_better",
    )


def _numeric(
    request: AtomicClaim, declaration: AtomicClaim, definition: ParameterDefinition
) -> PairEvaluation:
    units = _units(request, declaration, definition)
    if units is None:
        return _result(
            "unknown",
            "Units or measurement scales are missing, unknown, or dimensionally incompatible.",
            definition.rule,
        )
    first, second = units
    if definition.rule == "lower_is_better":
        return _resolution(request, declaration, first, second)
    if declaration.tolerance:
        # A fixed controlled setting can satisfy the same requested nominal
        # with an explicit tolerance. Compare accuracy bands, not selectable
        # setpoints, and scale tolerances without temperature offsets.
        if (
            definition.rule == "operating_range"
            and request.kind in {"condition", "scope"}
            and request.operator == declaration.operator == "="
            and request.tolerance
            and request.value is not None
            and declaration.value is not None
            and convert_value(request.value, first) == convert_value(declaration.value, second)
            and Fraction(declaration.tolerance) * second.factor
            <= Fraction(request.tolerance) * first.factor
        ):
            return _result(
                "supported",
                "The lab declares the same fixed nominal setting with a control tolerance "
                "no wider than the requested tolerance; selectable setpoints are not inferred.",
                "fixed_nominal_accuracy",
            )
        return _result(
            "unknown",
            "A lab nominal value with tolerance does not declare a selectable range; "
            "operating adjustability and control accuracy need separate declarations.",
            definition.rule,
        )
    if request.operator in {"<", ">"}:
        return _result(
            "unknown",
            "A strict specimen or operating threshold needs resolution or a reviewed boundary.",
            definition.rule,
        )
    if request.operator in {"<=", ">="} and request.kind in {"condition", "scope"}:
        return _result(
            "unknown",
            "An operating inequality needs the complete requested operating range.",
            definition.rule,
        )
    if declaration.operator in {"<=", "<", ">=", ">", "required", "text"}:
        return _result(
            "unknown",
            "The lab has not declared a complete selectable or reportable range.",
            definition.rule,
        )
    low, high = _bounds(request, first)
    cap_low, cap_high = _bounds(declaration, second)
    if low is None or high is None or (cap_low is None and cap_high is None):
        return _result(
            "unknown", "A required comparison boundary has not been recorded.", definition.rule
        )
    if (cap_low is not None and low < cap_low) or (cap_high is not None and high > cap_high):
        return _result(
            "gap",
            "The request exceeds this lab declaration's stated range in the same context.",
            definition.rule,
        )
    if cap_low is None or cap_high is None:
        return _result(
            "unknown", "One boundary of the lab capability is not recorded.", definition.rule
        )
    if declaration.operator == "one_of":
        points = (
            [convert_value(value, first) for value in request.values]
            if request.operator == "one_of"
            else [low, high]
        )
        allowed = {convert_value(value, second) for value in declaration.values}
        if not (request.operator == "one_of" or low == high) or not all(
            point in allowed for point in points
        ):
            return _result(
                "gap",
                "A requested setting is absent from the lab's explicit discrete set; "
                "intermediate values are not implied.",
                definition.rule,
            )
    if request.kind == "criterion":
        return _result(
            "supported",
            "The lab can measure/report the requested threshold or scale; "
            "specimen pass/fail is not predicted.",
            definition.rule,
        )
    return _result(
        "supported",
        "The requested setting or range is covered by this lab declaration in the same context.",
        definition.rule,
    )


def evaluate_pair(request: AtomicClaim, declaration: AtomicClaim) -> PairEvaluation | None:
    """Evaluate one already-selected pair; None leaves custom policy to caller.

    Callers must still select one compatible complete configuration for the
    whole test. This function deliberately never merges declarations.
    """
    request, declaration = normalize_claim(request), normalize_claim(declaration)
    first, second = (
        resolve_definition(request.parameter_id),
        resolve_definition(declaration.parameter_id),
    )
    if first is None or second is None:
        return None
    if first.id != second.id:
        return _result(
            "unknown", "The facts do not identify the same registered parameter.", "identity"
        )
    if (
        request.normalization_state == "unresolved"
        or declaration.normalization_state == "unresolved"
    ):
        return _result(
            "unknown",
            "A source fact remains unresolved and cannot establish capability.",
            "source_quality",
        )
    if not contexts_match(request, declaration):
        return _result(
            "unknown", "The stage, subject, sequence, or selected configuration differs.", "context"
        )
    if not (
        request.kind == declaration.kind
        or (request.kind == "criterion" and declaration.kind == "reporting")
    ):
        return _result(
            "unknown",
            "Execution conditions, specimen criteria, and reporting capability "
            "have different roles.",
            "role",
        )
    if declaration.availability == "unsupported":
        if generic_reporting_unavailable(request, declaration):
            return _result(
                "gap",
                "The lab explicitly declares this output unavailable without a limiting "
                "scope; it cannot report the more specifically qualified customer output.",
                "generic_reporting_unavailable",
            )
        return _result(
            "gap",
            "The lab explicitly declares this condition or output unavailable.",
            "availability",
        )
    if declaration.availability == "unknown":
        return _result(
            "unknown",
            "The lab has not established availability of this condition or output.",
            "availability",
        )
    if first.rule == "boolean_presence":
        positives = {"available", "yes", "present", "supported", "有", "是", "可用", "提供"}
        named_resources = {"master panel", "reference panel", "标准样板", "参考板"}
        requested_resource = fold(request.text_value)
        generic_presence_request = request.operator == "required" and requested_resource in {
            "",
            *positives,
            *named_resources,
        }
        named_presence = (
            request.operator == "required"
            and declaration.operator in {"required", "text"}
            and fold(request.text_value) in named_resources
            and fold(request.text_value) == fold(declaration.text_value)
        )
        if (
            named_presence
            or fold(declaration.text_value) in positives
            and (generic_presence_request or requested_resource in positives)
        ):
            return _result(
                "supported",
                "The requested resource is explicitly declared available in the same context.",
                first.rule,
            )
        return _result(
            "unknown", "A matching explicit resource-presence declaration is required.", first.rule
        )
    if first.rule in {"category", "required_set"}:
        return _categorical(request, declaration, first)
    if (
        request.operator == "required"
        and declaration.kind == "reporting"
        and declaration.operator not in {"text", "required"}
    ):
        return _result(
            "supported",
            "The lab explicitly declares this reportable output; "
            "specimen success is not predicted.",
            "reporting_presence",
        )
    if request.operator in {"text", "required"}:
        return _result(
            "unknown",
            "Numeric capability requires a typed value or range; prose is retained for review.",
            first.rule,
        )
    return _numeric(request, declaration, first)

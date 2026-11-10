"""Grounded directional wording assessment alongside deterministic numeric checks.

The model may interpret the scope/value of one captured pair. It cannot change
numbers, combine lab configurations, rename a code or erase explicit phase and
medium contradictions. Original facts and proposals stay in the run snapshot.
"""

from __future__ import annotations

import re

from scione.matching.semantics import SemanticEquivalenceError, _codes, _quantities
from scione.parameters import normalize_claim, resolve_definition
from scione.parameters.registry import normalize_stage
from scione.source_text import citation_contains

_AXES = (
    {"water": r"\bwater\b|水温", "air": r"\bair\b|空气", "oil": r"\boil\b|油温"},
    {"inlet": r"\binlet\b|入口", "outlet": r"\boutlet\b|出口"},
    {"topcoat": r"\btop\s*coat\b|面漆", "primer": r"\bprimer\b|底漆"},
)
_TEMPORAL_PHASES = {
    "initial",
    "after_cycle",
    "before_exposure",
    "after_exposure",
    "after_humidity",
    "after_wash",
}
_OPERATION_PHASES = {"curing", "measurement", "conditioning", "exposure", "preparation"}
_CYCLE_PHASES = {"thermal_cycle", "cycle_recovery", "cold", "hot"}
_DOCUMENT_ACRONYMS = {"ISO", "ASTM", "DIN", "EN", "GB", "OEM", "PDF", "CSV", "JSON", "LIMS"}


def _hard_codes(text):
    return {
        code
        for code in _codes(text)
        if code not in _DOCUMENT_ACRONYMS
        and (re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", code) or re.fullmatch(r"[A-Z][A-Z_/-]+", code))
    }


def _text_coverage_conflict(first, second):
    # Semantic entailment is directional. Extra descriptive words or a negated
    # paraphrase are for the model to interpret, not string-equality vetoes.
    # Physical quantities need typed comparison, while actual setup/model codes
    # must still be present in the declared service.
    if not _hard_codes(first) <= _hard_codes(second):
        return True
    if _quantities(first) != _quantities(second) and (_quantities(first) or _quantities(second)):
        return True
    return False


def _phase_conflict(first, second):
    if first == second:
        return False
    # Operation and time are different axes: an initial result is measured,
    # and 'measurement' alone does not mean either before or after exposure.
    for group in (_TEMPORAL_PHASES, _OPERATION_PHASES, _CYCLE_PHASES):
        if first in group and second in group:
            return True
    return {first, second} == {"exposure", "after_exposure"}


def _located(request, source):
    return bool(
        source
        and request.source_pages
        and set(request.source_pages)
        <= {
            evidence.page
            for evidence in source.evidence
            if citation_contains(evidence.quote, request.raw_text)
        }
    )


def coverage_pair(request, declaration, alignment, *, source):
    """Apply an explicit semantic decision to detached comparison copies only.

    Scope is a directional question, not equality of arbitrary quote fragments.
    Semantic assessment may remove redundant prose or interpret broader explicit
    service wording. Numeric ranges and availability always stay authoritative.
    """
    if not (alignment.scope_relation or alignment.value_relation):
        return declaration.model_copy(deep=True), []
    if not _located(request, source) or request.normalization_state == "unresolved":
        raise SemanticEquivalenceError("Semantic coverage needs a grounded usable source fact.")
    if declaration.normalization_state == "unresolved":
        raise SemanticEquivalenceError("Semantic coverage needs a usable lab declaration.")
    if declaration.availability == "unknown":
        raise SemanticEquivalenceError("The lab explicitly leaves this capability unknown.")
    result = declaration.model_copy(deep=True)
    actions = []
    if alignment.scope_relation:
        first, second = normalize_claim(request), normalize_claim(declaration)
        phases = normalize_stage(first.stage), normalize_stage(second.stage)
        if _phase_conflict(*phases):
            raise SemanticEquivalenceError("Semantic scope cannot equate explicit distinct phases.")
        if (
            first.configuration
            and second.configuration
            and first.configuration != second.configuration
        ):
            raise SemanticEquivalenceError("Semantic scope cannot merge distinct configurations.")
        if first.sequence and second.sequence and first.sequence != second.sequence:
            raise SemanticEquivalenceError(
                "Semantic scope cannot change an explicit step sequence."
            )
        left = " ".join((first.label, first.source_context or ""))
        right = " ".join((second.label, second.source_context or ""))
        for axis in _AXES:
            values = [
                {name for name, pattern in axis.items() if re.search(pattern, text, re.I)}
                for text in (left, right)
            ]
            if values[0] and values[1] and values[0] != values[1]:
                raise SemanticEquivalenceError(
                    "Semantic scope cannot change medium, layer or location."
                )
        # A context-only model/configuration code cannot disappear. Codes already
        # owned by the selected value (D65, for example) are not extra qualifiers.
        contextual_codes = []
        for claim in (first, second):
            owned = _hard_codes(claim.text_value or "")
            contextual_codes.append(_hard_codes(claim.source_context or "") - owned)
        if any(contextual_codes) and contextual_codes[0] != contextual_codes[1]:
            raise SemanticEquivalenceError(
                "Semantic scope cannot supply a missing setup identifier."
            )
        result.source_context = request.source_context
        result.stage = request.stage
        result.sequence = request.sequence
        result.configuration = request.configuration
        # A generic ambient conditioning temperature can be explicitly aligned
        # to air temperature; it must never silently become water/metal heat.
        if (
            first.parameter_id == "air_temperature"
            and second.parameter_id == "temperature"
            and re.search(
                r"conditioning|ambient|atmospher|空气|环境", right + " " + second.raw_text, re.I
            )
            and not re.search(r"\bwater\b|\bmetal\b|水温|金属", right + " " + second.raw_text, re.I)
        ):
            result.parameter_id = first.parameter_id
        actions.append(
            {
                "rule": "semantic_directional_scope",
                "relation": alignment.scope_relation,
                "reason": alignment.reason,
                "original_lab_scope": declaration.source_context,
                "original_lab_stage": declaration.stage,
                "semantic_inference": True,
            }
        )
    if alignment.value_relation:
        if request.operator not in {"text", "required"} or declaration.operator not in {
            "text",
            "required",
        }:
            raise SemanticEquivalenceError(
                "Semantic text coverage cannot replace numeric comparison."
            )
        if not request.text_value or not declaration.text_value:
            raise SemanticEquivalenceError("Semantic text coverage needs both recorded values.")
        first, second = normalize_claim(request), normalize_claim(declaration)
        definitions = [resolve_definition(c.parameter_id) for c in (first, second)]
        if any(
            d and d.rule not in {"category", "required_set", "boolean_presence"}
            for d in definitions
        ):
            raise SemanticEquivalenceError("This field requires its numeric measurement policy.")
        if alignment.value_relation != "incompatible":
            if _text_coverage_conflict(request.text_value, declaration.text_value):
                raise SemanticEquivalenceError(
                    "Text coverage cannot change a quantity, identifier or explicit resource."
                )
            result.text_value = request.text_value
        # A prose obligation may be entered under Instructions on one side and
        # Reporting/Scope on the other. This projection records an explicit AI
        # assessment of that same service, never a numeric role conversion or a
        # prediction that the customer's material passes.
        if request.kind != declaration.kind:
            result.kind = request.kind
        actions.append(
            {
                "rule": "semantic_directional_value",
                "relation": alignment.value_relation,
                "reason": alignment.reason,
                "original_lab_value": declaration.text_value,
                "original_lab_kind": declaration.kind,
                "semantic_inference": True,
            }
        )
    return result, actions

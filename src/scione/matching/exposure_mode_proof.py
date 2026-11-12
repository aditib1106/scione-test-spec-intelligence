"""Bind one reviewed lab exposure mode to its own scalar comparison context."""

from __future__ import annotations

import re

from scione.parameters import resolve_definition
from scione.parameters.registry import normalize_stage
from scione.parameters.units import resolve_unit
from scione.source_text import citation_contains

_SOURCE_CONTEXT = re.compile(r"(?P<method>ISO\s+6270-2(?::\d{4})?)\s*\(\s*CH\s*\)")
_CH_VALUE = re.compile(r"CH(?: constant[- ]condensation(?: exposure)?)?[.]?")
_QUALIFIED = re.compile(r"\b(?:not|no|without|except|unless|unsupported|unknown)\b", re.I)


def reviewed_exposure_mode_pair(
    first, second, source, declarations, *, declared_method_codes=(),
):
    """Complete only the lab comparison copy using an existing same-cap mode.

    An ordinary ISO reference is never an exposure mode. The source must own
    the complete ISO 6270-2 (CH) context and the selected capability must declare
    exactly that method. A single reviewed CH field in the same exposure stage,
    sequence and sole configuration supplies the otherwise missing scalar scope.
    This does not change durations or infer temperature, medium or lab support.
    """
    context = " ".join((first.source_context or "").split())
    match = _SOURCE_CONTEXT.fullmatch(context)
    definitions = [resolve_definition(claim.parameter_id) for claim in (first, second)]
    time_units = [resolve_unit(claim.unit) for claim in (first, second)]
    if not (
        match and all(item and item.id == "duration" for item in definitions)
        and all(unit and unit.dimension == "time" for unit in time_units)
        and first.kind == second.kind == "condition"
        and normalize_stage(first.stage) == normalize_stage(second.stage) == "exposure"
        and first.configuration == second.configuration
        and first.sequence == second.sequence
        and first.operator in {"=", "range", "<=", ">=", "<", ">"}
        and second.operator in {"=", "range", "<=", ">=", "<", ">"}
        and first.normalization_state != "unresolved"
        and second.normalization_state == "reviewed"
        and first.availability == second.availability == "supported"
        and second.source_context is None and first.source_pages
        and citation_contains(first.raw_text, first.source_context)
        and not _QUALIFIED.search(first.raw_text)
    ):
        return first, second, []
    def method_key(value):
        return re.sub(r"\s+", "", value)
    source_methods = {method_key(method.code) for method in source.requested_methods}
    method = method_key(match["method"])
    if source_methods != {method} or {
        method_key(code) for code in declared_method_codes
    } != {method}:
        return first, second, []
    owned_pages = {
        evidence.page for evidence in source.evidence
        if citation_contains(evidence.quote, first.raw_text)
    }
    if not set(first.source_pages) <= owned_pages:
        return first, second, []
    modes = [claim for claim in declarations if claim.parameter_id == "exposure_mode"]
    if len(modes) != 1:
        return first, second, []
    mode = modes[0]
    configurations = {
        claim.configuration for claim in declarations
        if normalize_stage(claim.stage) == "exposure"
    }
    if not (
        configurations == {second.configuration}
        and normalize_stage(mode.stage) == "exposure"
        and mode.sequence == second.sequence and mode.configuration == second.configuration
        and mode.normalization_state == "reviewed" and mode.availability == "supported"
        and mode.kind == "condition" and mode.operator in {"text", "required"}
        and mode.source_context is None and mode.text_value
        and _CH_VALUE.fullmatch(mode.text_value)
        and citation_contains(mode.raw_text, mode.text_value)
        and not _QUALIFIED.search(mode.raw_text)
    ):
        return first, second, []
    return first, second.model_copy(deep=True, update={"source_context": first.source_context}), [{
        "rule": "source_method_and_reviewed_sibling_exposure_mode",
        "side": "declaration", "field": "source_context", "source": second.source_context,
        "canonical": first.source_context, "related_lab_claim_id": mode.id,
        "capability_scalar_claim_id": second.id, "source_claim_id": first.id,
        "source_pages": list(first.source_pages), "source_quote": first.raw_text,
        "method_code": match["method"], "exposure_mode": "CH",
        "source_values_preserved": True, "semantic_inference": False,
    }]

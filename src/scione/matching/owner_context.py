"""Keep catalog owner captions separate from scientific comparison scope."""

from __future__ import annotations

import re
import unicodedata

from scione.parameters.registry import normalize_stage, resolve_definition
from scione.source_text import citation_contains


def _literal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).strip(" .")


def _field_caption(value, definition):
    """A field's own neutral label is not an additional scope restriction."""
    if definition is None:
        return False
    labels = {
        _literal(definition.label),
        _literal(definition.id.replace("_", " ")),
        *(_literal(alias) for alias in definition.aliases),
    }
    # Registry aliases sometimes include phase/step decorators for naming.
    # They must not erase a separately requested phase or reporting statistic.
    if re.search(
        r"\b(?:before|after|initial|post|cold|hot|cure|curing|conditioning|recovery|"
        r"mean|average|individual|maximum|minimum|per|each|every)\b", value
    ):
        return False
    neutral = re.sub(r"^(?:measured|reported|measurement of|measurement|value of)\s+", "", value)
    return value in labels or neutral in labels


def _stage_caption(value, claim, source, *, source_side):
    """Only a repeated, already-recorded execution phase is redundant."""
    stage = normalize_stage(claim.stage)
    if stage == "general":
        return False
    bare = re.sub(r"^(?:during|in|at)(?:\s+the)?\s+", "", value)
    if normalize_stage(bare) != stage:
        return False
    if not source_side:
        return True
    return any(
        evidence.page in claim.source_pages
        and citation_contains(evidence.quote.casefold(), claim.raw_text.casefold())
        and citation_contains(evidence.quote.casefold(), value)
        for evidence in source.evidence
    )


def owner_context_pair(requested, declared, source, owner_test_labels=()):
    """Clear complete identity captions on detached copies only.

    Test names must be supplied from the already-selected catalog entry. Every
    explicit comma/semicolon fragment must be a catalog label, requested method
    code, or the same registered parameter's label. Any scientific remainder,
    unfamiliar term, resource code or typo prevents this projection entirely.
    Phase, sequence, configuration, values and source quotations are untouched.
    """
    first, second = requested.model_copy(deep=True), declared.model_copy(deep=True)
    owners = {_literal(value) for value in owner_test_labels if value}
    # A method-specific declaration must not become a general declaration
    # across several requested procedures. Only one unambiguous route can use
    # its source method identifier as a redundant owner caption.
    if len(source.requested_methods) == 1:
        owners.add(_literal(source.requested_methods[0].code))
    actions = []
    for side, claim in (("requirement", first), ("declaration", second)):
        if not claim.source_context or claim.normalization_state == "unresolved":
            continue
        parts = [_literal(value) for value in re.split(r"[,;；，|]+", claim.source_context)]
        definition = resolve_definition(claim.parameter_id)
        if not parts or not all(
            value
            and (
                value in owners
                or _field_caption(value, definition)
                or _stage_caption(value, claim, source, source_side=side == "requirement")
            )
            for value in parts
        ):
            continue
        actions.append(
            {
                "side": side,
                "field": "source_context",
                "source": claim.source_context,
                "canonical": None,
                "rule": "selected_catalog_owner_caption",
                "caption_policy": "typed_field_phase_or_selected_owner_v2",
                "catalog_owner_labels": sorted(owners),
                "parameter_id": definition.id if definition else claim.parameter_id,
                "source_claim_id": claim.id,
                "source_values_preserved": True,
            }
        )
        claim.source_context = None
    return first, second, actions

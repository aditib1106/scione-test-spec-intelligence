"""Bound explicit AI wording proposals without changing source declarations.

Parameter identity alone does not prove category-value or context equivalence.
These optional proposals bind two existing strings. Hard contradictions and
missing input still defer to review; a language model remains fallible even when
these structural checks pass.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from scione.parameters import contexts_match, normalize_claim, resolve_definition
from scione.parameters.registry import known_category, normalize_category
from scione.parameters.units import canonical_unit_name
from scione.source_text import citation_contains
from scione.structured.models import AtomicClaim

if TYPE_CHECKING:
    from scione.matching.models import ClaimAlignment, ProposedEquivalence
    from scione.specifications.models import CustomerRequirement


class SemanticEquivalenceError(ValueError):
    """The proposed wording correspondence cannot apply to this source pair."""


@dataclass(frozen=True)
class SemanticPair:
    declaration: AtomicClaim
    applied: tuple[str, ...]


_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+./:#@-]*")
_CONTEXT_UNIT = re.compile(
    r"\s*(?P<unit>Δ\s*°[CF]|ΔK|°\s*[CF]|%\s*RH|"
    r"(?i:Celsius|Fahrenheit|kelvin|seconds?|minutes?|hours?|days?|inch(?:es)?|newtons?)|"
    r"摄氏度|华氏度|千克|牛顿|微米|毫米|厘米|分钟|小时|秒|米|克|"
    r"degrees?\s+[CF]|MPa|kPa|Pa|kN|kg|mg|µm|μm|mm|cm|nm|GU|"
    r"K|min|h|s|m|g|N|in|%)(?![A-Za-z])"
)
_NEGATIVE = re.compile(r"\b(?:not|no|without|except|excluding|sin)\b|不|无|未|除外", re.I)
_CONTEXT_AXES = (
    {"water": r"\bwater\b|试验用水|水", "air": r"\bair\b|空气", "oil": r"\boil\b|油"},
    {"steel": r"\bsteel\b|钢", "aluminium": r"\balumin(?:i)?um\b|铝"},
    {"inlet": r"\binlet\b|入口", "outlet": r"\boutlet\b|出口"},
    {"topcoat": r"\btop\s*coat\b|面漆", "primer": r"\bprimer\b|底漆"},
)


def joint_scope_text(claim: AtomicClaim) -> str | None:
    """A bounded, lossless wording view; never a model-produced source fact."""
    definition = resolve_definition(normalize_claim(claim).parameter_id)
    if (
        claim.operator not in {"text", "required"}
        or claim.normalization_state == "unresolved"
        or not claim.text_value
        or claim.kind not in {"condition", "scope"}
        or definition is None
        or definition.rule not in {"category", "required_set", "boolean_presence"}
        or any(
            value is not None
            for value in (
                claim.value,
                claim.minimum,
                claim.maximum,
                claim.tolerance,
            )
        )
        or claim.values
    ):
        return None
    text = json.dumps(
        {"value": claim.text_value, "context": claim.source_context},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return text if len(text) <= 2000 else None


def _scope_remainder(value: str, context: str) -> str:
    """Consume an explicit cross-field span, not an absent scientific qualifier."""
    # Exact text is intentionally stronger than a fuzzy language match here.
    # Translation remains possible for the remaining value, not an unlocated
    # context which an AI could have supplied from memory.
    if value.count(context) != 1:
        raise SemanticEquivalenceError(
            "The complete recorded context must occur once in the counterpart value."
        )
    remainder = value.replace(context, "", 1).strip(" ,;:")
    if not remainder:
        raise SemanticEquivalenceError("A joint scope needs a separately recorded value.")
    return remainder


def _source_owns_joint_context(requested, source):
    """A narrow quote may end exactly where its separately retained context starts.

    Both fields still need one contiguous, page-owned physical quotation. This
    does not borrow a qualifier elsewhere in the evidence or skip connecting
    words, intervening clauses, exceptions or another specimen's description.
    """
    context = requested.source_context
    if not context:
        return True
    if citation_contains(requested.raw_text, context):
        return True
    sequences = (
        f"{requested.raw_text} {context}",
        f"{context} {requested.raw_text}",
    )
    return bool(
        requested.source_pages
        and set(requested.source_pages)
        <= {
            evidence.page
            for evidence in source.evidence
            if any(citation_contains(evidence.quote, text) for text in sequences)
        }
    )


def _bind_joint_scope(proposal, requested, declared):
    """Bind original fields despite JSON presentation or quote-entity spelling.

    This is only transport parsing for the comparison view: duplicate/extra
    keys and any changed field value are rejected. The received strings and
    native source facts remain unchanged in the attempt artifact.
    """
    expected = (joint_scope_text(requested), joint_scope_text(declared))
    if not all(expected):
        raise SemanticEquivalenceError("Both original joint wording views must exist.")
    supplied = (proposal.requested_text, proposal.declared_text)
    if supplied == expected:
        return "literal_joint_views"
    if supplied == (requested.text_value, declared.text_value):
        # The separate code checks below still prove the entire context in
        # both original records; no omitted source field is inferred here.
        return "selected_values_with_source_owned_context"

    def unique_object(pairs):
        if len(dict(pairs)) != len(pairs):
            raise ValueError("Repeated joint-view field.")
        return dict(pairs)

    for original, target in zip(supplied, expected, strict=True):
        wanted = json.loads(target)
        matched = False
        # Some providers render JSON delimiters as typographic quotes or
        # omit them. This exact presentation projection is safe only when no
        # original data value contains a quote or structural field marker.
        # All scientific text, punctuation, codes and values must still match.
        plain_values = [v for v in wanted.values() if isinstance(v, str)]
        if not any(re.search(r'["«»“”{}]|(?:value|context)\s*:', value) for value in plain_values):
            displayed = re.sub(r'["«»“”]', "", original.replace("&quot;", '"'))
            if displayed == target.replace('"', ""):
                matched = True
        for transported in (original, original.replace("&quot;", '"')):
            try:
                decoded = json.loads(transported, object_pairs_hook=unique_object)
            except (ValueError, TypeError):
                continue
            if isinstance(decoded, dict) and decoded == wanted:
                matched = True
                break
        if not matched:
            raise SemanticEquivalenceError(
                "Joint wording evidence must retain every selected value and context exactly."
            )
    return "lossless_joint_view_transport"


def _joint_scope_pair(requested, declared, proposal, source):
    first, second = joint_scope_text(requested), joint_scope_text(declared)
    binding = _bind_joint_scope(proposal, requested, declared)
    if (
        source is None
        or not requested.source_pages
        or not set(requested.source_pages)
        <= {
            evidence.page
            for evidence in source.evidence
            if citation_contains(evidence.quote, requested.raw_text)
        }
    ):
        raise SemanticEquivalenceError("Joint scope needs the test's owned source quotation.")
    if bool(requested.source_context) == bool(declared.source_context):
        raise SemanticEquivalenceError(
            "Joint scope is only for an explicit context stored inside the opposite value."
        )
    # Positive descriptive values only. A negation, exception or conditional
    # demand cannot disappear into a new field boundary.
    for text in (first, second):
        if _NEGATIVE.search(text) or re.search(
            r"\b(?:if|unless|only|provided|conditional)\b", text, re.I
        ):
            raise SemanticEquivalenceError("Conditional or negative scope needs separate evidence.")
    left, right = requested.text_value, declared.text_value
    if requested.source_context:
        if not _source_owns_joint_context(requested, source):
            raise SemanticEquivalenceError("The proposed source context is not in its quotation.")
        right = _scope_remainder(right, requested.source_context)
    else:
        left = _scope_remainder(left, declared.source_context)
    if (
        requested.configuration != declared.configuration
        or requested.sequence != declared.sequence
        or not contexts_match(
            requested.model_copy(update={"source_context": None}),
            declared.model_copy(update={"source_context": None}),
        )
    ):
        raise SemanticEquivalenceError("Joint scope cannot change phase, setup or subject.")
    if _context_conflicts(left, right):
        raise SemanticEquivalenceError("Joint scope cannot change a scientific value or resource.")
    for axis in _CONTEXT_AXES:
        values = [
            {name for name, pattern in axis.items() if re.search(pattern, text, re.I)}
            for text in (left, right)
        ]
        if values[0] != values[1]:
            raise SemanticEquivalenceError("Joint scope cannot supply an unrecorded scope axis.")
    parameter = normalize_claim(requested).parameter_id or ""
    if (
        known_category(parameter, left)
        and known_category(parameter, right)
        and normalize_category(parameter, left) != normalize_category(parameter, right)
    ):
        raise SemanticEquivalenceError(
            "Distinct curated categories are not joint wording variants."
        )
    if (_identifier(left) or _identifier(right)) and left != right:
        raise SemanticEquivalenceError("A joint wording proposal cannot rename an identifier.")
    return declared.model_copy(
        deep=True,
        update={
            "text_value": requested.text_value,
            "source_context": requested.source_context,
        },
    ), binding


def _bind(proposal: ProposedEquivalence, requested: str | None, declared: str | None) -> None:
    if not requested or not declared:
        raise SemanticEquivalenceError("Both original wording values must be recorded.")
    if proposal.requested_text != requested or proposal.declared_text != declared:
        raise SemanticEquivalenceError(
            "Wording evidence must quote the selected input fields exactly."
        )


def _identifier(value: str) -> bool:
    """An unfamiliar model/code is not known to be case or punctuation insensitive."""
    return not re.search(r"\s", value) and bool(
        re.search(r"[\d_+./:#@-]", value) or re.fullmatch(r"[A-Z]{2,}", value)
    )


def _wording_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("−", "-")


def _codes(text: str) -> tuple[str, ...]:
    """Find exact embedded codes even next to Chinese text or prose labels."""
    result = []
    for match in _ASCII_TOKEN.finditer(_wording_text(text)):
        token = match.group().strip(".,;:!?")
        if "-" in token and not re.search(r"[\d_+./:#@]", token):
            parts = token.split("-")
            # Ordinary descriptors such as low-carbon or room-temperature are
            # prose. Short/code components still protect Polymer-A and XQ-a.
            if all(part.isalpha() and len(part) > 2 and not part.isupper() for part in parts):
                continue
        if token and not _NUMBER.fullmatch(token) and _identifier(token):
            result.append(token)
    return tuple(result)


def _quantities(text: str) -> tuple[tuple[Decimal, str], ...]:
    text = _wording_text(text)
    result = []
    for number in _NUMBER.finditer(text):
        unit = _CONTEXT_UNIT.match(text, number.end())
        if unit:
            spelling = re.sub(r"\s+", " ", unit["unit"])
            if re.fullmatch(r"degrees? [CF]", spelling):
                spelling = "°" + spelling[-1]
            elif re.fullmatch(r"Δ?° [CF]", spelling):
                spelling = spelling.replace(" ", "")
            result.append((Decimal(number.group()), canonical_unit_name(spelling) or spelling))
    return tuple(result)


def _context_conflicts(first: str, second: str) -> bool:
    # A phrase translation cannot rename an embedded model/setup/material code.
    if _codes(first) != _codes(second):
        return True
    if tuple(Decimal(m.group()) for m in _NUMBER.finditer(_wording_text(first))) != tuple(
        Decimal(m.group()) for m in _NUMBER.finditer(_wording_text(second))
    ):
        return True
    if _quantities(first) != _quantities(second):
        return True
    if bool(_NEGATIVE.search(first)) != bool(_NEGATIVE.search(second)):
        return True
    for axis in _CONTEXT_AXES:
        left = {name for name, pattern in axis.items() if re.search(pattern, first, re.I)}
        right = {name for name, pattern in axis.items() if re.search(pattern, second, re.I)}
        if left and right and left != right:
            return True
    return False


def semantic_pair(
    requested: AtomicClaim,
    declared: AtomicClaim,
    alignment: ClaimAlignment,
    *,
    source: CustomerRequirement | None = None,
) -> SemanticPair:
    """Return a comparison-only copy and an auditable list of wording bridges.

    Never infer missing facts, merge setups, change numbers/units, or equate
    unregistered codes. The original snapshot remains the source of UI values.
    """
    result = declared.model_copy(deep=True)
    applied = []
    if proposal := alignment.joint_scope_equivalence:
        result, binding = _joint_scope_pair(requested, declared, proposal, source)
        applied.append("source_owned_cross_field_wording")
        applied.append(binding)
    if proposal := alignment.context_equivalence:
        _bind(proposal, requested.source_context, declared.source_context)
        # Test stage, sequence, subject and setup separately, with only the free
        # wording removed. Source text/labels still supply explicit medium hints.
        if not contexts_match(
            requested.model_copy(update={"source_context": None}),
            declared.model_copy(update={"source_context": None}),
        ) or _context_conflicts(proposal.requested_text, proposal.declared_text):
            raise SemanticEquivalenceError(
                "Wording cannot override an explicit scope contradiction."
            )
        result.source_context = requested.source_context
        applied.append("context")
    if proposal := alignment.value_equivalence:
        _bind(proposal, requested.text_value, declared.text_value)
        if requested.operator not in {"text", "required"} or declared.operator not in {
            "text",
            "required",
        }:
            raise SemanticEquivalenceError("Value wording cannot replace numeric comparison.")
        if any(
            re.search(r"[;；\n]", value)
            for value in (proposal.requested_text, proposal.declared_text)
        ):
            raise SemanticEquivalenceError(
                "Set equivalence needs separately recorded member links."
            )
        normalized = normalize_claim(requested)
        definition = resolve_definition(normalized.parameter_id)
        if definition and definition.rule not in {"category", "required_set", "boolean_presence"}:
            raise SemanticEquivalenceError("This parameter requires a numeric comparison policy.")
        parameter = normalized.parameter_id or ""
        left, right = proposal.requested_text, proposal.declared_text
        curated = known_category(parameter, left) and known_category(parameter, right)
        if curated and normalize_category(parameter, left) != normalize_category(parameter, right):
            raise SemanticEquivalenceError("Distinct curated categories are not wording variants.")
        if not curated and (_identifier(left) or _identifier(right)) and left != right:
            raise SemanticEquivalenceError("Unregistered identifiers need exact declared identity.")
        if not curated and _context_conflicts(left, right):
            raise SemanticEquivalenceError(
                "Wording cannot override an explicit value or resource distinction."
            )
        if bool(_NEGATIVE.search(left)) != bool(_NEGATIVE.search(right)):
            raise SemanticEquivalenceError("Wording cannot erase a negative requirement.")
        result.text_value = requested.text_value
        applied.append("value")
    return SemanticPair(result, tuple(applied))

"""Bound identity candidates without treating retrieval as capability evidence.

Complete source method strings stay in the requirement. Candidate metadata is a
separate, versioned proposal boundary: a missing candidate is never proof that a
test is impossible or a method is equivalent to another one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from scione.catalogs.models import ReferenceCatalog
from scione.catalogs.normalization import normalized_name
from scione.reports.matching import _method_resolution, _parse_method
from scione.specifications.models import CustomerRequirement

RETRIEVAL_POLICY_VERSION = "bounded_identity_candidates_v2"
DEFAULT_TEST_LIMIT = 8
DEFAULT_METHOD_LIMIT = 48
DEFAULT_REQUEST_METHOD_LIMIT = 256
_TOKEN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.I)
_STOP = frozenset(
    {"the", "a", "an", "of", "for", "and", "or", "test", "testing", "method", "measurement"}
)


class RetrievalLimitError(ValueError):
    """A mandatory identity set cannot fit the explicit candidate budget."""


def _method_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    for dash in "‐‑‒–—−":
        text = text.replace(dash, "-")
    return re.sub(r"\s+", "", text).casefold()


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN.findall(normalized_name(value)) if token not in _STOP}


def _lexical_score(query: str, label: str) -> float:
    """Ranking only: fuzzy spelling and shared words never certify identity."""
    left, right = normalized_name(query), normalized_name(label)
    if not left or not right:
        return 0
    a, b = _tokens(left), _tokens(right)
    overlap = len(a & b) / len(a | b) if a and b else 0
    spelling = SequenceMatcher(None, left, right).ratio()
    # A short shared generic word is insufficient; typo similarity remains a
    # candidate signal, including for translated labels already in aliases.
    if not a & b and spelling < 0.6:
        return 0
    return round(max(overlap, spelling * 0.7), 6)


@dataclass(frozen=True)
class IdentityCandidates:
    requirement_id: str
    test_ids: tuple[str, ...]
    method_ids: tuple[str, ...]
    diagnostics: dict[str, Any]


def retrieve_identity_candidates(
    requirement: CustomerRequirement,
    catalog: ReferenceCatalog,
    *,
    preferred_test_ids: list[str] | tuple[str, ...] = (),
    pinned_test_id: str | None = None,
    human_reviewed: bool = False,
    test_limit: int = DEFAULT_TEST_LIMIT,
    method_limit: int = DEFAULT_METHOD_LIMIT,
) -> IdentityCandidates:
    """Keep exact evidence, then bounded lexical/rule alternatives.

    Human review pins its selected test (or explicitly unresolved identity).
    Exact method/name matches are mandatory candidates, never silently cut by
    top-k. Too many mandatory identities fail before inference. The method list
    retains exact source references even when a human selected another family.
    Broader source qualifiers, alternative roles and unknown editions remain in
    the requirement; retrieval cannot approve or rewrite them.
    """
    if test_limit < 1 or method_limit < 1:
        raise ValueError("Candidate limits must be positive.")
    tests = {test.id: test for test in catalog.tests}
    scores: dict[str, float] = {}
    signals: dict[str, list[str]] = {}
    mandatory: set[str] = set()
    method_ids: set[str] = set()
    reference_records: list[dict[str, Any]] = []

    def add(identifier: str, score: float, signal: str, *, required: bool = False) -> None:
        if identifier not in tests:
            return
        scores[identifier] = max(scores.get(identifier, 0), score)
        if signal not in signals.setdefault(identifier, []):
            signals[identifier].append(signal)
        if required:
            mandatory.add(identifier)

    for requested in requirement.requested_methods:
        key = _method_key(requested.code)
        exact = [
            method.id
            for method in catalog.methods
            if key
            in {
                _method_key(method.identifier),
                *(_method_key(alias.text) for alias in method.aliases),
            }
        ]
        state = "exact_identifier" if exact else "unresolved_reference"
        selected = exact
        if not selected:
            resolved, reason = _method_resolution(requested, catalog)
            if resolved is not None:
                selected = [resolved]
                state = "resolved_identifier" if reason is None else "identity_needs_review"
            elif (parsed := _parse_method(requested.code)) is not None:
                # Ambiguous editions of the same method are identity candidates,
                # not equivalent methods. Preserve every such candidate or fail
                # visibly if the method budget cannot hold them.
                selected = [
                    method.id
                    for method in catalog.methods
                    if any(
                        (other := _parse_method(value)) is not None and other.key == parsed.key
                        for value in [method.identifier, *(alias.text for alias in method.aliases)]
                    )
                ]
                if selected:
                    state = "identity_needs_review"
        method_ids.update(selected)
        reference_records.append(
            {"source": requested.code, "state": state, "method_ids": sorted(selected)}
        )
        for test in catalog.tests:
            if set(selected) & set(test.method_ids):
                add(
                    test.id,
                    120 if state in {"exact_identifier", "resolved_identifier"} else 95,
                    state,
                    required=True,
                )
    if len(method_ids) > method_limit:
        raise RetrievalLimitError(
            "The source refers to too many distinct catalog method candidates for one "
            "matching request. Review the method identities or split the request. "
            "No source reference was silently discarded."
        )

    name = normalized_name(requirement.original_test_name)
    output_labels = [
        claim.parameter_id.replace("_", " ")
        for claim in requirement.atomic_claims
        if claim.parameter_id
        and claim.kind in {"criterion", "reporting"}
        and claim.normalization_state != "unresolved"
    ]
    for test in catalog.tests:
        labels = [test.name, *(alias.text for alias in test.aliases)]
        if name in {normalized_name(label) for label in labels}:
            add(test.id, 110, "exact_curated_name_or_alias", required=True)
        else:
            lexical = max(_lexical_score(requirement.original_test_name, label) for label in labels)
            if lexical >= 0.3:
                add(test.id, lexical * 60, "lexical_name_candidate")
            output_score = max(
                (_lexical_score(value, label) for value in output_labels for label in labels),
                default=0,
            )
            if output_score >= 0.45:
                # Source-extracted quantity identities provide a language-neutral
                # ranking hint, not proof that test procedures are equivalent.
                add(test.id, output_score * 40, "source_output_parameter_candidate")
            if test.id.startswith("labtest_"):
                descriptions = [
                    test.description,
                    *(method.name for method in catalog.methods if method.id in test.method_ids),
                ]
                hint = max(
                    (_lexical_score(requirement.original_test_name, text) for text in descriptions),
                    default=0,
                )
                if hint >= 0.3:
                    add(test.id, hint * 35, "lab_private_description_candidate")
    for index, identifier in enumerate(preferred_test_ids):
        add(identifier, max(20, 70 - index * 0.1), "upstream_rule_candidate")

    if human_reviewed:
        chosen = (pinned_test_id,) if pinned_test_id in tests else ()
        mode = "human_pinned" if chosen else "human_unresolved_or_stale"
        if chosen:
            add(chosen[0], 200, "human_pinned")
    else:
        if len(mandatory) > test_limit:
            raise RetrievalLimitError(
                "Exact source identities exceed the matching candidate limit. Review the "
                "ambiguous catalog identities or split the request; no exact match was dropped."
            )
        ordered = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))
        # Fill after mandatory matches, not before them; upstream candidates
        # can never push an exact method or alias outside the retained window.
        required = sorted(mandatory, key=lambda identifier: (-scores[identifier], identifier))
        # Exact curated evidence already supplies an identity boundary. Weak
        # lexical neighbors need not fill empty slots or distract the model.
        # Conflicting exact names/methods are all retained above for review.
        chosen = tuple(required if required else ordered[:test_limit])
        mode = "bounded_ranked_candidates" if chosen else "no_catalog_candidate"
    chosen_set = set(chosen)
    ranked = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))
    diagnostics = {
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "requirement_id": requirement.id,
        "mode": mode,
        "test_limit": test_limit,
        "method_limit": method_limit,
        "catalog_test_count": len(catalog.tests),
        "catalog_method_count": len(catalog.methods),
        "candidate_test_count": len(chosen),
        "candidate_method_count": len(method_ids),
        "omitted_ranked_candidate_count": sum(i not in chosen_set for i in ranked),
        "candidates": [
            {"test_id": identifier, "score": scores[identifier], "signals": signals[identifier]}
            for identifier in chosen
        ],
        "method_references": reference_records,
        "metrics": {
            "precision": None,
            "recall": None,
            "note": "Not measured without independent reference identity labels.",
        },
        "limitations": [
            "Lexical retrieval does not establish semantic identity or capability.",
            "A missing candidate is not proof of absence; unknown matches remain reviewable.",
        ],
    }
    return IdentityCandidates(requirement.id, tuple(chosen), tuple(sorted(method_ids)), diagnostics)


def evaluate_candidate_recall(
    candidates: IdentityCandidates, expected_test_ids: set[str]
) -> dict[str, int | float | None]:
    """Evaluate retrieval only against explicitly supplied independent labels."""
    selected = set(candidates.test_ids)
    found = len(selected & expected_test_ids)
    return {
        "selected": len(selected),
        "expected": len(expected_test_ids),
        "found": found,
        "precision": found / len(selected) if selected else None,
        "recall": found / len(expected_test_ids) if expected_test_ids else None,
    }

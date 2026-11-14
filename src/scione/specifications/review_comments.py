"""Discard closed generic model commentary from row review, never as evidence.

The model occasionally writes an observation about its own handling of a summary
in the concern list. A strictly generic, unscoped positive sentence does not ask
the lab to make a decision. This classification does not establish that the
summary or extracted facts are consistent; all source/scientific checks still run.
Specific scientific assertions and concerns remain actionable.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from scione.ingestion.method_inventory import literal_method_designations
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import CustomerExtractionRun

MODEL_COMMENT_POLICY_VERSION = "generic_summary_comment_v1"
_PREFIX = r"(?:the )?(?:acceptance )?summary table"
_BODY = r"(?:the )?body (?:limits|requirements)"
_PATTERNS = (
    rf"{_PREFIX} repeats {_BODY}(?:; treated as consistent repeats)?[.]?",
    rf"{_PREFIX} contains consistent repeats of {_BODY}[.]?",
)


class UnusedModelCommentAudit(TypedDict):
    kind: Literal["unused_model_comment"]
    policy_version: str
    source_field: Literal["extraction.review_issues"]
    source_index: int
    original_comment: str
    used_as_evidence: Literal[False]
    row_blocking: Literal[False]
    classification: Literal["closed_generic_positive_summary_comment"]


def review_model_comment_audits(run: CustomerExtractionRun) -> list[UnusedModelCommentAudit]:
    """Return developer diagnostics without modifying the original model output.

    No numbers, units, method identifiers, property names, negation, uncertainty,
    conflict, row IDs or action requests fit the full closed grammar. The audit
    explicitly marks each comment as unused evidence, not a validated source fact.
    """
    audits = []
    for index, issue in enumerate(run.extraction.review_issues):
        if not issue.isascii():
            continue
        text = " ".join(issue.casefold().split())
        if not any(re.fullmatch(pattern, text) for pattern in _PATTERNS):
            continue
        audits.append(
            UnusedModelCommentAudit(
                kind="unused_model_comment",
                policy_version=MODEL_COMMENT_POLICY_VERSION,
                source_field="extraction.review_issues",
                source_index=index,
                original_comment=issue,
                used_as_evidence=False,
                row_blocking=False,
                classification="closed_generic_positive_summary_comment",
            )
        )
    return audits


_REFERENCE_COMMENT_WORDS = frozenset("""
a an the this its it in on of for to from and or but only solely directly itself
is are was were does do not name named names method methods body section sections
footer footnote note alignment test item bank relationship connection stated there s
cited cite reference references referenced recording requirement requirements appears
appear mentioned mention supplied provided extracted identifies identify identified
with as at that by requests request naming without
""".split())


def method_reference_comment_audits(
    run: CustomerExtractionRun, document: IngestedDocument
) -> list[dict]:
    """Keep a proved source reference as a notice despite its footer location.

    The model concern selects this audit; it never proves the method. An exact
    bank family/identifier association must occur in this row's physically
    verified evidence. Extra quantities, applicability or scientific conflict
    language cannot fit the closed location-comment grammar.
    """
    from scione.catalogs.bank import identifier_key, load_synthetic_method_bank

    if not any(re.search(r"\b(?:footer|footnote|bank\s+alignment)\b", issue, re.I)
               for issue in run.extraction.review_issues):
        return []
    bank = load_synthetic_method_bank()
    pages = {page.page_number: page.text for page in document.pages}
    audits = []
    for row in run.extraction.requirements:
        for method in row.requested_methods:
            entry = bank.lookup_identifier(method.code)
            if entry is None or identifier_key(entry.identifier) != identifier_key(method.code):
                continue
            heading = re.compile(
                re.escape(entry.family_id) + r"\s+" +
                r"\s+".join(re.escape(word) for word in entry.family_name.split()) +
                r"\s*:\s*" + r"\s*".join(re.escape(word) for word in method.code.split()) +
                r"(?![\w:-])", re.I,
            )
            evidence = [item for item in row.evidence
                        if item.page in pages and citation_contains(pages[item.page], item.quote)
                        and heading.search(item.quote)
                        and not re.search(
                            r"\b(?:withdrawn|obsolete|superseded|hypothetical|modified|adapted)\b|"
                            r"\b(?:not applicable|do not use|must not use|not assigned)\b",
                            item.quote, re.I,
                        )]
            if not evidence:
                continue
            for index, comment in enumerate(run.extraction.review_issues):
                codes = literal_method_designations(comment)
                if not codes or {identifier_key(code) for code in codes} != {
                    identifier_key(method.code)
                }:
                    continue
                named = set(re.findall(r"\breq_[A-Za-z0-9_]+\b", comment))
                if named and named != {row.id}:
                    continue
                text = re.sub(r"\breq_[A-Za-z0-9_]+\b", " ", comment)
                for code in codes:
                    text = re.sub(re.escape(code), " method ", text, flags=re.I)
                # A parenthetical owner caption must exactly retain the source
                # family and qualifier after removing its already-proved code.
                for part in re.findall(r"\(([^()]*)\)", text):
                    stripped = [re.sub(re.escape(method.code), " ", item.quote, flags=re.I)
                                for item in evidence]
                    def key(value):
                        return " ".join(re.findall(r"[\w-]+", value.casefold()))
                    if any(key(part) == key(value) for value in stripped):
                        text = text.replace("(" + part + ")", " ")
                text = re.sub(r"\bsections?\s+\d+(?:\s+and\s+\d+)?\b", " section ",
                              text, flags=re.I)
                words = set(re.findall(r"[^\W\d_]+", text.casefold()))
                family_words = set(re.findall(r"[^\W\d_]+", entry.family_name.casefold()))
                if (re.search(r"\d|[°%±≤≥<>=]", text)
                        or not words <= _REFERENCE_COMMENT_WORDS | family_words
                        or not re.search(r"\b(?:footer|footnote|bank\s+alignment)\b",
                                         text, re.I)):
                    continue
                audits.append({
                    "kind": "source_method_reference_notice",
                    "policy_version": "physical_method_reference_v1",
                    "requirement_id": row.id, "source_index": index,
                    "original_comment": comment, "row_blocking": False,
                    "used_as_evidence": False, "method_code": method.code,
                    "source_document_sha256": document.sha256,
                    "source_evidence": [item.model_dump(mode="json") for item in evidence],
                })
    return audits

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

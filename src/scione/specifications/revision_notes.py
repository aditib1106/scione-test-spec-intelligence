"""Keep one proved-consistent revision annotation out of every row's queue.

This is document-control triage, not acceptance of a synthetic-provenance
proposal containing scientific content. The original proposal/rejection stays
audited. Only a closed initial-gloss revision note with an independently located
equal body range can become an informational note; conflicts stay actionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from scione.numeric_literals import NUMBER, decimal_literal
from scione.parameters.registry import resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.named_scale_units import _literal_spans
from scione.structured.lexical_grounding import source_quantities

_NOTE = re.compile(
    rf"(?:SYNTHETIC\s+DEMO\s+)?(?:REVISION|REV[.]?|ISSUE)\s+"
    rf"(?P<revision>[A-Z](?:[A-Z0-9.-]{{0,10}})|\d[A-Z0-9.-]{{0,10}})\s*:\s*"
    rf"initial\s+gloss\s+target\s+is\s+(?P<low>{NUMBER})\s*[-–]\s*"
    rf"(?P<high>{NUMBER})\s*GU[.]\s*Other\s+requirements\s+unchanged[.]?",
    re.I,
)
_REVISION = re.compile(
    r"\b(?i:revision|rev[.]?|issue)\s+([A-Z](?:[A-Z0-9.-]{0,10})|\d[A-Z0-9.-]{0,10})\b"
)
_SOURCE_ERROR = (
    "Document notice needs review: Provenance notice contains other content that still "
    "requires review. "
)
_SAFE_ISSUE_WORDS = frozenset(
    """
a an the this these both all initial gloss target targets range mean value values
limit limits threshold body table summary note annotation revision demo synthetic
appears appeared stated stating states in on from and of as with is are was were
be been retained kept consistent agree agrees same repeated repeats repeated
document source its matches match unchanged other requirements per printed
""".split()
)
_RESTATEMENT = re.compile(
    r"The page-(?P<page>[1-9]\d*) note '(?P<quote>(?:SYNTHETIC DEMO )?"
    r"REVISION (?P<revision>[A-Z](?:[A-Z0-9.-]{0,10})|\d[A-Z0-9.-]{0,10}): "
    rf"initial gloss target is (?P<low>{NUMBER})\s*[-–]\s*(?P<high>{NUMBER}) GU)' "
    r"restates the gloss initial limit; no conflicting printed revision of the gloss limit "
    r"was found[.]",
    re.I,
)


@dataclass(frozen=True)
class RevisionNoteProjection:
    nonblocking_issues: frozenset[str]
    notices: dict[str, tuple[str, ...]]
    proofs: tuple[dict, ...]


def _physical_gu_ranges(document: IngestedDocument) -> list[tuple[Decimal, Decimal]]:
    ranges = []
    for page in document.pages:
        groups = {}
        for item in source_quantities(page.text, context=page.text):
            if item.unit == "GU" and item.role == "range_endpoint" and item.ambiguity is None:
                groups.setdefault(item.expression_span, []).append(item)
        for values in groups.values():
            if len(values) == 2 and all(item.value is not None for item in values):
                ranges.append(tuple(sorted(item.value for item in values)))
    return ranges


def _consistent_issue(
    issue: str, quote: str, revision: str, page: int, low: Decimal, high: Decimal
) -> bool:
    if not re.search(r"\b(?:consistent|agree|agrees|same|matches)\b", issue, re.I):
        return False
    if not re.search(r"\bgloss\b", issue, re.I) or not re.search(r"\brevision\b", issue, re.I):
        return False
    outside = issue
    for match in list(re.finditer(r"'([^']+)'|\"([^\"]+)\"|«([^»]+)»", issue))[::-1]:
        fragment = next(value for value in match.groups() if value)
        if not citation_contains(quote, fragment):
            return False
        outside = outside[: match.start()] + " " + outside[match.end() :]
    outside = re.sub(rf"\bpage\s+{page}\b", " ", outside, flags=re.I)
    outside = re.sub(rf"\brevision\s+{re.escape(revision)}\b", " revision ", outside, flags=re.I)
    expected = (low, high)
    matched_ranges = list(re.finditer(rf"(?P<lo>{NUMBER})\s*[-–]\s*(?P<hi>{NUMBER})\s*GU", outside))
    for match in reversed(matched_ranges):
        if (decimal_literal(match["lo"]), decimal_literal(match["hi"])) != expected:
            return False
        outside = outside[: match.start()] + " " + outside[match.end() :]
    if re.search(r"\d|[°%±≤≥<>]", outside):
        return False
    return set(re.findall(r"[A-Za-z]+", outside.casefold())) <= _SAFE_ISSUE_WORDS


def _restatement_issue(issue, quote, revision, page, low, high):
    match = _RESTATEMENT.fullmatch(issue)
    return bool(
        match
        and int(match["page"]) == page
        and match["revision"].upper() == revision
        and decimal_literal(match["low"]) == low
        and decimal_literal(match["high"]) == high
        and citation_contains(quote, match["quote"])
    )


def source_revision_notes(
    run: CustomerExtractionRun, document: IngestedDocument
) -> RevisionNoteProjection:
    """Reclassify only closed selectors with independent physical body proof."""
    ignored = set()
    notices = {}
    proofs = []
    pages = {page.page_number: page.text for page in document.pages}
    candidates = [
        (audit.proposal.page, audit.proposal.quote, audit.status, "typed_notice_proposal")
        for audit in run.notice_audits
        if audit.status == "needs_review"
    ]
    # A model restatement is not provenance. Select a separate, physically
    # printed closed annotation; never create or accept a synthetic notice.
    if any(_RESTATEMENT.fullmatch(issue) for issue in run.extraction.review_issues):
        candidates.extend(
            (page.page_number, match[0], None, "physical_annotation")
            for page in document.pages
            for match in _NOTE.finditer(page.text)
        )
    seen = set()
    for source_page, source_quote, notice_status, candidate_kind in candidates:
        key = (source_page, source_quote)
        if key in seen:
            continue
        seen.add(key)
        found = _NOTE.fullmatch(source_quote.strip())
        if not found or source_page not in pages:
            continue
        quote_spans = _literal_spans(pages[source_page], source_quote)
        if len(quote_spans) != 1:
            continue
        before, after = quote_spans[0]
        if pages[source_page][:before].rsplit("\n", 1)[-1].strip() or (
            pages[source_page][after:].split("\n", 1)[0].strip()
        ):
            continue
        revision = found["revision"].upper()
        revisions = {
            match[1].upper() for page in document.pages for match in _REVISION.finditer(page.text)
        }
        metadata = " ".join(
            filter(None, (run.extraction.document.revision, run.extraction.document.identifier))
        )
        meta_revisions = {match[1].upper() for match in _REVISION.finditer(metadata)}
        bare_revision = run.extraction.document.revision
        if bare_revision and re.fullmatch(
            r"[A-Z](?:[A-Z0-9.-]{0,10})|\d[A-Z0-9.-]{0,10}", bare_revision, re.I
        ):
            meta_revisions.add(bare_revision.upper())
        elif bare_revision and not _REVISION.search(bare_revision):
            continue  # Unparsed extracted revision text is not a verified revision.
        if revisions != {revision} or (meta_revisions and meta_revisions != {revision}):
            continue
        low, high = decimal_literal(found["low"]), decimal_literal(found["high"])
        if low > high:
            continue
        ranges = _physical_gu_ranges(document)
        if len(ranges) < 2 or any(pair != (low, high) for pair in ranges):
            continue
        numeric_gloss = [
            claim
            for requirement in run.extraction.requirements
            for claim in requirement.atomic_claims
            if (definition := resolve_definition(claim.parameter_id))
            and definition.id == "gloss"
            and claim.operator not in {"text", "required"}
        ]
        if len(numeric_gloss) != 1:
            continue
        owners = []
        for requirement in run.extraction.requirements:
            for claim in requirement.atomic_claims:
                definition = resolve_definition(claim.parameter_id)
                if not (
                    definition
                    and definition.id == "gloss"
                    and claim.kind == "criterion"
                    and claim.operator == "range"
                    and claim.minimum == low
                    and claim.maximum == high
                    and claim.value is None
                    and claim.tolerance is None
                    and not claim.values
                    and claim.text_value is None
                    and canonical_unit_name(claim.unit) == "GU"
                    and claim.stage in {"initial", "general", "measurement"}
                    and claim.configuration is None
                    and claim.sequence is None
                    and claim.normalization_state != "unresolved"
                    and claim.availability == "supported"
                    and (
                        not claim.source_context
                        or all(
                            citation_contains(claim.raw_text, part.strip())
                            for part in claim.source_context.split(";")
                        )
                    )
                    and re.search(
                        r"\bas delivered\b|\binitial\b|\bas received\b", claim.raw_text, re.I
                    )
                ):
                    continue
                locations = [
                    (page, span)
                    for page in document.pages
                    for span in _literal_spans(page.text, claim.raw_text)
                ]
                if len(locations) != 1:
                    continue
                page, span = locations[0]
                if page.page_number not in claim.source_pages or (
                    page.page_number == source_page
                    and span[0] < quote_spans[0][1]
                    and quote_spans[0][0] < span[1]
                ):
                    continue
                parent = any(
                    evidence.page == page.page_number
                    and citation_contains(evidence.quote, claim.raw_text)
                    and len(_literal_spans(page.text, evidence.quote)) == 1
                    and re.search(r"\b(?:specular[- ]?)?gloss\b", evidence.quote, re.I)
                    for evidence in requirement.evidence
                )
                if parent:
                    owners.append((requirement.id, claim.id, page.page_number, claim.raw_text))
        if len({owner[0] for owner in owners}) != 1:
            continue
        owner = owners[0][0]
        related = set()
        exact_error = _SOURCE_ERROR + source_quote
        if (
            candidate_kind == "typed_notice_proposal"
            and exact_error in run.extraction.review_issues
        ):
            related.add(exact_error)
        for issue in run.extraction.review_issues:
            if _consistent_issue(issue, source_quote, revision, source_page, low, high) or (
                _restatement_issue(issue, source_quote, revision, source_page, low, high)
            ):
                related.add(issue)
        if not related:
            continue
        ignored.update(related)
        notices[owner] = tuple(
            dict.fromkeys(
                (
                    *notices.get(owner, ()),
                    f"Revision {revision} repeats the initial gloss range {low}–{high} GU; "
                    "the body agrees.",
                )
            )
        )
        proofs.append(
            {
                "rule": "source_proved_consistent_revision_annotation",
                "revision": revision,
                "source_page": source_page,
                "source_quote": source_quote,
                "requirement_id": owner,
                "range": [str(low), str(high)],
                "unit": "GU",
                "independent_body_claim_ids": sorted({item[1] for item in owners}),
                "body_source_quotes": [item[3] for item in owners],
                "original_notice_status": notice_status,
                "selector_kind": candidate_kind,
                "model_comment_used_as_evidence": False,
                "original_issues_preserved": sorted(related),
            }
        )
    return RevisionNoteProjection(frozenset(ignored), notices, tuple(proofs))

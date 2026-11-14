"""Conservative source-only enrichment of already extracted stage citations.

This never labels a new stage or changes a quantity. It restores a directly
adjacent source heading omitted from model evidence, with an explicit audit
action. Uncertain source associations remain unchanged for human review.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from scione.parameters.registry import normalize_stage
from scione.schemas import IngestedDocument
from scione.specifications.models import (
    CustomerExtraction,
    CustomerGroundingAction,
    RequirementEvidence,
)

_STAGE_HEADER = re.compile(
    r"Stage\s*:\s*(measurement|conditioning|curing|initial|exposure|"
    r"thermal[- _]cycle|after[- _]cycle|cycle[- _]recovery)\s*[.;]?",
    re.I,
)


def enrich_stage_citations(
    extraction: CustomerExtraction, document: IngestedDocument
) -> tuple[CustomerExtraction, list[CustomerGroundingAction]]:
    """Attach a matching, immediately preceding stage heading for a unique quote.

    Only literal words and wrapping whitespace are matched. A quote repeated on
    any source page, spanning physical lines, or separated from a heading by any
    intervening line is deliberately ineligible. The provider output and the
    supplied extraction object remain untouched.
    """
    enriched = extraction.model_copy(deep=True)
    actions: dict[tuple[str, int, int, str], CustomerGroundingAction] = {}
    for requirement in enriched.requirements:
        for claim in requirement.atomic_claims:
            words = claim.raw_text.split()
            if not words:
                continue
            # Lookahead also counts overlapping appearances as ambiguous.
            first = r"(?<!\w)" if words[0][0].isalnum() else ""
            last = r"(?!\w)" if words[-1][-1].isalnum() else ""
            expression = re.compile(
                r"(?=(" + first + r"\s+".join(re.escape(w) for w in words) + last + r"))"
            )
            locations = [
                (page, match.start(1), match.end(1))
                for page in document.pages
                for match in expression.finditer(page.text)
            ]
            if len(locations) != 1:
                continue
            page, start, end = locations[0]
            if page.page_number not in claim.source_pages:
                continue
            offset = 0
            for index, line in enumerate(page.text.splitlines(keepends=True)):
                content = line.rstrip("\r\n")
                if offset <= start and end <= offset + len(content):
                    source_lines = page.text.splitlines()
                    if index == 0:
                        break
                    header = source_lines[index - 1].strip()
                    found = _STAGE_HEADER.fullmatch(header)
                    if not found:
                        break
                    source_stage, emitted_stage = normalize_stage(found[1]), normalize_stage(
                        claim.stage
                    )
                    if source_stage != emitted_stage:
                        # Quality classification is a local guard, not a new
                        # stage/value. Preserve the contradictory machine fact
                        # for inspection while preventing capability support.
                        claim.normalization_state = "unresolved"
                        claim.availability = "unknown"
                    identity = (requirement.id, page.page_number, index, emitted_stage)
                    if identity in actions:
                        if claim.id not in actions[identity].claim_ids:
                            actions[identity].claim_ids.append(claim.id)
                        break
                    if source_stage != emitted_stage:
                        actions[identity] = CustomerGroundingAction(
                            action="stage_conflict",
                            requirement_id=requirement.id,
                            claim_ids=[claim.id],
                            page=page.page_number,
                            source_line=index,
                            quote=header,
                            stage=emitted_stage,
                            source_stage=source_stage,
                        )
                        issue = (
                            f"{requirement.id}: extracted atom {claim.id} stage '{claim.stage}' "
                            f"conflicts with the immediately preceding source header '{header}' "
                            f"on physical page {page.page_number}. Review the stage association."
                        )
                        if issue not in enriched.review_issues:
                            enriched.review_issues.append(issue)
                        break
                    # Store the adjacent physical source span as one citation;
                    # a detached page-level header could leak to another fact.
                    previous = page.text.splitlines(keepends=True)[index - 1]
                    citation = page.text[offset - len(previous) : offset + len(content)].strip()
                    try:
                        added_evidence = RequirementEvidence(page=page.page_number, quote=citation)
                    except ValidationError:
                        # The source line can be far longer than the atom quote.
                        # Skip the optional enrichment rather than truncate it
                        # or reject an otherwise valid extraction response.
                        break
                    if any(
                        item.page == page.page_number and item.quote == citation
                        for item in requirement.evidence
                    ):
                        break
                    requirement.evidence.append(added_evidence)
                    actions[identity] = CustomerGroundingAction(
                        requirement_id=requirement.id,
                        claim_ids=[claim.id],
                        page=page.page_number,
                        source_line=index,  # Previous line's one-based physical index.
                        quote=header,
                        citation=citation,
                        stage=emitted_stage,
                        source_stage=source_stage,
                    )
                    break
                offset += len(line)
    return enriched, list(actions.values())

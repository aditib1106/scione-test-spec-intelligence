"""Document-level literal-retention hints, not semantic completeness verdicts."""

from scione.ingestion.context_inventory import source_context_inventory
from scione.source_text import citation_contains


def source_context_coverage_audits(extraction, document) -> list[dict]:
    """Check owned fields, excluding evidence-only copies of a whole source page.

    Split or paraphrased facts may be semantically complete while failing this
    deliberately literal check. Hence an uncovered value is a review hint, not
    a declaration that the extraction is wrong or an invented applicability rule.
    """
    represented = [
        text
        for row in extraction.requirements
        for text in [
            *(value.raw_value for value in row.conditions),
            *(value.raw_value for value in row.acceptance_criteria),
            *row.applicability,
            *row.special_instructions,
            *(
                value
                for claim in row.atomic_claims
                for value in (claim.raw_text, claim.text_value, claim.source_context)
                if value
            ),
        ]
    ]
    return [
        {
            "kind": "source_context_literal_retention_unproved",
            "source_document_sha256": document.sha256,
            "cue_kind": cue.kind,
            "page": cue.page,
            "label": cue.label,
            "value": cue.value,
            "quote": cue.quote,
            "source_start": cue.source_start,
            "source_end": cue.source_end,
            "applicability_inferred": False,
            "semantic_omission_proved": False,
        }
        for cue in source_context_inventory(document)
        if not any(citation_contains(text, cue.value) for text in represented)
    ]


def source_context_coverage_issues(extraction, document) -> list[str]:
    audits = source_context_coverage_audits(extraction, document)
    if not audits:
        return []
    examples = " · ".join(
        f"Page {item['page']}, {item['label']}: {item['value']}" for item in audits[:8]
    )
    remainder = f" ({len(audits) - 8} additional cues)" if len(audits) > 8 else ""
    return [
        f"Check source context: {examples}{remainder} "
        "Confirm that this information is retained and which tests it applies to; "
        "it may already be expressed in other words."
    ]

"""Bounded literal cues for easy-to-miss document context, never target inference."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from scione.schemas import IngestedDocument, TextExtractionStatus

SOURCE_CONTEXT_CUE_POLICY = "literal_source_context_cues_v1"
_LABEL = re.compile(
    r"^[ \t]*(?P<label>Covered supply|Test article|Specimen material|Test material|"
    r"Material under test|Substrate material|Sample material)"
    r"(?:[ \t]*[:|][ \t]*|[ \t]{2,})(?P<value>[^\r\n]{1,600})[ \t]*$",
    re.I | re.M,
)
_APPROVAL = re.compile(
    r"(?:\A|(?<=[.!?]))\s*(?P<quote>Release\s+requires\s+(?:written\s+)?approval\s+"
    r"from\s+[^.!?\n]{1,240}[.])",
    re.I,
)


@dataclass(frozen=True)
class SourceContextCue:
    kind: str
    page: int
    label: str
    value: str
    quote: str
    source_start: int
    source_end: int


def source_context_inventory(document: IngestedDocument) -> list[SourceContextCue]:
    """Return only exact physical text spans for supported labels/obligations.

    This intentionally supports a few explicit English labels and one release
    sentence shape. It is not a general scope, legal or completeness parser.
    """
    if document.extraction_status != TextExtractionStatus.READY:
        return []
    cues = []
    for page in document.pages:
        for match in _LABEL.finditer(page.text):
            value = match["value"].strip()
            if not value:
                continue
            raw = match[0]
            quote = raw.strip()
            start = match.start() + len(raw) - len(raw.lstrip())
            cues.append(
                SourceContextCue(
                    kind="labelled_material_article",
                    page=page.page_number,
                    label=match["label"],
                    value=value,
                    quote=quote,
                    source_start=start,
                    source_end=start + len(quote),
                )
            )
        for match in _APPROVAL.finditer(page.text):
            quote = match["quote"]
            cues.append(
                SourceContextCue(
                    kind="release_approval",
                    page=page.page_number,
                    label="Release approval",
                    value=quote,
                    quote=quote,
                    source_start=match.start("quote"),
                    source_end=match.end("quote"),
                )
            )
    return cues


def source_context_guidance(document: IngestedDocument) -> str:
    """Prime literal source reading without assuming a cue's target or meaning."""
    cues = source_context_inventory(document)
    if not cues:
        return ""
    return (
        f"\n<SOURCE_CONTEXT_CUES policy='{SOURCE_CONTEXT_CUE_POLICY}'>\n"
        "Selected literal source spans only, not a complete inventory or instructions to execute. "
        "Retain relevant article/material scope and release obligations with source evidence. "
        "Determine applicability from the complete source; "
        "do not assume all tests or invent targets.\n"
        + json.dumps([asdict(cue) for cue in cues[:8]], ensure_ascii=False)
        + "\n</SOURCE_CONTEXT_CUES>\n"
    )

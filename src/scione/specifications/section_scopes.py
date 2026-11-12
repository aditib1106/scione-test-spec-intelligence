"""Prove narrow section-local clause ownership on detached comparison inputs.

This does not infer the scope of arbitrary prose. Only uniquely located numbered
section introductions, before a physical table, and literal table-row identities
can remove a demonstrably inherited clause from a different section's view.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass

import pymupdf

from scione.schemas import IngestedDocument
from scione.specifications.models import CustomerRequirement
from scione.specifications.pdf_preview import (
    _detected_geometric_table_rows,
    pdf_quote_tokens,
)
from scione.specifications.source_chunking import _section_source_spans

SECTION_SCOPE_POLICY = "literal_section_scope_v1"
_NONLOCAL = re.compile(
    r"\b(?:all|every|each|any)\s+(?:tests?|requirements?|sections?|methods?)\b|"
    r"\bdocument[\s-]+wide\b|\b(?:unless|except|excepting|otherwise|however|"
    r"notwithstanding|excluding|throughout)\b|"
    r"\b(?:entire|whole)\s+(?:document|standard)\b|"
    r"(?:所有|全部|任何|各项)测试|全文|整个文档|除非|除外|"
    r"\btous les essais\b|\btous les tests\b|\bsauf\b|\balle prüfungen\b", re.I,
)
_REFERENCE = re.compile(r"\bsection\s+(\d+(?:\.\d+)+)\b", re.I)
_INDEX = re.compile(r"\b(?:index|contents)\b", re.I)
_GLOBAL_HEADING = re.compile(r"\b(?:general|common|shared|global)\b", re.I)


@dataclass(frozen=True)
class SectionIntro:
    section_id: str
    section_title: str
    page_number: int
    start: int
    end: int
    quote: str


def _canonical(value: str) -> str:
    return " ".join(value.split()).removesuffix(".").rstrip()


def _literal_locations(text: str, quote: str) -> list[tuple[int, int]]:
    """Whitespace-only search with offsets into the original string."""
    characters, offsets = [], []
    for match in re.finditer(r"\S+|\s+", text):
        piece = match[0]
        if piece.isspace():
            characters.append(" ")
            offsets.append(match.start())
        else:
            characters.extend(piece)
            offsets.extend(range(match.start(), match.end()))
    haystack, needle = "".join(characters), " ".join(quote.split())
    if not needle:
        return []
    result, cursor = [], 0
    while (at := haystack.find(needle, cursor)) >= 0:
        result.append((offsets[at], offsets[at + len(needle) - 1] + 1))
        cursor = at + 1
    return result


def _line_at(text: str, offset: int) -> str:
    return text[offset:].splitlines()[0].strip()


def _unique_line_rectangle(page, text: str):
    rectangles = page.search_for(text)
    return rectangles[0] if len(rectangles) == 1 else None


def _structure(document, pdf):
    ranges = _section_source_spans(document)
    sections = {}
    for identifier, spans in ranges.items():
        first = spans[0]
        heading = _line_at(document.pages[first.page_number - 1].text, first.start)
        title = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", heading)
        boundaries = {}
        for span in spans:
            original = document.pages[span.page_number - 1].text
            page = pdf[span.page_number - 1]
            lower, upper = 0.0, page.rect.height
            if span.start:
                rectangle = _unique_line_rectangle(page, _line_at(original, span.start))
                if rectangle is None:
                    continue
                lower = rectangle.y0
            if span.end < len(original):
                rectangle = _unique_line_rectangle(page, _line_at(original, span.end))
                if rectangle is None:
                    continue
                upper = rectangle.y0
            boundaries[span.page_number] = (lower, upper)
        sections[identifier] = {
            "title": title, "spans": spans, "heading": heading, "bounds": boundaries,
        }
    return sections


def _intros(document, pdf, sections):
    result = []
    for identifier, section in sections.items():
        if _GLOBAL_HEADING.search(section["title"]):
            continue
        first = section["spans"][0]
        page = pdf[first.page_number - 1]
        heading = _unique_line_rectangle(page, section["heading"])
        if heading is None or first.page_number not in section["bounds"]:
            continue
        _, upper = section["bounds"][first.page_number]
        try:
            tables = [table for table in page.find_tables().tables
                      if heading.y1 < table.bbox[1] < upper]
        except (RuntimeError, ValueError, TypeError, AttributeError):
            continue
        if not tables:
            continue
        text = page.get_text(
            "text", clip=pymupdf.Rect(0, heading.y1, page.rect.width,
                                      min(table.bbox[1] for table in tables)), sort=True,
        ).strip()
        for paragraph in re.split(r"\n\s*\n", text):
            quote = " ".join(paragraph.split())
            if not quote or _NONLOCAL.search(quote):
                continue
            if any(ref != identifier for ref in _REFERENCE.findall(quote)):
                continue
            locations = [
                (source.page_number, start, end)
                for source in document.pages
                for start, end in _literal_locations(source.text, quote)
            ]
            if len(locations) != 1 or locations[0][0] != first.page_number:
                continue
            number, start, end = locations[0]
            if not any(span.page_number == number and span.start <= start < end <= span.end
                       for span in section["spans"]):
                continue
            result.append(SectionIntro(
                identifier, section["title"], number, start, end,
                document.pages[number - 1].text[start:end],
            ))
    return tuple(result)


def _same_clause(value: str, intro: SectionIntro, *, annotation=False) -> bool:
    if _canonical(value) == _canonical(intro.quote):
        return True
    if not annotation:
        return False
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)\s*[.]?", value, re.S)
    if match is None or _canonical(match[1]) != _canonical(intro.quote):
        return False
    # Only a closed caption of this physically proven section is ignorable.
    # Units, conditions, other section IDs and arbitrary commentary cannot fit.
    title = re.escape(" ".join(intro.section_title.split()))
    return bool(re.fullmatch(
        rf"(?:{title}(?:\s+(?:tests?|measurements?))?\s*[,;:]?\s*)?"
        rf"section\s+{re.escape(intro.section_id)}", " ".join(match[2].split()), re.I,
    ))


def _row_owner(requirement, sections, rows):
    labels = pdf_quote_tokens(requirement.original_test_name)
    cited = {item.page for item in requirement.evidence}
    candidates = [(number, row) for number in cited for row in rows.get(number, ())
                  if row.cells and row.cells[0] == labels]
    exact = [(number, row) for number, row in candidates if any(
        evidence.page == number and pdf_quote_tokens(evidence.quote) == row.tokens
        for evidence in requirement.evidence
    )]
    selected = exact if exact else candidates
    if len(selected) != 1:
        return None
    number, row = selected[0]
    owners = [identifier for identifier, section in sections.items()
              if number in section["bounds"]
              and section["bounds"][number][0] <= row.bounds[1]
              and row.bounds[3] <= section["bounds"][number][1]]
    if not owners:
        return None
    depth = max(len(identifier.split(".")) for identifier in owners)
    owners = [identifier for identifier in owners if len(identifier.split(".")) == depth]
    if len(owners) != 1 or _INDEX.search(sections[owners[0]]["title"]):
        return None
    return owners[0], {
        "page_number": number, "bounds": list(row.bounds),
        "first_cell_tokens": list(row.cells[0]),
        "location_kind": "exact_complete_row" if exact else "unique_literal_first_cell",
    }


def _valid_source(document, pdf_bytes):
    return isinstance(pdf_bytes, bytes) and bool(pdf_bytes) and (
        hashlib.sha256(pdf_bytes).hexdigest() == document.sha256
    )


def source_section_intros(document: IngestedDocument, pdf_bytes: bytes) -> tuple[SectionIntro, ...]:
    """Expose only uniquely located local introductions for bounded source hints."""
    if not _valid_source(document, pdf_bytes):
        return ()
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if len(pdf) != document.page_count or pdf.needs_pass:
                return ()
            return _intros(document, pdf, _structure(document, pdf))
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return ()


def apply_section_scopes(
    requirements: list[CustomerRequirement], document: IngestedDocument, pdf_bytes: bytes,
) -> tuple[list[CustomerRequirement], list[dict]]:
    """Exclude only exactly proved foreign-section inheritance from detached rows.

    Same-section and descendant work retains the clause. Reviewed atoms remain
    unchanged with a conflict action. A condition without a matching parsed native
    atom is never excluded. Unknown/ambiguous source
    locations, global/exception language, paraphrases and unrelated fields stay
    untouched. Original machine output, reviewed facts and source evidence remain
    intact; actions retain the complete excluded fields and physical proof.
    """
    views = [row.model_copy(deep=True) for row in requirements]
    if not _valid_source(document, pdf_bytes):
        return views, []
    actions = []
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if len(pdf) != document.page_count or pdf.needs_pass:
                return views, []
            sections = _structure(document, pdf)
            intros = _intros(document, pdf, sections)
            cited = {e.page for row in requirements for e in row.evidence}
            rows = {number: _detected_geometric_table_rows(pdf[number - 1])
                    for number in cited if 1 <= number <= len(pdf)}
            for view in views:
                owner = _row_owner(view, sections, rows)
                if owner is None:
                    continue
                section_id, proof = owner
                for intro in intros:
                    if (section_id == intro.section_id
                            or section_id.startswith(intro.section_id + ".")):
                        continue
                    # An actual source-body cross-reference can extend scope;
                    # a model's appended caption is not that source proof.
                    if any(
                        e.page == proof["page_number"]
                        and intro.section_id in _REFERENCE.findall(e.quote)
                        for e in view.evidence
                        if not _same_clause(e.quote, intro)
                    ):
                        continue
                    matches = [claim for claim in view.atomic_claims
                               if claim.kind in {"condition", "scope", "instruction"}
                               and set(claim.source_pages) == {intro.page_number}
                               and _same_clause(claim.raw_text, intro)]
                    reviewed = [claim for claim in matches
                                if claim.normalization_state == "reviewed"]
                    if reviewed:
                        actions.append({
                            "rule": SECTION_SCOPE_POLICY, "requirement_id": view.id,
                            "disposition": "retained_reviewed_scope_conflict",
                            "requires_review": True,
                            "reviewed_claim_ids": [claim.id for claim in reviewed],
                            "source_section": intro.section_id, "body_section": section_id,
                            "source_span": asdict(intro), "body_row_proof": proof,
                            "removed_atoms": [], "removed_fields": [],
                            "original_source_preserved": True, "establishes_lab_support": False,
                        })
                        continue
                    atoms = [claim for claim in matches if claim.normalization_state == "parsed"]
                    if not atoms:
                        # A manually entered condition has no machine inheritance
                        # proof merely because its wording matches this intro.
                        continue
                    fields = [(index, field) for index, field in enumerate(view.conditions)
                              if _same_clause(field.raw_value, intro, annotation=True)]
                    if not atoms and not fields:
                        continue
                    identifiers = {claim.id for claim in atoms}
                    indexes = {index for index, _ in fields}
                    view.atomic_claims = [c for c in view.atomic_claims if c.id not in identifiers]
                    view.conditions = [c for i, c in enumerate(view.conditions) if i not in indexes]
                    actions.append({
                        "rule": SECTION_SCOPE_POLICY, "requirement_id": view.id,
                        "disposition": "excluded_model_inheritance", "requires_review": False,
                        "source_section": intro.section_id, "body_section": section_id,
                        "source_span": asdict(intro), "body_row_proof": proof,
                        "removed_atoms": [c.model_dump(mode="json") for c in atoms],
                        "removed_fields": [{"field": "conditions", "index": i,
                                            "value": f.model_dump(mode="json")} for i, f in fields],
                        "original_source_preserved": True, "establishes_lab_support": False,
                    })
    except (RuntimeError, ValueError, TypeError, AttributeError):
        # Never return a partially applied projection when physical proof fails.
        return [row.model_copy(deep=True) for row in requirements], []
    return views, actions

"""Bounded raw-source views with original page provenance and complete coverage.

This planner never summarizes, truncates or calls a model. A caller measures each
candidate's complete provider request (including prompt and schema), not merely
its source text. Shared-context detection is a conservative heading heuristic;
the receiving prompt must still determine the scope of each quoted clause.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from scione.schemas import IngestedDocument

SOURCE_CHUNK_POLICY = "shared_source_pages_v1"
MAX_SOURCE_CHUNKS = 8
DEFAULT_MAX_PRIMARY_CHARACTERS = 4_800
_NUMBERED_HEADING = re.compile(
    r"^\s*(?P<number>\d{1,2}(?:\.\d+)*)(?P<end>[.)])?\s+"
    r"(?P<title>\S.{0,155})\s*$"
)
_PAGE_FOOTER = re.compile(r"^\s*Page\s+\d+\s+(?:of|/)\s+\d+\s*$", re.I)
_SHARED_TITLE = re.compile(
    r"\b(?:general|common|shared|global)\b.{0,70}"
    r"\b(?:conditions?|requirements?|instructions?|preparation|scope|procedures?)\b"
    r"|\bsystem definition\b.{0,50}\btest conditions\b"
    r"|\b(?:regional|global)\s+annex\b"
    r"|\bmaterial compliance\b.{0,40}\bannex\b"
    r"|\bapproval\s+(?:types|requirements|procedures)\b"
    r"|^(?:scope|applicability)$",
    re.I,
)
_INDEX_TITLE = re.compile(r"\b(?:test item index|test index|table of contents)\b", re.I)
_UNNUMBERED_SHARED = re.compile(
    r"^\s*((?:general|common|shared|global)\s+(?:test\s+)?"
    r"(?:conditions?|requirements?|instructions?|preparation|scope|procedures?)"
    r"|scope|applicability|regional annex)\s*[:–—-]?\s*$",
    re.I,
)
_SECTION_REFERENCE = re.compile(r"\bSections?\s+(\d{1,2}(?:\.\d+)+)\b(?!\.\d)", re.I)


class SourceChunkPlanningError(ValueError):
    """A complete raw-source plan cannot fit the supplied request/call budget."""

    error_code = "input_too_large"


@dataclass(frozen=True)
class SourceContextSpan:
    """One unchanged fragment in an original physical page; offsets are half-open."""

    page_number: int
    start: int
    end: int


@dataclass(frozen=True)
class SourceChunk:
    """A transient source view; physical page IDs and document identity stay intact."""

    index: int
    primary_pages: tuple[int, ...]
    context_pages: tuple[int, ...]
    shared_context_pages: tuple[int, ...]
    document: IngestedDocument
    referenced_context_pages: tuple[int, ...] = ()
    context_source_spans: tuple[SourceContextSpan, ...] = ()
    section_context_pages: tuple[int, ...] = ()


def _section_source_spans(
    document: IngestedDocument,
) -> dict[str, tuple[SourceContextSpan, ...]]:
    """Index uniquely printed sections as unchanged original-page substrings.

    Numbered headings start a line and have a capitalized title. Bare quantities
    (``30 min``) are not headings. A new heading at the same or a higher level
    ends the section. Only the section's exact first/last-page spans are included;
    intervening continuation pages remain whole original pages.
    Repeated page furniture before the next heading is not section content.
    Duplicate section numbers are deliberately left unresolved.
    """
    counts = Counter(
        line.strip()
        for page in document.pages
        for line in set(page.text.splitlines())
        if line.strip()
    )
    furniture = {
        line for line, count in counts.items()
        if len(document.pages) > 1 and count >= max(2, len(document.pages) // 2)
    }
    headings: list[tuple[str, int, int, int]] = []
    for page_index, page in enumerate(document.pages):
        offset = 0
        for raw in page.text.splitlines(keepends=True):
            heading = _NUMBERED_HEADING.fullmatch(raw.strip())
            line_start, offset = offset, offset + len(raw)
            if heading is None:
                continue
            number, title = heading["number"], heading["title"]
            if "." not in number and not heading["end"]:
                continue
            if not title[0].isalpha() or title[0].islower():
                continue
            headings.append((number, len(number.split(".")), page_index, line_start))
    number_counts = Counter(number for number, *_ in headings)
    result = {}
    for index, (number, depth, page_index, start_offset) in enumerate(headings):
        if "." not in number or number_counts[number] != 1:
            continue
        end_page_index = len(document.pages) - 1
        end_offset = len(document.pages[end_page_index].text)
        for _, next_depth, next_page_index, next_offset in headings[index + 1:]:
            if next_depth > depth:
                continue
            prefix = document.pages[next_page_index].text[:next_offset].splitlines()
            has_continuation = any(
                line.strip() and line.strip() not in furniture
                and not _PAGE_FOOTER.fullmatch(line.strip())
                for line in prefix
            )
            end_page_index = max(
                page_index, next_page_index if has_continuation else next_page_index - 1,
            )
            end_offset = (
                next_offset if end_page_index == next_page_index
                else len(document.pages[end_page_index].text)
            )
            break
        spans = []
        for current in range(page_index, end_page_index + 1):
            page = document.pages[current]
            start = start_offset if current == page_index else 0
            end = end_offset if current == end_page_index else len(page.text)
            if end > start:
                spans.append(SourceContextSpan(page.page_number, start, end))
        result[number] = tuple(spans)
    return result


def _section_page_ranges(document: IngestedDocument) -> dict[str, tuple[int, ...]]:
    """Expose physical section ranges without claiming whole-page ownership."""
    return {
        number: tuple(span.page_number for span in spans)
        for number, spans in _section_source_spans(document).items()
    }


def _union_source_spans(spans: list[SourceContextSpan]) -> tuple[SourceContextSpan, ...]:
    merged: list[SourceContextSpan] = []
    for span in sorted(spans, key=lambda item: (item.page_number, item.start, item.end)):
        if (
            merged and merged[-1].page_number == span.page_number
            and span.start <= merged[-1].end
        ):
            previous = merged[-1]
            merged[-1] = SourceContextSpan(
                previous.page_number, previous.start, max(previous.end, span.end),
            )
        else:
            merged.append(span)
    return tuple(merged)


def shared_context_pages(document: IngestedDocument) -> tuple[int, ...]:
    """Locate raw general-condition/annex blocks and their page continuations.

    The title page is always retained as context. Explicit shared-section headings
    carry forward until the next major heading; a test index ends that carry.
    This is not a claim that every such clause applies to every extracted test.
    """
    if not document.pages:
        return ()
    line_counts = Counter(
        line.strip()
        for page in document.pages
        for line in set(page.text.splitlines())
        if line.strip()
    )
    # Repeated page furniture must not carry an old section onto a new section's
    # first page before its heading has been read.
    furniture = {
        line for line, count in line_counts.items()
        if len(document.pages) > 1 and count >= max(2, len(document.pages) // 2)
    }
    selected = {document.pages[0].page_number}
    active = False
    for page in document.pages:
        for raw in page.text.splitlines():
            line = raw.strip()
            if not line or _PAGE_FOOTER.fullmatch(line):
                continue
            heading = _NUMBERED_HEADING.fullmatch(line)
            if (
                heading and "." not in heading["number"] and not heading["end"]
                and not _SHARED_TITLE.search(heading["title"])
            ):
                # A bare quantity such as "7 days at room temperature" must
                # not terminate the shared section as though it were a heading.
                heading = None
            if line in furniture and heading is None and not _UNNUMBERED_SHARED.fullmatch(line):
                continue
            if heading:
                title = heading["title"]
                if _INDEX_TITLE.search(title):
                    active = False
                elif "." not in heading["number"]:
                    active = bool(_SHARED_TITLE.search(title))
                elif _SHARED_TITLE.search(title):
                    active = True
            elif _UNNUMBERED_SHARED.fullmatch(line):
                active = True
            if active:
                selected.add(page.page_number)
    return tuple(sorted(selected))


def plan_source_chunks(
    document: IngestedDocument,
    *,
    request_size: Callable[[SourceChunk], int],
    max_input_characters: int,
    max_calls: int = MAX_SOURCE_CHUNKS,
    max_primary_pages: int = 3,
    max_primary_characters: int | None = DEFAULT_MAX_PRIMARY_CHARACTERS,
    section_intro_spans: tuple[tuple[str, SourceContextSpan], ...] = (),
) -> tuple[SourceChunk, ...]:
    """Preflight a complete partition using actual complete-request sizes.

    Every original page is primary in exactly one chunk. Each request retains
    the raw shared-context pages, even if that forces a smaller primary range.
    Explicit ``Section x.y`` references on primary pages also retain the exact
    raw spans of a uniquely located section. This is one hop only: references in
    context pages do not spread into unrelated chunks or recursively add pages.
    Unknown/duplicate headings stay unresolved, with their source text intact.
    Optional caller-proved section introductions are mandatory context for any
    primary page intersecting that section, including its continuation pages.
    Their exact original spans are validated, never inferred from a title.
    Primary/shared pages stay whole. A referenced-only page remains a section
    fragment even when it is also an optional neighbor, so adjacency cannot add
    unrelated rows. Offsets identify every supplied fragment in the original page.
    Adjacent pages help continued tables when they fit; these optional neighbors
    are dropped before reducing the primary range. Primary and shared pages are
    never shortened; referenced-only views retain exact audited section spans.
    A single oversized page or mandatory shared block fails before any calls.

    A separate soft cap limits dense primary output work. Only non-shared primary
    text counts, since repeated shared context is not independent output work.
    An indivisible primary page may exceed this heuristic if its complete input
    still fits; this is not a guarantee that a provider's output limit is enough.
    """
    for name, value in (
        ("max_input_characters", max_input_characters),
        ("max_calls", max_calls),
        ("max_primary_pages", max_primary_pages),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer.")
    if max_primary_characters is not None and (
        isinstance(max_primary_characters, bool)
        or not isinstance(max_primary_characters, int)
        or max_primary_characters < 1
    ):
        raise ValueError("max_primary_characters must be a positive integer or None.")
    numbers = tuple(page.page_number for page in document.pages)
    if not numbers or numbers != tuple(range(1, document.page_count + 1)):
        raise SourceChunkPlanningError(
            "Source chunk planning requires every original physical page in order. "
            "No model call was made."
        )
    by_number = {page.page_number: page for page in document.pages}
    shared = shared_context_pages(document)
    shared_set = set(shared)
    section_spans = _section_source_spans(document)
    introductions = []
    seen_sections = set()
    for entry in section_intro_spans:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError(
                "section_intro_spans must contain (section ID, SourceContextSpan) pairs."
            )
        identifier, span = entry
        if not isinstance(identifier, str) or identifier not in section_spans:
            raise ValueError("section_intro_spans requires a uniquely located source section ID.")
        if identifier in seen_sections:
            raise ValueError("section_intro_spans must not repeat a section ID.")
        seen_sections.add(identifier)
        if not isinstance(span, SourceContextSpan) or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (span.page_number, span.start, span.end)
        ):
            raise ValueError(
                "section_intro_spans requires integer original-page character offsets."
            )
        if (
            span.page_number not in by_number
            or not 0 <= span.start < span.end <= len(by_number[span.page_number].text)
            or not any(owner.page_number == span.page_number
                       and owner.start <= span.start < span.end <= owner.end
                       for owner in section_spans[identifier])
        ):
            raise ValueError("section_intro_spans must stay inside its original source section.")
        introductions.append((identifier, span))

    def candidate(index: int, primary: tuple[int, ...], optional: tuple[int, ...]):
        reference_spans = _union_source_spans([
            span
            for number in primary
            for match in _SECTION_REFERENCE.finditer(by_number[number].text)
            for span in section_spans.get(match[1], ())
        ])
        referenced = {span.page_number for span in reference_spans}
        intro_spans = [span for identifier, span in introductions if any(
            owner.page_number in primary for owner in section_spans[identifier]
        )]
        section_context = {span.page_number for span in intro_spans}
        included = shared_set | set(primary) | set(optional) | referenced | section_context
        fragment_spans = tuple(
            span for span in _union_source_spans([*reference_spans, *intro_spans])
            if span.page_number not in shared_set and span.page_number not in primary
        )
        fragment_pages = {span.page_number for span in fragment_spans}
        context = tuple(
            number for number in numbers if number in included and number not in primary
        )
        pages = []
        for number in numbers:
            if number not in included:
                continue
            original = by_number[number]
            updates = {}
            if number in fragment_pages:
                text = "".join(
                    original.text[span.start:span.end]
                    for span in fragment_spans if span.page_number == number
                )
                updates = {
                    "text": text, "character_count": len(text),
                    "non_whitespace_character_count": sum(not char.isspace() for char in text),
                }
            pages.append(original.model_copy(update=updates, deep=True))
        view = document.model_copy(update={
            "pages": pages,
        })
        return SourceChunk(
            index=index,
            primary_pages=primary,
            context_pages=context,
            shared_context_pages=tuple(number for number in shared if number not in primary),
            document=view,
            referenced_context_pages=tuple(number for number in numbers
                                           if number in referenced and number not in primary),
            context_source_spans=fragment_spans,
            section_context_pages=tuple(number for number in numbers
                                        if number in section_context and number not in primary),
        )

    chunks: list[SourceChunk] = []
    at = 0
    while at < len(numbers):
        if len(chunks) >= max_calls:
            raise SourceChunkPlanningError(
                f"Complete source chunking would require more than {max_calls} model calls. "
                "No model call was made and no source pages were omitted."
            )
        accepted = None
        smallest_size = None
        for width in range(min(max_primary_pages, len(numbers) - at), 0, -1):
            primary = numbers[at:at + width]
            if (
                width > 1 and max_primary_characters is not None
                and sum(len(by_number[number].text) for number in primary
                        if number not in shared_set) > max_primary_characters
            ):
                continue
            before = (numbers[at - 1],) if at else ()
            after = (numbers[at + width],) if at + width < len(numbers) else ()
            # Deduplicate identical views when a neighbor is already shared.
            seen: set[tuple[int, ...]] = set()
            for neighbors in (before + after, before, after, ()):
                chunk = candidate(len(chunks) + 1, primary, neighbors)
                included = tuple(page.page_number for page in chunk.document.pages)
                if included in seen:
                    continue
                seen.add(included)
                size = request_size(chunk)
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise ValueError("request_size must return a nonnegative integer.")
                if width == 1 and not neighbors:
                    smallest_size = size
                if size <= max_input_characters:
                    accepted = chunk
                    break
            if accepted is not None:
                break
        if accepted is None:
            detail = f" ({smallest_size:,} input characters)" if smallest_size is not None else ""
            raise SourceChunkPlanningError(
                f"Physical page {numbers[at]} with required shared context and section references"
                f"{detail} exceeds the "
                f"{max_input_characters:,}-character complete-request budget. "
                "No model call was made; no page or shared clause was truncated."
            )
        chunks.append(accepted)
        at += len(accepted.primary_pages)
    return tuple(chunks)

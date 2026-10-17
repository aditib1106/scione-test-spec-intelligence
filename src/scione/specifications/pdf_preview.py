"""Render an uploaded PDF page locally, without browsers, OCR, or model calls."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

import pymupdf


class PDFPreviewError(RuntimeError):
    """A PDF page could not be rendered safely."""


Rectangle = tuple[float, float, float, float]


@dataclass(frozen=True)
class QuoteHighlight:
    """What can actually be located on the requested physical source page.

    A table quotation may add pipes between cells. Each cell must still match
    consecutive source tokens. Duplicate occurrences are deliberately not chosen
    arbitrarily, and an unmatched cell is never replaced with a fuzzy guess.
    """

    quote: str
    status: Literal["matched", "partial", "unmatched", "ambiguous"]
    rectangles: tuple[Rectangle, ...]
    matched_segments: int
    total_segments: int
    ambiguous_segments: int = 0


@dataclass(frozen=True)
class PDFHighlightResult:
    matches: tuple[QuoteHighlight, ...]

    @property
    def rectangles(self) -> tuple[Rectangle, ...]:
        return tuple(dict.fromkeys(rect for match in self.matches for rect in match.rectangles))

    @property
    def matched_count(self) -> int:
        return sum(match.status == "matched" for match in self.matches)

    @property
    def partial_count(self) -> int:
        return sum(match.status == "partial" for match in self.matches)

    @property
    def unmatched_count(self) -> int:
        return sum(match.status == "unmatched" for match in self.matches)

    @property
    def ambiguous_count(self) -> int:
        return sum(match.ambiguous_segments > 0 for match in self.matches)


@dataclass(frozen=True)
class _SourceWord:
    rectangle: Rectangle
    line: tuple[int, int]


# Preserve numerical signs and inequalities: normalization must not turn
# "<= 5" into "5", or a negative temperature into a positive one.
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[.,][0-9]+)?|[<>]=?|[=±°%+\-]", re.UNICODE)
_PUNCTUATION = str.maketrans(
    {
        "\u00ad": "",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "≤": "<=",
        "≥": ">=",
    }
)


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION).casefold()
    return _TOKEN_PATTERN.findall(normalized)


@contextmanager
def _open_page(data: bytes, page_number: int) -> Iterator[pymupdf.Page]:
    if not isinstance(data, bytes) or not data or len(data) > 25 * 1024 * 1024:
        raise PDFPreviewError("Preview requires a nonempty PDF of at most 25 MB.")
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise PDFPreviewError("Page number must be a 1-based integer.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or (pdf.metadata or {}).get("encryption"):
                raise PDFPreviewError("Encrypted PDFs cannot be previewed.")
            if not 1 <= page_number <= len(pdf):
                raise PDFPreviewError(f"Page number must be between 1 and {len(pdf)}.")
            yield pdf[page_number - 1]
    except PDFPreviewError:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise PDFPreviewError(f"Unable to render PDF page: {exc}") from exc


def _merge_word_rectangles(words: Sequence[_SourceWord]) -> tuple[Rectangle, ...]:
    lines: dict[tuple[int, int], set[Rectangle]] = defaultdict(set)
    for word in words:
        lines[word.line].add(word.rectangle)
    rectangles: list[Rectangle] = []
    for line_words in lines.values():
        current: list[float] | None = None
        for rectangle in sorted(line_words):
            x0, y0, x1, y1 = rectangle
            # Do not paint the empty space between table columns just because
            # the PDF groups the cells into a single text line.
            if current is not None and x0 - current[2] <= (y1 - y0) * 0.8:
                current = [
                    min(current[0], x0),
                    min(current[1], y0),
                    max(current[2], x1),
                    max(current[3], y1),
                ]
                rectangles[-1] = (current[0], current[1], current[2], current[3])
            else:
                current = list(rectangle)
                rectangles.append(rectangle)
    return tuple(rectangles)


def _inspect_page_highlights(page: pymupdf.Page, quotes: Sequence[str]) -> PDFHighlightResult:
    if isinstance(quotes, (str, bytes)) or len(quotes) > 100:
        raise PDFPreviewError("Highlight quotes must be a sequence of at most 100 strings.")
    if any(not isinstance(quote, str) or len(quote) > 8000 for quote in quotes):
        raise PDFPreviewError("Each highlight quote must be a string of at most 8000 characters.")

    values: list[str] = []
    source_words: list[_SourceWord] = []
    starts: dict[str, list[int]] = defaultdict(list)
    word_starts: set[int] = set()
    word_ends: set[int] = set()
    for x0, y0, x1, y1, value, block, line, _ in page.get_text("words"):
        word = _SourceWord((x0, y0, x1, y1), (block, line))
        word_tokens = _tokens(value)
        if word_tokens:
            word_starts.add(len(values))
            word_ends.add(len(values) + len(word_tokens))
        for token in word_tokens:
            starts[token].append(len(values))
            values.append(token)
            source_words.append(word)

    matches: list[QuoteHighlight] = []
    for quote in quotes:
        segments = [tokens for part in quote.split("|") if (tokens := _tokens(part))]
        selected: list[_SourceWord] = []
        matched_segments = ambiguous_segments = 0
        for needle in segments:
            occurrences: list[int] = []
            for start in starts.get(needle[0], []):
                end = start + len(needle)
                if start in word_starts and end in word_ends and values[start:end] == needle:
                    occurrences.append(start)
                    if len(occurrences) > 1:
                        break
            if len(occurrences) == 1:
                start = occurrences[0]
                selected.extend(source_words[start : start + len(needle)])
                matched_segments += 1
            elif len(occurrences) > 1:
                ambiguous_segments += 1

        if matched_segments and matched_segments == len(segments):
            status = "matched"
        elif matched_segments:
            status = "partial"
        elif ambiguous_segments:
            status = "ambiguous"
        else:
            status = "unmatched"
        matches.append(
            QuoteHighlight(
                quote=quote,
                status=status,
                rectangles=_merge_word_rectangles(selected),
                matched_segments=matched_segments,
                total_segments=len(segments),
                ambiguous_segments=ambiguous_segments,
            )
        )
    return PDFHighlightResult(tuple(matches))


def inspect_pdf_highlights(
    data: bytes,
    page_number: int = 1,
    quotes: Sequence[str] = (),
) -> PDFHighlightResult:
    """Locate evidence without altering the PDF or guessing approximate matches.

    Case, Unicode typography, whitespace, and table pipes are normalized. All
    remaining tokens must appear consecutively in the PDF's native text order.
    This is source location, not a claim that a model's extracted fact is correct.
    """

    with _open_page(data, page_number) as page:
        return _inspect_page_highlights(page, quotes)


def render_pdf_page(
    data: bytes,
    page_number: int = 1,
    scale: float = 1.3,
    *,
    max_dimension: int = 1500,
    highlight_quotes: Sequence[str] = (),
) -> bytes:
    """Return a bounded RGB PNG of a 1-based page; never write to disk."""

    if not math.isfinite(scale) or scale <= 0:
        raise PDFPreviewError("Preview scale must be positive and finite.")
    if not isinstance(max_dimension, int) or not 100 <= max_dimension <= 2000:
        raise PDFPreviewError("Maximum preview dimension must be between 100 and 2000 pixels.")
    with _open_page(data, page_number) as page:
        width, height = page.rect.width, page.rect.height
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise PDFPreviewError("PDF page has invalid dimensions.")
        highlights = _inspect_page_highlights(page, highlight_quotes)
        for rectangle in highlights.rectangles:
            annotation = page.add_highlight_annot(pymupdf.Rect(rectangle))
            annotation.set_colors(stroke=(1.0, 0.75, 0.1))
            annotation.set_opacity(0.45)
            annotation.update()
        bounded_scale = min(scale, max_dimension / max(width, height))
        return page.get_pixmap(
            matrix=pymupdf.Matrix(bounded_scale, bounded_scale),
            colorspace=pymupdf.csRGB,
            alpha=False,
        ).tobytes("png")

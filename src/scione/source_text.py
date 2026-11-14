"""Source-only citation matching with narrowly defined layout/typography tolerance.

Whitespace can change across CJK PDF line wrapping. Within lowercase French
elisions only, a typographic apostrophe U+2019 and ASCII apostrophe can match.
Other punctuation, identifier case, signs and digits remain exact. A citation
match does not establish that neighboring clauses or parameters are related.
"""

from __future__ import annotations

import re

CITATION_TEXT_POLICY_VERSION = "v2"

_WHITESPACE = re.compile(r"\s+")
_CJK_PUNCTUATION = frozenset("、。，；：！？「」『』（）［］｛｝【】〔〕〈〉《》〖〗〘〙〚〛“”‘’…‥")
_TOKEN = frozenset("_+-.−‐‑‒–—:/")
_FRENCH_ELISION = re.compile(
    r"(?<![\w\u0300-\u036f'’])"
    r"(?P<prefix>jusqu|lorsqu|puisqu|qu|l|d|j|t|c|n|s)"
    r"[’'](?P<word>[a-zàâäéèêëîïôöùûüÿçœæ]+)"
    r"(?![\w\u0300-\u036f'’])"
)
_SCIENTIFIC_WORDS = frozenset(
    {
        "mm",
        "cm",
        "km",
        "nm",
        "pa",
        "kpa",
        "mpa",
        "gpa",
        "kn",
        "kj",
        "hz",
        "khz",
        "ghz",
        "mol",
        "rad",
        "ppm",
        "ppb",
    }
)


def canonicalize_citation_apostrophes(text: str) -> str:
    """Fold U+2019 only in a lowercase French elision, without changing length.

    Whitelisted prefixes are l/d/j/t/c/n/s/qu/jusqu/lorsqu/puisqu. The following
    whole lexical word must contain lowercase French Latin letters only. Codes,
    uppercase tokens, digits, Greek letters, unsupported Latin letters, combining
    marks and scientific abbreviations do not receive this typography tolerance.
    This is not Unicode normalization, spelling correction or fuzzy matching.
    """

    def replace(match: re.Match[str]) -> str:
        if match["word"] in _SCIENTIFIC_WORDS:
            return match.group()
        return match["prefix"] + "'" + match["word"]

    return _FRENCH_ELISION.sub(replace, text)


def _cjk_flow(character: str) -> bool:
    code = ord(character)
    return (
        0x3400 <= code <= 0x4DBF  # Han extension A.
        or 0x4E00 <= code <= 0x9FFF  # Unified Han ideographs.
        or 0xF900 <= code <= 0xFAFF  # Compatibility Han, preserved without NFKC.
        or 0x20000 <= code <= 0x323AF  # Supplementary Han extensions.
        or 0x3040 <= code <= 0x309F  # Hiragana.
        or 0x30A0 <= code <= 0x30FF  # Katakana.
        or 0x31F0 <= code <= 0x31FF  # Katakana extensions.
        or 0xFF66 <= code <= 0xFF9F  # Half-width kana, also preserved exactly.
        or character in _CJK_PUNCTUATION
    )


def citation_text(text: str) -> str:
    """Apply citation v2 whitespace and narrow French apostrophe canonicalization.

    Mixed ``采用 D65 光源`` can match ``采用D65光源``. Spaces between Latin
    words, between digits, and inside Latin identifiers remain word boundaries.
    This function deliberately does not remove soft hyphens or zero-width marks
    that Python does not classify as whitespace, and never case-folds text.
    The only non-whitespace substitution is U+2019 to ASCII inside a whitelisted
    lowercase French elision. Source and model text are never rewritten in place.
    """
    pieces = []
    cursor = 0
    for match in _WHITESPACE.finditer(text):
        pieces.append(text[cursor : match.start()])
        if match.start() and match.end() < len(text):
            before = text[match.start() - 1]
            after = text[match.end()]
            if not (_cjk_flow(before) or _cjk_flow(after)):
                pieces.append(" ")
        cursor = match.end()
    pieces.append(text[cursor:])
    return canonicalize_citation_apostrophes("".join(pieces))


def _token_character(character: str) -> bool:
    # Avoid joining or matching part of a Latin/scientific identifier or a signed
    # number. CJK remains segmentable without an English-style word boundary.
    return character.isascii() and character.isalnum() or character in _TOKEN


def citation_contains(source: str, quote: str) -> bool:
    """Return a nonempty canonical citation match with Latin token boundaries.

    Citation canonicalization does not infer a table reading order, translate
    text, fix a typo, weaken a number or decide the meaning of a negation.
    """
    source_value = citation_text(source)
    quote_value = citation_text(quote)
    if not quote_value:
        return False
    start = source_value.find(quote_value)
    while start >= 0:
        end = start + len(quote_value)
        left_ok = (
            start == 0
            or not _token_character(quote_value[0])
            or not _token_character(source_value[start - 1])
        )
        right_ok = (
            end == len(source_value)
            or not _token_character(quote_value[-1])
            or not _token_character(source_value[end])
        )
        # A terminal period or introducing colon is punctuation; a following
        # digit/letter still makes it part of an identifier continuation.
        if (
            not right_ok
            and source_value[end] in {".", ":"}
            and (end + 1 == len(source_value) or not _token_character(source_value[end + 1]))
        ):
            right_ok = True
        if left_ok and right_ok:
            return True
        start = source_value.find(quote_value, start + 1)
    return False

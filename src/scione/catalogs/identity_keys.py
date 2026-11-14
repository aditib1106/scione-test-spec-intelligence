"""Formatting keys for explicit private procedure codes, within a lab's catalog."""

from __future__ import annotations

import re
import unicodedata

_DASHES = str.maketrans({character: "-" for character in "‐‑‒–—−"})
_STANDARD_PREFIX = re.compile(
    r"^(?:ISO|IEC|ASTM|DIN|EN|GB|SAE|VDMA|VDA|BS|ANSI|JIS|NF|UL|MIL|GOST|"
    r"AATCC|TAPPI|NACE|API|AS)(?=[\s_/:\d-]|[A-Z]\s*\d|$)",
    re.IGNORECASE,
)
_COMPACT_STANDARD = re.compile(
    r"^(?:ASTM\s*[A-Z]\s*\d|(?:DIN|BS|NF|EN)\s*(?:EN\s*)?"
    r"(?:(?:ISO|IEC)\s*)?\d)",
    re.IGNORECASE,
)
_REVISION = re.compile(
    r"(?:^|[ _-])(?:rev(?:ision)?|ver(?:sion)?|edition|issue|amend(?:ment)?|r|"
    r"modified|adapted|alternative|equivalent|deviation|annex|addendum)"
    r"(?=[ _.-]|\d|$)",
    re.IGNORECASE,
)
_PRIVATE_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[-_ ][a-z0-9]+)*$")


def private_method_key(value: str) -> str | None:
    """Fold formatting only for an explicitly recorded private alphanumeric code.

    Callers must use this within the current lab's private method identities and
    retain collisions as ambiguity. This does not resolve standard methods,
    editions, arbitrary procedure names, aliases, or method equivalence.
    """
    text = " ".join(unicodedata.normalize("NFKC", value).translate(_DASHES).split())
    if not text or len(text) > 160:
        return None
    if _STANDARD_PREFIX.match(text) or _COMPACT_STANDARD.match(text):
        return None
    if _REVISION.search(text):
        return None
    text = text.casefold()
    if re.search(r" [a-z]", text):
        return None
    text = re.sub(r"\s*([-_])\s*", r"\1", text)
    if not _PRIVATE_CODE.fullmatch(text) or not re.search(r"\d", text):
        return None
    return re.sub(r"(?<=[a-z])[-_ ](?=\d)", "", text)

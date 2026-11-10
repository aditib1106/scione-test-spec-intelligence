"""Literal method-designator checklist from the source, never inferred tests.

This is a non-exhaustive lexical inventory. A mention can be an alternative,
measurement aid or index entry; its role still comes from the surrounding source.
No standard content, catalog default or lab declaration is added here.
"""

import re

from scione.schemas import IngestedDocument

_DESIGNATOR = re.compile(
    r"(?<![A-Za-z0-9_])(?:DIN\s+EN\s+ISO|DIN\s+EN|DIN\s+ISO|EN\s+ISO|"
    r"ISO\s*/\s*IEC|GB\s*/\s*T|ISO|ASTM|SAE|VDMA|VDA|DIN|EN|GB)"
    r"\s*[A-Z]?\s*\d+(?:\s*[.-]\s*\d+)*"
    r"(?:\s*:\s*\d{4}(?:-\d{2})?)?(?![A-Za-z0-9_:-]|\.\d)",
    re.I,
)


def literal_method_designations(text: str) -> list[str]:
    """Return lexical source mentions, preserving their spelling and editions."""
    return [" ".join(match.group().split()) for match in _DESIGNATOR.finditer(text)]


def source_method_inventory(document: IngestedDocument) -> list[dict]:
    """Keep literal spelling/edition/parts and the physical pages of each mention."""
    found: dict[str, dict] = {}
    for page in document.pages:
        for code in literal_method_designations(page.text):
            key = re.sub(r"\s+", "", code).upper()
            entry = found.setdefault(key, {"code": code, "pages": []})
            if page.page_number not in entry["pages"]:
                entry["pages"].append(page.page_number)
    return list(found.values())

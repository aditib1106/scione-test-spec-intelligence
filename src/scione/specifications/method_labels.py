"""Source-backed parsing of exact synthetic-bank method-label conventions.

A successful parse removes only a known metadata heading or exact family-ID
suffix. It does not infer method equivalence, edition, applicability or lab support.
Unknown descriptions and approval/modification qualifiers fail closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from scione.catalogs.bank import MethodBankIndex, identifier_key, load_synthetic_method_bank
from scione.catalogs.models import ReferenceCatalog
from scione.schemas.documents import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import RequirementEvidence


@dataclass(frozen=True)
class MethodLabelProof:
    code: str
    raw_reference: str
    family_id: str
    family_name: str
    form: Literal["family_heading", "family_id_suffix"]
    bank_record_id: str | None
    page: int
    source_span: tuple[int, int]
    source_code_span: tuple[int, int]
    parent_evidence_index: int
    parent_evidence_span: tuple[int, int]


def _literal_pattern(text: str) -> str:
    """Only existing whitespace runs may change, never signs or punctuation."""
    return "".join(
        r"\s+" if part.isspace() else re.escape(part) for part in re.split(r"(\s+)", text)
    )


def _known_identity(
    code: str, family: str, name: str, bank: MethodBankIndex, catalog: ReferenceCatalog | None
) -> str | None:
    method = bank.lookup_identifier(code)
    if method is not None:
        return method.record_id if method.family_id == family else None
    if catalog is None:
        return None
    identities = [
        row for row in catalog.methods if identifier_key(row.identifier) == identifier_key(code)
    ]
    if len(identities) != 1:
        return None
    linked = [
        test for test in catalog.tests if test.name == name and identities[0].id in test.method_ids
    ]
    return "runtime:" + identities[0].id if len(linked) == 1 else None


def canonicalize_method_label(
    raw: str,
    requirement_evidence: list[RequirementEvidence],
    document: IngestedDocument,
    *,
    bank: MethodBankIndex | None = None,
    catalog: ReferenceCatalog | None = None,
) -> MethodLabelProof | None:
    """Return one literal, parent-owned bank-label parse, or ``None``.

    Allowed forms are ``FAMILY-ID Exact family name: COMPLETE_IDENTIFIER``,
    the same heading with `` - ``, or ``COMPLETE_IDENTIFIER (FAMILY-ID)``.
    Heading names are the bank's exact curated English names, not translations
    or free descriptions. A heading occupies its complete physical source line;
    trailing comments are never silently removed. A suffix is only an exact
    bank family ID belonging to that complete identifier, not generic parentheses.
    Offsets refer to unchanged physical page text, including any PDF line wraps.
    The original model response/reference must be retained by the caller.
    """
    if not raw.strip():
        return None
    bank = bank if bank is not None else load_synthetic_method_bank()
    value = raw.strip()
    parsed: tuple[str, str, str, tuple[int, int], str] | None = None
    for family, name in bank.family_names.items():
        heading = re.fullmatch(
            re.escape(family) + r"\s+" + _literal_pattern(name) + r"(?::\s*| - )(?P<code>.+)",
            value,
        )
        if heading:
            code = heading["code"]
            if _known_identity(code, family, name, bank, catalog) is not None:
                parsed = (family, name, code, heading.span("code"), "family_heading")
                break
        suffix = re.fullmatch(r"(?P<code>.+?)\s*\(" + re.escape(family) + r"\)", value)
        if suffix:
            code = suffix["code"].rstrip()
            # A suffix asserts a concrete bank family, so a runtime-only alias
            # is not enough to establish that exact metadata association.
            method = bank.lookup_identifier(code)
            if method is not None and method.family_id == family:
                start = suffix.start("code")
                parsed = (family, name, code, (start, start + len(code)), "family_id_suffix")
                break
    if parsed is None:
        return None
    family, name, code, code_span, form = parsed
    identity = _known_identity(code, family, name, bank, catalog)
    assert identity is not None
    before, after = value[: code_span[0]], value[code_span[1] :]
    pattern = re.compile(
        _literal_pattern(before)
        + "(?P<code>"
        + _literal_pattern(code)
        + ")"
        + _literal_pattern(after)
    )
    evidence_by_page: dict[int, list[tuple[int, RequirementEvidence]]] = {}
    for index, evidence in enumerate(requirement_evidence):
        evidence_by_page.setdefault(evidence.page, []).append((index, evidence))
    matches: dict[tuple[int, tuple[int, int]], MethodLabelProof] = {}
    for page in document.pages:
        for evidence_index, evidence in evidence_by_page.get(page.page_number, []):
            if not evidence.quote.strip():
                continue
            for parent in re.finditer(_literal_pattern(evidence.quote.strip()), page.text):
                for label in pattern.finditer(page.text, parent.start(), parent.end()):
                    if not citation_contains(
                        page.text[max(0, label.start() - 2) : label.end() + 2], value
                    ):
                        continue
                    if form == "family_id_suffix" and re.search(
                        r"\b(?:DIN|EN|BS|NF|ISO|IEC|ASTM|GB/T|GB|JIS)\s+$",
                        page.text[max(0, label.start() - 80) : label.start()],
                    ):
                        # An ISO fragment inside DIN EN ISO is not the complete
                        # source identifier. Adoption prefixes are not metadata.
                        continue
                    # The prefix is a heading, not a substring of 'modified X'.
                    # Do not trim trailing source qualifiers absent from model raw.
                    if form == "family_heading":
                        left = page.text.rfind("\n", 0, label.start()) + 1
                        right = page.text.find("\n", label.end())
                        right = len(page.text) if right < 0 else right
                        if (
                            page.text[left : label.start()].strip()
                            or page.text[label.end() : right].strip()
                        ):
                            continue
                    proof = MethodLabelProof(
                        code=page.text[slice(*label.span("code"))],
                        raw_reference=raw,
                        family_id=family,
                        family_name=name,
                        form=form,
                        bank_record_id=None if identity.startswith("runtime:") else identity,
                        page=page.page_number,
                        source_span=label.span(),
                        source_code_span=label.span("code"),
                        parent_evidence_index=evidence_index,
                        parent_evidence_span=parent.span(),
                    )
                    matches[(page.page_number, label.span())] = proof
    return next(iter(matches.values())) if len(matches) == 1 else None

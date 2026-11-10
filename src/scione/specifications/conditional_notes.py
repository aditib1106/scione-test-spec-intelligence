"""Separate one proved, inactive method-route caveat from source errors.

Only a future acceptance class for an approval-dependent alternative can become
a notice. This never selects that route, supplies its missing class, changes a
lab verdict, or suppresses numeric, scope or scientific concerns.
"""

from __future__ import annotations

import re
from hashlib import sha256

from pydantic import ValidationError

from scione.ingestion.method_inventory import literal_method_designations
from scione.providers.json_output import loads_exact
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import (
    CompactMethodRole,
    CustomerExtractionRun,
    CustomerRequirement,
)

_ROW_ID = re.compile(r"(?<![\w-])req_[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?![\w-])")
_OPTIONAL_ROLE = "optional customer approval required"
_WORDS = frozenset("""
a an the this that its it corresponding specific respective
route routes method methods alternative optional approval approved agreement
has have is are was were be been no not yet stated specified defined available
acceptance class classes criterion criteria limit limits threshold thresholds
customer buyer team will shall issue provide define assign supply set only after
before testing once until if when so therefore can recorded record
for of on in by to from and or based pending separately
""".split())


def _key(code: str) -> str:
    return re.sub(r"\s+", "", code).casefold()


def _literal(code: str) -> str:
    return r"\s*".join(re.escape(part) for part in code.split())


def _owned_quotes(row: CustomerRequirement, document: IngestedDocument) -> list[str]:
    pages = {page.page_number: page.text for page in document.pages}
    return [
        evidence.quote
        for evidence in row.evidence
        if evidence.page in pages and citation_contains(pages[evidence.page], evidence.quote)
    ]


def _role_proofs(
    run: CustomerExtractionRun, row: CustomerRequirement, document: IngestedDocument
) -> dict[str, str]:
    """Recheck original typed role proposals against this row's owned source."""
    pages = {page.page_number: page.text for page in document.pages}
    proposed: dict[str, set[str]] = {}
    for call in run.calls:
        if call.purpose != "extraction":
            continue
        try:
            payload = loads_exact(call.raw_response)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("requirements"), list):
            continue
        for index, emitted in enumerate(payload["requirements"], 1):
            if (
                not isinstance(emitted, dict)
                or row.id != f"req_{index:03d}"
                or emitted.get("test") != row.original_test_name
                or not isinstance(emitted.get("method_roles"), list)
            ):
                continue
            for item in emitted["method_roles"]:
                try:
                    role = CompactMethodRole.model_validate(item)
                except ValidationError:
                    continue
                code = _key(role.code)
                if (
                    role.page not in pages
                    or not citation_contains(pages[role.page], role.quote)
                    or not any(
                        evidence.page == role.page
                        and citation_contains(evidence.quote, role.quote)
                        and citation_contains(pages[role.page], evidence.quote)
                        for evidence in row.evidence
                    )
                    or code not in {
                        _key(value) for value in literal_method_designations(role.quote)
                    }
                ):
                    continue
                proposed.setdefault(code, set()).add(role.role)
    return {code: next(iter(roles)) for code, roles in proposed.items() if len(roles) == 1}


def _future_class_warning(issue: str, row_id: str, allowed_codes: set[str]) -> set[str]:
    named = set(_ROW_ID.findall(issue))
    if named and named != {row_id}:
        return set()
    references = literal_method_designations(issue)
    codes = {_key(code) for code in references}
    if not codes or not codes <= allowed_codes:
        return set()
    text = _ROW_ID.sub(" ", issue)
    families = set()
    for code in sorted(references, key=len, reverse=True):
        text = re.sub(_literal(code), " method ", text, flags=re.I)
        family = re.match(r"[A-Za-z]+", code)
        if family:
            families.add(family.group().casefold())
    # The allowed family reference describes the already-mentioned optional
    # route. No other numbers, units or comparison symbols may be hidden here.
    if re.search(r"\d|[°%±≤≥<>=]|\bclass\s+[A-Za-z]\b", text):
        return set()
    words = set(re.findall(r"[^\W\d_]+", text.casefold(), re.UNICODE))
    if not words <= _WORDS | families:
        return set()
    if not re.search(r"\bacceptance\s+(?:class|classes|criterion|criteria|limit)", text, re.I):
        return set()
    if not re.search(r"\b(?:no\s+(?:stated|specified|defined)|not\s+(?:yet\s+)?stated|pending)",
                     text, re.I):
        return set()
    if not re.search(
        r"\b(?:will|shall)\s+(?:issue|provide|define|assign|supply|set)\b"
        r"[^.;]{0,160}\bafter\s+(?:customer\s+)?approval\b", text, re.I,
    ):
        return set()
    remaining = re.sub(
        r"\bno\s+(?:stated|specified|defined)\s+acceptance\s+"
        r"(?:class|classes|criterion|criteria|limit)\b|"
        r"\bnot\s+(?:yet\s+)?(?:stated|specified|defined|available)\b",
        " ", text, flags=re.I,
    )
    for family in families:
        remaining = re.sub(
            rf"\bno\s+{re.escape(family)}[- ]based\s+acceptance\s+limit\s+"
            r"can\s+be\s+recorded\b", " ", remaining, flags=re.I,
        )
    if re.search(r"\b(?:no|not)\b", remaining, re.I):
        return set()
    return codes


def _reference(code: str, row: CustomerRequirement) -> str:
    """Use a family label only when it identifies exactly one method here."""
    family = re.match(r"[A-Za-z]+", code)
    if family and sum(
        bool(re.match(re.escape(family.group()) + r"\b", method.code, re.I))
        for method in row.requested_methods
    ) == 1:
        return rf"(?:{_literal(code)}|{re.escape(family.group())})"
    return _literal(code)


def _operative_search(pattern: str, quote: str) -> bool:
    """A withdrawn/example/negated clause cannot establish a current route."""
    boundaries = [match.end() for match in re.finditer(r"[;。!?]|\.(?=\s|$)", quote)]
    for match in re.finditer(pattern, quote, re.I):
        left = max([0, *(value for value in boundaries if value <= match.start())])
        right = min([len(quote), *(value for value in boundaries if value >= match.end())])
        clause = quote[left:right]
        if not re.search(
            r"\b(?:not|never|withdrawn|superseded|example|hypothetical|rejected|obsolete)\b|"
            r"\bno\s+longer\b|\b(?:mustn|can)['’]?t\b", clause, re.I,
        ):
            return True
    return False


def _source_proves_inactive_class(
    row: CustomerRequirement, optional_code: str, primary_code: str, quotes: list[str]
) -> bool:
    optional = _reference(optional_code, row)
    primary = _reference(primary_code, row)
    optional_request = any(_operative_search(
        rf"\b(?:may|can)\s+request\s+{_literal(optional_code)}(?![\w:-])", quote
    ) for quote in quotes)
    primary_request = any(_operative_search(
        rf"\b(?:should|must|shall)\s+(?:use|follow)\s+{_literal(primary_code)}(?![\w:-])",
        quote,
    )
        for quote in quotes)
    future_class = any(_operative_search(
        rf"\bif\s+(?:an?\s+|the\s+)?{optional}\s+(?:route|method)\s+is\s+approved\s*,?"
        r"\s*[^.;]{0,80}?\bwill\s+(?:issue|provide|assign)\b[^.;]{0,80}?"
        r"\bacceptance\s+class\b[^.;]{0,80}?\bbefore\s+testing\b", quote,
    ) for quote in quotes)
    # Do not unblock an incompletely extracted primary acceptance criterion.
    primary_class = any(
        citation_contains(quote, criterion.raw_value)
        and _operative_search(
            rf"\b{primary}\b[^.;]{{0,100}}\bclass\s+[0-9A-Z](?![a-z])", quote
        )
        and re.search(r"\bclass\s+[0-9A-Z](?![a-z])", criterion.raw_value, re.I)
        for criterion in row.acceptance_criteria
        for quote in quotes
    )
    return optional_request and primary_request and future_class and primary_class


def conditional_route_proofs(
    run: CustomerExtractionRun, document: IngestedDocument
) -> dict[str, dict]:
    """Prove inactive method routes without depending on model warning prose."""
    result = {}
    for row in run.extraction.requirements:
        primary = [method for method in row.requested_methods
                   if (method.role or "").strip().casefold() in {"primary", "required"}]
        optional = [method for method in row.requested_methods
                    if (method.role or "").strip().casefold() == _OPTIONAL_ROLE]
        if len(primary) != 1 or not optional:
            continue
        proofs = _role_proofs(run, row, document)
        if proofs.get(_key(primary[0].code)) not in {"primary", "required"}:
            continue
        allowed = {_key(method.code) for method in optional
                   if proofs.get(_key(method.code)) == "optional_approval_required"}
        if not allowed:
            continue
        quotes = _owned_quotes(row, document)
        proved = [method.code for method in optional if _key(method.code) in allowed
                  and _source_proves_inactive_class(row, method.code, primary[0].code, quotes)]
        if not proved:
            continue
        pages = {page.page_number: page.text for page in document.pages}
        result[row.id] = {
            "policy_version": "optional_route_source_v1",
            "primary_method_code": primary[0].code,
            "optional_method_codes": proved,
            "source_run_id": run.run_id,
            "source_document_id": document.document_id,
            "source_document_sha256": document.sha256,
            "source_requirement_sha256": sha256(row.model_dump_json().encode()).hexdigest(),
            "source_evidence": [
                {"page": evidence.page, "quote": evidence.quote}
                for evidence in row.evidence
                if evidence.page in pages
                and citation_contains(pages[evidence.page], evidence.quote)
            ],
        }
    return result


def conditional_source_notices(
    run: CustomerExtractionRun, document: IngestedDocument
) -> dict[str, tuple[str, ...]]:
    """Return original, source-proved warnings as notes for their owners only."""
    proofs = conditional_route_proofs(run, document)
    result = {}
    for row in run.extraction.requirements:
        if row.id not in proofs:
            continue
        allowed = {_key(code) for code in proofs[row.id]["optional_method_codes"]}
        notes = []
        for issue in run.extraction.review_issues:
            codes = _future_class_warning(issue, row.id, allowed)
            if codes:
                notes.append(issue)
        if notes:
            result[row.id] = tuple(dict.fromkeys(notes))
    return result

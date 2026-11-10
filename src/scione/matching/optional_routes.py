"""Preserve the boundary between an active primary and an unapproved route.

Inactive-route information needs a document-aware source proof captured by the
workspace. Selection of the alternative always requires its own issued criterion;
the primary route's class cannot supply it, regardless of an AI correspondence.
"""

from __future__ import annotations

import re
from hashlib import sha256

from scione.ingestion.method_inventory import literal_method_designations
from scione.matching.models import RequirementCandidates, SemanticMatchingRun
from scione.source_text import citation_contains
from scione.structured.models import AtomicClaim, ClaimCheck


def _key(code: str) -> str:
    return re.sub(r"\s+", "", code).casefold()


def _literal(code: str) -> str:
    return r"\s*".join(re.escape(word) for word in code.split())


def _selected_codes(run: SemanticMatchingRun, identifier: str) -> list[str] | None:
    review = run.review_snapshot or {}
    workspace = review.get("report_workspace", {})
    if not isinstance(workspace, dict):
        return None
    mappings = workspace.get("mappings", {})
    choice = mappings.get(identifier, {}) if isinstance(mappings, dict) else {}
    if not isinstance(choice, dict):
        return None
    codes = choice.get("selected_method_codes")
    return (
        codes if isinstance(codes, list) and all(isinstance(code, str) for code in codes) else None
    )


def _proof(run: SemanticMatchingRun, source: RequirementCandidates) -> dict | None:
    proofs = run.policy_snapshot.get("verified_optional_route_proofs", {})
    proof = proofs.get(source.requirement.id) if isinstance(proofs, dict) else None
    if (
        not isinstance(proof, dict)
        or proof.get("policy_version") != "optional_route_source_v1"
        or proof.get("source_run_id") != run.input_snapshot.extraction_run_id
        or proof.get("source_requirement_sha256")
        != sha256(source.requirement.model_dump_json().encode()).hexdigest()
        or not isinstance(proof.get("primary_method_code"), str)
        or not isinstance(proof.get("optional_method_codes"), list)
        or not proof["optional_method_codes"]
        or not all(isinstance(code, str) for code in proof["optional_method_codes"])
    ):
        return None
    methods = {
        _key(method.code): (method.role or "").casefold().strip()
        for method in source.requirement.requested_methods
    }
    primary = _key(proof["primary_method_code"])
    optional = {_key(code) for code in proof["optional_method_codes"]}
    if (
        methods.get(primary) not in {"primary", "required"}
        or primary in optional
        or any(methods.get(code) != "optional customer approval required" for code in optional)
    ):
        return None
    return proof


def _future_class(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:if\b[^.;]{0,100}\bapproved\b[^.;]{0,100})?"
            r"\bwill\s+(?:issue|provide|assign)\b[^.;]{0,100}\bacceptance\s+class\b"
            r"[^.;]{0,80}\bbefore\s+testing\b",
            text,
            re.I,
        )
    )


def _extra_quantity(text: str) -> bool:
    for code in literal_method_designations(text):
        text = re.sub(_literal(code), " ", text, flags=re.I)
    return bool(re.search(r"\d|[°%±≤≥<>=]", text))


def _closed_future(text: str, source: RequirementCandidates, optional: set[str]) -> bool:
    references = [
        _literal(method.code)
        for method in source.requirement.requested_methods
        if _key(method.code) in optional
    ]
    for method in source.requirement.requested_methods:
        family = re.match(r"[A-Za-z]+", method.code)
        if _key(method.code) in optional and family:
            references.append(re.escape(family.group()))
    return bool(references) and bool(
        re.fullmatch(
            r"if\s+(?:an?\s+|the\s+)?(?:" + "|".join(references) + r")\s+"
            r"(?:route|method)\s+is\s+approved\s*,?\s*"
            r"(?:our\s+team|the\s+customer|the\s+buyer)\s+will\s+(?:issue|provide|assign)\s+"
            r"(?:the\s+)?(?:corresponding\s+)?acceptance\s+class\s+before\s+testing\.?",
            " ".join(text.split()),
            re.I,
        )
    )


def _closed_request(text: str, source: RequirementCandidates, optional: set[str]) -> bool:
    # This closed request grammar contains only the inactive route and the
    # source's required identification/approval process. Added tools, materials
    # and operational constraints do not match and must remain assessable.
    return any(
        re.fullmatch(
            r"(?:A\s+(?:North\s+American|European|regional)\s+supplier|"
            r"(?:A|The)\s+(?:receiving\s+)?(?:lab|supplier))\s+may\s+request\s+"
            + _literal(method.code)
            + r"(?:\s+for\s+the\s+same\s+[A-Za-z]+(?:-[A-Za-z]+)*\s+question)?"
            r"(?:;\s*customer\s+approval\s+is\s+required|,\s*but\s+must\s+identify\s+"
            r"the\s+cutting\s+spacing,\s*tape,\s*scoring\s+and\s+rating\s+scale\s+"
            r"before\s+any\s+results\s+are\s+compared)\.?",
            " ".join(text.split()),
            re.I,
        )
        for method in source.requirement.requested_methods
        if _key(method.code) in optional
    )


def _owned(claim: AtomicClaim, source: RequirementCandidates) -> bool:
    return any(
        evidence.page in claim.source_pages and citation_contains(evidence.quote, claim.raw_text)
        for evidence in source.requirement.evidence
    )


def _future_targets_optional(text: str, source: RequirementCandidates, optional: set[str]) -> bool:
    references = {_key(code) for code in literal_method_designations(text)}
    if references:
        return references <= optional
    families = {}
    for method in source.requirement.requested_methods:
        family = re.match(r"[A-Za-z]+", method.code)
        if family:
            families.setdefault(family.group().casefold(), set()).add(_key(method.code))
    mentioned = [
        codes
        for family, codes in families.items()
        if re.search(rf"\b{re.escape(family)}\s+(?:route|method|procedure)\b", text, re.I)
    ]
    return bool(mentioned) and all(len(codes) == 1 and codes <= optional for codes in mentioned)


def _primary_class(claim: AtomicClaim, source: RequirementCandidates) -> bool:
    if claim.kind != "criterion":
        return False
    primary = [
        method
        for method in source.requirement.requested_methods
        if (method.role or "").casefold().strip() in {"primary", "required"}
    ]
    if len(primary) != 1:
        return False
    code = primary[0].code
    family = re.match(r"[A-Za-z]+", code)
    family_unique = (
        family
        and sum(
            bool(re.match(re.escape(family.group()) + r"\b", method.code, re.I))
            for method in source.requirement.requested_methods
        )
        == 1
    )
    reference = (
        rf"(?:{_literal(code)}|{re.escape(family.group())})" if family_unique else _literal(code)
    )
    return any(
        evidence.page in claim.source_pages
        and citation_contains(evidence.quote, claim.raw_text)
        and re.search(rf"\b{reference}\b[^.;]{{0,100}}\bclass\s+[0-9A-Z]", evidence.quote, re.I)
        for evidence in source.requirement.evidence
    )


def _check(claim: AtomicClaim, status: str, reason: str) -> ClaimCheck:
    return ClaimCheck(
        id=claim.id,
        parameter_id=claim.parameter_id,
        label=claim.label,
        kind=claim.kind,
        stage=claim.stage,
        unit=claim.unit,
        requested=claim.display_value,
        status=status,
        reason=reason,
        source_pages=list(claim.source_pages),
        requirement_claim=claim.model_copy(deep=True),
        comparison_basis="source_optional_route_policy",
        comparison_metadata=[{"rule": "primary_route_without_unapproved_alternative"}],
    )


def optional_route_check(
    run: SemanticMatchingRun, source: RequirementCandidates, claim: AtomicClaim
) -> ClaimCheck | None:
    """Return one explicit route note/unknown before generic workflow handling."""
    row = source.requirement
    selected = _selected_codes(run, row.id)
    optional_codes = {
        _key(method.code)
        for method in row.requested_methods
        if (method.role or "").casefold().strip() == "optional customer approval required"
    }
    active_optional = selected is not None and bool(
        {_key(code) for code in selected} & optional_codes
    )
    primary_codes = {
        _key(method.code)
        for method in row.requested_methods
        if (method.role or "").casefold().strip() in {"primary", "required"}
    }
    foreign_selection = selected is not None and bool(
        {_key(code) for code in selected} - primary_codes
    )
    future = _future_class(claim.raw_text)
    if (active_optional and future) or (foreign_selection and _primary_class(claim, source)):
        return _check(
            claim,
            "unknown",
            "The selected alternative needs its own customer-issued acceptance class. "
            "The primary method's class cannot be applied to this route.",
        )
    proof = _proof(run, source)
    optional_request = any(
        re.search(
            rf"\b(?:may|can)\s+request\s+{_literal(method.code)}(?![\w:-])", claim.raw_text, re.I
        )
        for method in row.requested_methods
        if _key(method.code) in optional_codes
    )
    if not future and not optional_request:
        return None
    numeric_payload = bool(claim.values) or any(
        value is not None
        for value in (
            claim.value,
            claim.minimum,
            claim.maximum,
            claim.tolerance,
        )
    )
    if (
        not proof
        or not _owned(claim, source)
        or _extra_quantity(claim.raw_text)
        or numeric_payload
        or claim.unit
        or claim.operator not in {"text", "required"}
    ):
        return _check(
            claim,
            "unknown",
            "The approval-dependent route needs a verified source decision. "
            "No missing acceptance class or method detail has been inferred.",
        )
    primary_key = _key(proof["primary_method_code"])
    current = {_key(code) for code in selected} if selected is not None else {primary_key}
    proved_optional = {_key(code) for code in proof["optional_method_codes"]}
    if future and not _future_targets_optional(claim.raw_text, source, proved_optional):
        return _check(
            claim,
            "unknown",
            "The future acceptance class is not proved to belong only to the inactive "
            "alternative. Confirm its method route before comparing it.",
        )
    if not (
        _closed_future(claim.raw_text, source, proved_optional)
        if future
        else _closed_request(claim.raw_text, source, proved_optional)
    ):
        return _check(
            claim,
            "unknown",
            "This clause includes more than the inactive method's approval or future class. "
            "Its additional restrictions remain to be assessed.",
        )
    all_methods = {_key(method.code) for method in row.requested_methods}
    if current != {primary_key} or all_methods != {primary_key, *proved_optional}:
        return _check(
            claim,
            "unknown",
            "Confirm the active method route and its customer-issued acceptance class "
            "before comparing these conditional requirements.",
        )
    return _check(
        claim,
        "informational",
        "This alternative is subject to customer approval and is not selected. "
        "Its future acceptance class is retained as a note; the primary route is assessed.",
    )

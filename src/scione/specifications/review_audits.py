"""Narrow source-association review cues, never inferred lab requirements.

These cues do not remove rows, rewrite atoms or certify unflagged content.
They identify an explicitly named test's specimens being assigned to another
test, using only literal source text and the read-only test identity catalog.
"""

from __future__ import annotations

import re

from scione.catalogs.normalization import normalized_name
from scione.catalogs.seed import load_reference_catalog
from scione.parameters.registry import resolve_definition
from scione.providers.json_output import loads_exact
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.inventory import shared_applicability_has_exception
from scione.specifications.models import (
    CompactCustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
)
from scione.specifications.named_scale_units import _literal_spans
from scione.specifications.wire_normalization import normalize_compact_containers

COMPOUND_REPORTING_PROBLEM_CODE = "compound_measurement_report_request"
_TOTAL_FILM_REQUEST = r"(?:the\s+)?total\s+(?:dry\s+)?film\s+(?:build|thickness)"
_LAYER_REQUEST = r"(?:the\s+)?(?:separate\s+|individual\s+)?(?:top[ -]?coat|layer)\s+thickness"
_COMPOUND_REPORT = re.compile(
    rf"(?:(?:record|measure|report|determine)\s+)?(?:"
    rf"{_TOTAL_FILM_REQUEST}\s+(?:and|&)\s+{_LAYER_REQUEST}|"
    rf"{_LAYER_REQUEST}\s+(?:and|&)\s+{_TOTAL_FILM_REQUEST})[.]?",
    re.I,
)


def compound_reporting_problem(claim) -> str | None:
    """One native identity cannot establish two distinct measured outputs.

    Only the requested target text is examined. Two correctly separated atoms
    may share the same compound source quotation and must remain valid. This
    does not split a request, infer a missing property or assess lab support.
    """
    definition = resolve_definition(claim.parameter_id)
    if not (
        definition
        and definition.id in {"film_thickness", "topcoat_thickness"}
        and definition.value_kind == "number"
        and "reporting" in definition.allowed_roles
        and claim.kind == "reporting"
        and claim.operator == "required"
        and claim.text_value
    ):
        return None
    if re.search(
        r"\b(?:informational|background|explanatory|alternative|either|instead|unless)\b|"
        r"\bnot\s+(?:requested|required|a\s+measurement)\b",
        claim.source_context or "",
        re.I,
    ):
        return None
    if not _COMPOUND_REPORT.fullmatch(" ".join(claim.text_value.split())):
        return None
    return (
        "One reporting entry combines total film and separate layer thickness. "
        "Check and separate these measurements before comparing lab support."
    )


def native_scientific_review_reason(requirement: CustomerRequirement) -> str | None:
    """Surface known native contradictions, not every unresolved parser field.

    A separately source-grounded fallback with the same scientific identity and
    typed payload may already supersede an incomplete native representation.
    Such software recovery does not require the user to approve parser details.
    Generic units, unfamiliar attributes and raw workflow prose are not scored
    as errors by this triage check.
    """
    from scione.structured.normalization import (
        known_source_fact_problem,
        normalize_requirement,
    )

    for claim in requirement.atomic_claims:
        if compound := compound_reporting_problem(claim):
            return compound

    problems = [
        claim
        for claim in requirement.atomic_claims
        if known_source_fact_problem(claim, requirement) is not None
    ]
    if not problems:
        return None
    normalized = normalize_requirement(requirement)
    scientific_fields = (
        "parameter_id",
        "kind",
        "stage",
        "operator",
        "value",
        "minimum",
        "maximum",
        "tolerance",
        "values",
        "unit",
        "text_value",
        "source_context",
        "configuration",
        "sequence",
    )
    unresolved = []
    for original in problems:
        recovered = any(
            "_field_" in candidate.id
            and candidate.normalization_state != "unresolved"
            and candidate.availability != "unknown"
            and known_source_fact_problem(candidate, requirement) is None
            and all(
                getattr(candidate, field) == getattr(original, field) for field in scientific_fields
            )
            and set(candidate.source_pages) == set(original.source_pages)
            and citation_contains(candidate.raw_text, original.raw_text)
            for candidate in normalized.claims
        )
        if not recovered:
            unresolved.append(original)
    if not unresolved:
        return None
    labels = list(dict.fromkeys(claim.label for claim in unresolved))[:3]
    return (
        "Check " + ", ".join(labels) + " against the PDF. "
        "Some extracted details describe a different quantity, unit, code or comparison."
    )


def has_explicit_measurement_request(
    requirement: CustomerRequirement, document: IngestedDocument
) -> bool:
    """A closed, source-located measurement request need not invent a pass limit.

    This handles plain registered numeric properties only. Extra qualifiers,
    numbers, custom names, omitted properties in a joint request and unlocated
    text keep the ordinary review cue. It is not a completeness certificate.
    """
    pages = {page.page_number: page.text for page in document.pages}
    candidates = []
    for claim in requirement.atomic_claims:
        definition = resolve_definition(claim.parameter_id)
        label = resolve_definition(claim.label)
        if not (
            claim.kind == "reporting"
            and claim.operator == "required"
            and definition
            and definition.value_kind == "number"
            and "reporting" in definition.allowed_roles
            and label
            and label.id == definition.id
            and claim.normalization_state != "unresolved"
            and claim.availability == "supported"
            and claim.value is None
            and claim.minimum is None
            and claim.maximum is None
            and claim.tolerance is None
            and not claim.values
            and claim.unit is None
            and claim.source_pages
            and all(
                page in pages and len(_literal_spans(pages[page], claim.raw_text)) == 1
                for page in claim.source_pages
            )
            and any(
                evidence.page in claim.source_pages
                and citation_contains(evidence.quote, claim.raw_text)
                for evidence in requirement.evidence
            )
        ):
            continue
        found = re.fullmatch(
            r"(?:Measure|Record|Report|Determine)\s+(.+?)[.]?", claim.raw_text, flags=re.I
        )
        if not found:
            continue
        complete = True
        for page in claim.source_pages:
            start, end = _literal_spans(pages[page], claim.raw_text)[0]
            before = re.split(r"[.;!?。]", pages[page][:start])[-1]
            after = pages[page][end:].lstrip()
            if re.search(r"\b(?:not|never|except|unless|without)\b|不|无需", before, re.I) or (
                not claim.raw_text.endswith((".", ";", "!", "?", "。"))
                and after
                and after[0] not in ".;!?。"
            ):
                complete = False
        if not complete:
            continue
        properties = [
            re.sub(r"^(?:the|a)\s+", "", term.strip(), flags=re.I)
            for term in re.split(r"\s+and\s+|,\s*", found[1], flags=re.I)
        ]
        definitions = [resolve_definition(term) for term in properties]
        if not definitions or any(
            item is None or item.value_kind != "number" or "reporting" not in item.allowed_roles
            for item in definitions
        ):
            continue
        candidates.append((claim.raw_text, definition.id, {item.id for item in definitions}))
    return any(
        needed <= {identifier for raw, identifier, _ in candidates if raw == quoted}
        for quoted, _, needed in candidates
    )


_NONIDENTIFYING = frozenset(
    """
test tests testing measurement measurements resistance temperature pressure
sample samples specimen specimens material materials surface coating coated
after before initial final conditioned conditioning preparation common each
all both every other same valid production new flat black steel painted
""".split()
)
_SPECIMENS = r"(?:specimens?|samples?|bars?|beams?|coupons?|panels?|witnesses)"


def nonblocking_shared_clause_issues(run: CustomerExtractionRun) -> set[str]:
    """Correct historical software-only exception flags from actual typed clauses.

    No free-form model concern, real exception, ordinary source citation or
    scientific atom warning is removed. The saved original issue remains intact.
    """
    if run.strategy not in {"compact_document", "inventory_compact_document"}:
        return set()
    ignored = set()
    body = (
        "shared source conditions contain an exception; verify their applicability "
        "to this test before accepting the draft."
    )
    for call in run.calls:
        if call.purpose != "extraction":
            continue
        try:
            payload, _ = normalize_compact_containers(
                loads_exact(call.raw_response), call_index=call.call_index
            )
            compact = CompactCustomerExtraction.model_validate(payload)
        except (ValueError, TypeError):
            continue
        model_warnings = [
            *compact.review_issues,
            *(row.uncertainty for row in compact.requirements if row.uncertainty),
        ]
        if any(body.casefold() in warning.casefold() for warning in model_warnings):
            continue
        targets = {index for shared in compact.shared_requirements for index in shared.applies_to}
        genuine = {
            index
            for shared in compact.shared_requirements
            if shared_applicability_has_exception(shared)
            for index in shared.applies_to
        }
        for index in targets - genuine:
            issue = f"req_{index:03d}: " + body
            if issue in run.extraction.review_issues:
                ignored.add(issue)
    return ignored


def _method_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def scoped_specimen_review_reasons(
    requirements: list[CustomerRequirement], document: IngestedDocument
) -> dict[str, str]:
    """Flag only source-located numerical conditions with a different named owner.

    The current lexical audit recognizes an explicit test descriptor directly
    before an English specimen noun. It is not a universal scope resolver or a
    multilingual ownership guarantee. Generic or jointly named/all-sample
    preparation stays unclassified, rather than receiving an invented owner.
    """
    catalog = load_reference_catalog()
    methods = {item.id: _method_key(item.identifier) for item in catalog.methods}
    descriptors: dict[str, set[str]] = {}
    for row in requirements:
        references = {_method_key(item.code) for item in row.requested_methods}
        for family in catalog.tests:
            if not any(methods[method_id] in references for method_id in family.method_ids):
                continue
            for name in (family.name, *(alias.text for alias in family.aliases)):
                for token in re.findall(r"[a-z]{3,}", normalized_name(name)):
                    if token not in _NONIDENTIFYING:
                        descriptors.setdefault(token, set()).add(row.id)

    sentences = {
        page.page_number: re.split(r"(?<=[.!?。])\s+(?=[A-ZÀ-Ý])", page.text)
        for page in document.pages
    }
    reasons: dict[str, str] = {}
    for row in requirements:
        for claim in row.atomic_claims:
            if claim.kind != "condition" or claim.operator in {"text", "required"}:
                continue
            candidates = [
                clause
                for page in claim.source_pages
                for clause in sentences.get(page, [])
                if citation_contains(clause, claim.raw_text)
            ]
            for clause in sorted(candidates, key=len):
                if re.search(r"\b(?:all|every|both|each)\s+" + _SPECIMENS + r"\b", clause, re.I):
                    continue
                owners = set()
                for match in re.finditer(r"\b([A-Za-z][A-Za-z-]{2,40})\s+" + _SPECIMENS, clause):
                    for descriptor in normalized_name(match[1]).split():
                        owners.update(descriptors.get(descriptor, ()))
                if owners and row.id not in owners:
                    reasons[row.id] = (
                        "Some extracted conditions name another test's specimens. "
                        "Check which test these conditions belong to in the PDF."
                    )
                    break
            if row.id in reasons:
                break
    return reasons

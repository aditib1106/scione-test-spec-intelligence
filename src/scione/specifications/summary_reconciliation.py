"""Reconcile one closed acceptance-summary template against physical source facts.

This is deliberately not a general summary interpreter. A model handling comment
is only a selector for this audit; it is never evidence. Every listed threshold
must agree in the physical body, the physical summary and an independently owned
native criterion, including phase and measurement scale. Other templates,
ambiguous owners or any changed scientific detail fail closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scione.numeric_literals import NUMBER, decimal_literal
from scione.parameters.registry import resolve_definition
from scione.parameters.units import canonical_unit_name
from scione.schemas import IngestedDocument, TextExtractionStatus
from scione.source_text import citation_contains
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.named_scale_units import (
    _literal_spans,
    physical_named_scale_unit_evidence,
)

SUMMARY_RECONCILIATION_POLICY = "physical_acceptance_summary_v1"
_NUM = rf"(?:{NUMBER})"
_RANGE = rf"(?P<{{low}}>{_NUM})\s*[-–]\s*(?P<{{high}}>{_NUM})"
_SUMMARY = re.compile(
    r"Acceptance\s+summary\s+for\s+the\s+receiving\s+report\s+"
    r"Customer\s+checkpoint\s+Release\s+threshold\s+"
    r"(?P<thickness>Topcoat\s+film\s+thickness\s+"
    + _RANGE.format(low="tlow", high="thigh")
    + r"\s*µm\s+on\s+each\s+panel)\s+"
    r"(?P<adhesion>Scored-grid\s+retention\s+ISO\s+class\s+"
    r"(?P<alow>\d+)\s*[-–]\s*(?P<ahigh>\d+)\s+before\s+and\s+after\s+cycles)\s+"
    r"(?P<gloss>(?P<angle>\d+)°\s+reflected\s+gloss\s+"
    + _RANGE.format(low="glow", high="ghigh")
    + rf"\s*GU\s+initial;\s*>=\s*(?P<retention>{_NUM})\s*%\s+site\s+retention)\s+"
    rf"(?P<colour>Master-panel\s+colour\s+drift\s+ΔE\*ab\s*<=\s*(?P<colourmax>{_NUM}))"
    r"(?=\s+For\s+release\s+review,)",
    re.I,
)
_THICKNESS_BODY = re.compile(
    r"Record\s+total\s+dry\s+film\s+build\s+and\s+the\s+separate\s+topcoat\s+thickness;\s*"
    rf"the\s+latter\s+must\s+be\s+between\s+(?P<low>{_NUM})\s+and\s+"
    rf"(?P<high>{_NUM})\s*µm\s+on\s+every\s+panel[.]",
    re.I,
)
_ADHESION_BODY = re.compile(
    r"If\s+the\s+ISO\s+procedure\s+is\s+used,\s+only\s+class\s+"
    r"(?P<low>\d+)\s+or\s+(?P<high>\d+)\s+is\s+acceptable(?=;)",
    re.I,
)
_ADHESION_PHASE = re.compile(
    r"Repeat\s+the\s+check\s+both\s+before\s+and\s+after\s+"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+cycles\s+"
    r"between\s+[^.!?]+[.]",
    re.I,
)
_ADHESION_METHOD = re.compile(
    r"For\s+this\s+grid-and-pull\s+assessment\s+the\s+receiving\s+site\s+should\s+use\s+"
    r"(?P<method>ISO\s+\d+(?::\d{4})?)[.]",
    re.I,
)
_GLOSS_BODY = re.compile(
    r"Record\s+the\s+(?P<angle>\d+)°\s+specular-gloss\s+reading\s+using\s+"
    r"(?P<method>ASTM\s+D\d+-\d+)\s+at\s+five\s+marked\s+locations[.]\s+"
    rf"(?P<initial>The\s+arithmetic\s+mean\s+must\s+be\s+(?P<low>{_NUM})\s*[-–]\s*"
    rf"(?P<high>{_NUM})\s*GU\s+as\s+delivered);\s*"
    rf"(?P<retention>after\s+cycling,\s+each\s+location\s+must\s+retain\s+at\s+least\s+"
    rf"(?P<percent>{_NUM})\s*%\s+of\s+its\s+own\s+original\s+value)[.]",
    re.I,
)
_COLOUR_BODY = re.compile(
    r"Separately,\s+compare\s+the\s+same\s+panel\s+with\s+an\s+untouched\s+master\s+under\s+"
    r"D\d+\s+lighting:\s*(?P<criterion>the\s+overall\s+colour\s+difference,\s+calculated\s+"
    rf"from\s+measured\s+L\*,\s+a\*\s+and\s+b\*,\s+may\s+not\s+exceed\s+"
    rf"(?P<maximum>{_NUM}))[.]",
    re.I,
)
_COMMENT = re.compile(
    r"The acceptance summary table repeats body limits \(topcoat "
    + _RANGE.format(low="tlow", high="thigh")
    + r" µm, ISO class (?P<alow>\d+)-(?P<ahigh>\d+), "
    + _RANGE.format(low="glow", high="ghigh")
    + rf" GU / >=(?P<retention>{_NUM})% retention, ΔE\*ab <=(?P<colourmax>{_NUM})\) "
    r"without adding new requirements; treated as consistent repeats[.]",
    re.I,
)


@dataclass(frozen=True)
class SummaryReconciliation:
    nonblocking_issues: frozenset[str]
    proofs: tuple[dict, ...]


def _located(pattern, document, *, sentence_start=False):
    matches = [
        (page.page_number, match)
        for page in document.pages
        for match in pattern.finditer(page.text)
        if not sentence_start
        or not page.text[: match.start()].strip()
        or page.text[: match.start()].rstrip().endswith((".", "!", "?"))
    ]
    return matches[0] if len(matches) == 1 else None


def _citation(page, match, group=0):
    return {
        "page": page,
        "quote": match[group],
        "source_start": match.start(group),
        "source_end": match.end(group),
    }


def _key(value):
    return re.sub(r"\s+", "", value).casefold()


def _context_owned(value, physical_text):
    if not value:
        return True
    for part in value.split(";"):
        part = part.strip()
        if citation_contains(physical_text, part):
            continue
        # A single fixed reference-panel paraphrase has no additional scientific
        # content; the colour body itself must independently name that master.
        if part.casefold() == "versus untouched master" and citation_contains(
            physical_text, "with an untouched master"
        ):
            continue
        return False
    return True


def _claim(
    run,
    document,
    parameter,
    stage,
    operator,
    values,
    unit,
    body,
    *,
    raw_group=0,
    allowed_stages=None,
    context_source=None,
):
    """One correctly labelled owner and one exact, source-owned native criterion."""
    page, match = body
    quote = match[raw_group]
    owners = []
    for row in run.extraction.requirements:
        for claim in row.atomic_claims:
            definition = resolve_definition(claim.parameter_id)
            if not definition or definition.id != parameter:
                continue
            if claim.operator in {"text", "required"} and not (
                claim.value is not None
                or claim.minimum is not None
                or claim.maximum is not None
                or claim.tolerance is not None
                or claim.values
            ):
                continue  # e.g. conditional, future ASTM acceptance remains a separate claim.
            owners.append((row, claim))
    if not owners or len({row.id for row, _ in owners}) != 1:
        return None
    selected = []
    for row, claim in owners:
        if claim.stage not in (allowed_stages or {stage}):
            return None
        if claim.stage != stage:
            continue
        label = resolve_definition(claim.label)
        candidate = (
            [claim.minimum, claim.maximum]
            if operator == "range"
            else list(claim.values)
            if operator == "one_of"
            else [claim.value]
        )
        if not (
            claim.kind == "criterion"
            and claim.operator == operator
            and candidate == values
            and label
            and label.id == parameter
            and claim.availability == "supported"
            and claim.normalization_state == "parsed"
            and claim.configuration is None
            and claim.sequence is None
            and claim.tolerance is None
            and (claim.value is None and not claim.values if operator == "range" else True)
            and (
                claim.value is None and claim.minimum is None and claim.maximum is None
                if operator == "one_of"
                else True
            )
            and (
                claim.minimum is None and claim.maximum is None and not claim.values
                if operator not in {"range", "one_of"}
                else True
            )
            and claim.text_value is None
            and _context_owned(claim.source_context, context_source or quote)
            and (canonical_unit_name(claim.unit) == unit if unit else claim.unit in {None, "class"})
            and set(claim.source_pages) == {page}
            and citation_contains(quote, claim.raw_text)
            and len(_literal_spans(match.string, claim.raw_text)) == 1
            and any(
                evidence.page == page
                and citation_contains(evidence.quote, quote)
                and len(_literal_spans(match.string, evidence.quote)) == 1
                for evidence in row.evidence
            )
        ):
            return None
        selected.append((row, claim))
    return selected[0] if len(selected) == 1 else None


def source_summary_reconciliation(
    run: CustomerExtractionRun, document: IngestedDocument
) -> SummaryReconciliation:
    """A complete physical body/summary/native proof or no review-state change."""
    if (
        run.document_id != document.document_id
        or document.extraction_status != TextExtractionStatus.READY
    ):
        return SummaryReconciliation(frozenset(), ())
    issues = [(issue, _COMMENT.fullmatch(issue)) for issue in run.extraction.review_issues]
    issues = [(issue, match) for issue, match in issues if match]
    if not issues:
        return SummaryReconciliation(frozenset(), ())
    patterns = (
        _SUMMARY,
        _THICKNESS_BODY,
        _ADHESION_BODY,
        _ADHESION_PHASE,
        _ADHESION_METHOD,
        _GLOSS_BODY,
        _COLOUR_BODY,
    )
    found = [
        _located(pattern, document, sentence_start=pattern is not _SUMMARY) for pattern in patterns
    ]
    if any(item is None for item in found):
        return SummaryReconciliation(frozenset(), ())
    summary, thickness, adhesion, phase, method, gloss, colour = found
    _, table = summary
    _, top = thickness
    _, iso = adhesion
    _, shine = gloss
    _, delta = colour
    values = {
        name: decimal_literal(table[name])
        for name in ("tlow", "thigh", "alow", "ahigh", "glow", "ghigh", "retention", "colourmax")
    }
    if not (
        values["tlow"] <= values["thigh"]
        and values["glow"] <= values["ghigh"]
        and values["ahigh"] - values["alow"] == 1
        and [values["tlow"], values["thigh"]]
        == [decimal_literal(top["low"]), decimal_literal(top["high"])]
        and [values["alow"], values["ahigh"]]
        == [decimal_literal(iso["low"]), decimal_literal(iso["high"])]
        and [values["glow"], values["ghigh"]]
        == [decimal_literal(shine["low"]), decimal_literal(shine["high"])]
        and values["retention"] == decimal_literal(shine["percent"])
        and values["colourmax"] == decimal_literal(delta["maximum"])
        and table["angle"] == shine["angle"]
    ):
        return SummaryReconciliation(frozenset(), ())
    top_native = _claim(
        run,
        document,
        "topcoat_thickness",
        "general",
        "range",
        [values["tlow"], values["thigh"]],
        "µm",
        thickness,
    )
    iso_native = _claim(
        run,
        document,
        "adhesion_class",
        "initial",
        "one_of",
        [values["alow"], values["ahigh"]],
        None,
        adhesion,
        allowed_stages={"initial", "after_cycle"},
        context_source=iso[0] + " " + table["adhesion"] + " " + phase[1][0],
    )
    after_native = _claim(
        run,
        document,
        "adhesion_class",
        "after_cycle",
        "one_of",
        [values["alow"], values["ahigh"]],
        None,
        summary,
        raw_group="adhesion",
        allowed_stages={"initial", "after_cycle"},
        context_source=iso[0] + " " + table["adhesion"] + " " + phase[1][0],
    )
    gloss_native = _claim(
        run,
        document,
        "gloss",
        "initial",
        "range",
        [values["glow"], values["ghigh"]],
        "GU",
        gloss,
        raw_group="initial",
    )
    retention_native = _claim(
        run,
        document,
        "gloss_retention",
        "after_cycle",
        ">=",
        [values["retention"]],
        "%",
        gloss,
        raw_group="retention",
    )
    # The physical named-scale helper binds the missing native unit to this exact
    # colour body and named summary row; it never borrows an unrelated scalar.
    colour_candidates = [
        (row, claim, proof)
        for row in run.extraction.requirements
        for claim in row.atomic_claims
        if claim.parameter_id == "color_difference"
        and claim.kind == "criterion"
        and claim.operator == "<="
        and claim.value == values["colourmax"]
        and claim.stage == "measurement"
        and citation_contains(delta["criterion"], claim.raw_text)
        and _context_owned(claim.source_context, delta[0])
        and (
            proof := physical_named_scale_unit_evidence(
                claim, row, document, requirements=run.extraction.requirements
            )
        )
    ]
    if not all((top_native, iso_native, after_native, gloss_native, retention_native)):
        return SummaryReconciliation(frozenset(), ())
    all_colour_numeric = [
        claim
        for row in run.extraction.requirements
        for claim in row.atomic_claims
        if (definition := resolve_definition(claim.parameter_id))
        and definition.id == "color_difference"
        and claim.operator not in {"text", "required"}
    ]
    if len(colour_candidates) != 1 or len(all_colour_numeric) != 1:
        return SummaryReconciliation(frozenset(), ())
    colour_row, colour_claim, colour_proof = colour_candidates[0]
    iso_row = iso_native[0]
    if not (
        iso_row.id == after_native[0].id
        and gloss_native[0].id == retention_native[0].id
        and len({top_native[0].id, iso_row.id, gloss_native[0].id, colour_row.id}) == 4
        and re.search(r"\btopcoat\b.*\bthickness\b", top_native[0].original_test_name, re.I)
        and re.search(
            r"\b(?:scored[- ]grid|cross[- ]cut|grid-and-tape|adhesion)\b",
            iso_row.original_test_name,
            re.I,
        )
        and re.search(r"\bgloss\b", gloss_native[0].original_test_name, re.I)
        and re.search(r"\bcolou?r\s+(?:difference|drift)\b", colour_row.original_test_name, re.I)
        and any(
            _key(item.code) == _key(method[1]["method"]) and item.role == "primary"
            for item in iso_row.requested_methods
        )
        and any(
            _key(item.code) == _key(shine["method"]) for item in gloss_native[0].requested_methods
        )
        and any(
            evidence.page == phase[0] and citation_contains(evidence.quote, phase[1][0])
            for evidence in iso_row.evidence
        )
    ):
        return SummaryReconciliation(frozenset(), ())
    matched_issues = [
        issue
        for issue, match in issues
        if all(decimal_literal(match[name]) == value for name, value in values.items())
    ]
    if not matched_issues:
        return SummaryReconciliation(frozenset(), ())
    native = (top_native, iso_native, after_native, gloss_native, retention_native)
    proofs = [
        {
            "rule": "physical_body_and_acceptance_summary_agree",
            "policy_version": SUMMARY_RECONCILIATION_POLICY,
            "document_sha256": document.sha256,
            "model_comments": matched_issues,
            "model_comment_used_as_evidence": False,
            "summary": _citation(*summary),
            "physical_body": [
                _citation(*item) for item in (thickness, adhesion, phase, method, gloss, colour)
            ],
            "native_criterion_claims": [
                {
                    "requirement_id": row.id,
                    "claim_id": claim.id,
                    "parameter_id": claim.parameter_id,
                    "stage": claim.stage,
                    "operator": claim.operator,
                    "unit": claim.unit,
                }
                for row, claim in native
            ],
            "physical_named_scale_proof": colour_proof,
            "colour_native_claim_id": colour_claim.id,
            "scope": "one closed four-property summary template; "
            "not a document completeness certificate",
        }
    ]
    return SummaryReconciliation(frozenset(matched_issues), tuple(proofs))

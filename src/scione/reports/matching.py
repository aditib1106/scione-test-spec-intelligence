"""Conservative, offline identity matching and explicitly reviewed draft pricing.

Names identify tests; method references identify methods. Neither proves that the
lab can meet the customer's conditions. Free-form constraints are not silently
interpreted as machine-readable limits, and a row count is never a quantity.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from uuid import uuid4

from scione.catalogs.models import (
    LabCapability,
    LabProfile,
    ReferenceCatalog,
    TestMethod,
    validate_lab_references,
)
from scione.catalogs.normalization import (
    is_source_defined_procedure,
    normalized_name,
    scoped_pilot_family,
)
from scione.reports.capability import CapabilityAssessment, assess_capability
from scione.reports.models import (
    GapLine,
    MappingChoice,
    QuoteLine,
    ReportSnapshot,
    ReportWorkspace,
    multiply_amounts,
    sum_amounts,
)
from scione.specifications.extraction import coverage_issues
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement, RequestedMethod
from scione.specifications.review import (
    REVIEW_POLICY_VERSION,
    RequirementAssessment,
    assess_requirements,
)
from scione.specifications.store import RequirementReviewSet, StoredSpecification

_DASHES = str.maketrans({character: "-" for character in "‐‑‒–—−"})
_METHOD = re.compile(
    r"(?P<authority>ISO|ASTM|GB\s*/\s*T|GB|SAE|VDMA|VDA|DIN)\s*"
    r"(?P<number>[A-Z]?\s*\d+)(?P<parts>(?:\s*-\s*\d+)*)",
    re.IGNORECASE,
)
_REVISION = re.compile(r"^\s*:\s*(\d{4})\s*$")
MATCHING_POLICY_VERSION = "0.4"


@dataclass(frozen=True)
class _MethodIdentity:
    key: str
    revision: str | None
    qualifier: str


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).translate(_DASHES).strip()


def _name(value: str) -> str:
    return normalized_name(value)


def _parse_method(value: str) -> _MethodIdentity | None:
    text = _text(value).upper()
    matched = _METHOD.match(text)
    if matched is None:
        return None
    authority = re.sub(r"\s+", "", matched["authority"])
    number = re.sub(r"\s+", "", matched["number"])
    parts = re.sub(r"\s+", "", matched["parts"])
    remainder = text[matched.end() :]
    revision: str | None = None
    # ASTM's trailing two/four-digit year is not a method part. ISO/VDA parts
    # remain identity-bearing and must never collapse onto their parent method.
    if authority == "ASTM" and re.fullmatch(r"-\d{2}(?:\d{2})?", parts):
        revision, parts = parts[1:], ""
    revision_match = _REVISION.fullmatch(remainder)
    if revision_match:
        revision = revision_match[1]
        remainder = ""
    return _MethodIdentity(f"{authority}:{number}{parts}", revision, remainder.strip())


def _revision(value: str | None) -> str | None:
    return _text(value).casefold().lstrip(":").strip() if value else None


def _method_resolution(
    requested: RequestedMethod, catalog: ReferenceCatalog
) -> tuple[str | None, str | None]:
    identity = _parse_method(requested.code)
    if identity is None:
        # Nonstandard source procedures have their own explicit identifiers.
        # Only exact curated strings are accepted; no phrase is stripped until
        # a standard happens to appear, and no equipment implies equivalence.
        exact = [
            method
            for method in catalog.methods
            if _name(requested.code)
            in {_name(method.identifier), *(_name(alias.text) for alias in method.aliases)}
        ]
        if len(exact) == 1:
            if requested.revision and _revision(requested.revision) != _revision(exact[0].edition):
                return exact[0].id, (
                    f"Requested procedure revision {requested.revision} is not established "
                    f"by the catalog for {requested.code}."
                )
            return exact[0].id, None
        if exact:
            return None, f"Ambiguous catalog procedure: {requested.code}."
        return None, f"Unrecognized method reference: {requested.code}."
    candidates: list[TestMethod] = []
    for method in catalog.methods:
        identifiers = [method.identifier, *(alias.text for alias in method.aliases)]
        if any(
            (parsed := _parse_method(value)) is not None and parsed.key == identity.key
            for value in identifiers
        ):
            candidates.append(method)
    if len(candidates) != 1:
        message = "Unknown" if not candidates else "Ambiguous"
        return None, f"{message} catalog method: {requested.code}."
    method = candidates[0]
    if identity.qualifier:
        return method.id, (
            f"Method qualifier needs review: {requested.code}; the base identifier alone "
            "does not establish support."
        )
    embedded = _revision(identity.revision)
    explicit = _revision(requested.revision)
    if embedded and explicit and embedded != explicit:
        return method.id, f"Conflicting method revisions: {requested.code} / {requested.revision}."
    requested_revision = explicit or embedded
    catalog_revision = _revision(method.edition)
    if requested_revision and requested_revision != catalog_revision:
        return method.id, (
            f"Requested revision {requested_revision} of {method.identifier} is not "
            f"established by the catalog (edition: {method.edition or 'unknown'})."
        )
    return method.id, None


def input_fingerprint(
    stored: StoredSpecification,
    run: CustomerExtractionRun,
    review: RequirementReviewSet,
    lab: LabProfile,
    catalog: ReferenceCatalog,
    workspace: ReportWorkspace,
) -> str:
    """Hash every semantic source input, excluding output ID/time and file paths."""

    source = {
        "matching_policy": MATCHING_POLICY_VERSION,
        "review_policy": REVIEW_POLICY_VERSION,
        "document": stored.document.model_dump(mode="json", exclude_computed_fields=True),
        "original_name": stored.original_name,
        "uploaded_at": stored.created_at.isoformat(),
        "run": run.model_dump(mode="json"),
        "review": review.model_dump(mode="json"),
        "lab": lab.model_dump(mode="json"),
        "catalog": catalog.model_dump(mode="json"),
        "workspace": workspace.model_dump(mode="json"),
    }
    encoded = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def lab_fingerprint(lab: LabProfile) -> str:
    """Bind scope and quantity approvals to the exact lab declaration reviewed."""

    encoded = json.dumps(
        lab.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical(
    requirement: CustomerRequirement,
    catalog: ReferenceCatalog,
    method_ids: list[str],
    choice: MappingChoice | None,
) -> tuple[str | None, list[str], str, list[str]]:
    name = _name(requirement.original_test_name)
    exact = [
        test.id
        for test in catalog.tests
        if name in {_name(test.name), *(_name(alias.text) for alias in test.aliases)}
    ]
    scoped = scoped_pilot_family(requirement, catalog)
    if scoped and scoped not in exact:
        exact.append(scoped)
    method_candidates = [
        test.id for test in catalog.tests if set(method_ids) & set(test.method_ids)
    ]
    fuzzy = [
        test.id
        for test in catalog.tests
        if max(
            SequenceMatcher(None, name, _name(label)).ratio()
            for label in [test.name, *(alias.text for alias in test.aliases)]
        )
        >= 0.65
    ]
    candidates = list(dict.fromkeys([*exact, *method_candidates, *fuzzy]))
    if choice is not None and choice.canonical_test_id is None:
        return (
            None,
            candidates,
            "Human left test identity unresolved",
            ["Human matching review left this test unresolved / not in the catalog."],
        )
    if choice is not None and choice.canonical_test_id:
        if choice.canonical_test_id not in {test.id for test in catalog.tests}:
            return (
                None,
                candidates,
                "Invalid human choice",
                ["Saved test choice is not in catalog."],
            )
        test_id, basis = choice.canonical_test_id, "Human-confirmed test identity"
    elif len(exact) == 1:
        test_id = exact[0]
        basis = (
            "Scoped curated chemical / fluid family; original medium and conditions retained"
            if scoped == test_id
            else "Exact curated name / alias"
        )
    else:
        reason = (
            "Test name matches multiple catalog aliases; choose the intended test."
            if len(exact) > 1
            else "No exact curated name / alias match; confirm a catalog test or leave unresolved."
        )
        return None, candidates, "Candidate suggestions only", [reason]
    test = catalog.test_by_id(test_id)
    conflicting = set(method_ids) - set(test.method_ids)
    if conflicting:
        return (
            test_id,
            candidates,
            basis,
            [
                "Requested method is not associated with the selected test; review the test / "
                "method conflict. No cross-standard equivalence has been assumed."
            ],
        )
    return test_id, candidates, basis, []


def _selected_methods(
    requirement: CustomerRequirement, choice: MappingChoice | None
) -> tuple[list[RequestedMethod], list[str]]:
    if choice is None or choice.selected_method_codes is None:
        roles = [
            (_name(method.role) if method.role else "") for method in requirement.requested_methods
        ]
        # Explicit source roles can establish that references are cumulative
        # (for example exposure + measurement). Merely seeing several known
        # identifiers never establishes their applicability or equivalence.
        cumulative_roles = {"exposure", "measurement", "assessment", "calculation", "preparation"}
        explicit_cumulative = (
            len(roles) > 1
            and len(set(roles)) == len(roles)
            and all(role in cumulative_roles for role in roles)
            and not any(
                word in _name(text)
                for text in [*requirement.special_instructions, *requirement.applicability]
                for word in (
                    "alternative",
                    "instead",
                    "either",
                    "regional",
                    "optional",
                    "unless",
                    "except",
                    "substitute",
                    "replace",
                    " only ",
                    " or ",
                )
            )
        )
        return requirement.requested_methods, (
            [
                "Multiple method references may be alternatives or cumulative requirements; "
                "explicitly confirm which original method reference(s) apply."
            ]
            if len(requirement.requested_methods) > 1 and not explicit_cumulative
            else []
        )
    selected = choice.selected_method_codes
    original = {method.code for method in requirement.requested_methods}
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= original:
        return requirement.requested_methods, [
            "Saved method selection is empty, duplicated, or no longer an original method code."
        ]
    return [method for method in requirement.requested_methods if method.code in selected], []


def _choose_capability(
    test_id: str,
    method_ids: list[str],
    lab: LabProfile,
    choice: MappingChoice | None,
) -> tuple[LabCapability | None, str, list[str], list[str]]:
    same_test = [cap for cap in lab.capabilities if cap.canonical_test_id == test_id]
    all_methods = list(dict.fromkeys(method for cap in same_test for method in cap.method_ids))
    if choice is not None and choice.capability_id:
        selected = next((cap for cap in same_test if cap.id == choice.capability_id), None)
        if selected is None:
            return (
                None,
                "needs_review",
                all_methods,
                ["Saved lab capability choice is missing or belongs to another test."],
            )
        if not set(method_ids) <= set(selected.method_ids):
            return (
                selected,
                "method_gap",
                selected.method_ids,
                ["The chosen lab capability does not list every selected requested method."],
            )
        return selected, "in_house", selected.method_ids, []
    if not same_test:
        return (
            None,
            "test_gap",
            [],
            [
                "The lab catalog has no declaration for this test; this is not proof "
                "it is impossible."
            ],
        )
    full = [cap for cap in same_test if set(method_ids) <= set(cap.method_ids)]
    if len(full) == 1:
        return full[0], "in_house", full[0].method_ids, []
    if len(full) > 1:
        return (
            None,
            "needs_review",
            all_methods,
            [
                "Several lab declarations list the requested methods; choose the "
                "applicable capability."
            ],
        )
    return (
        same_test[0] if len(same_test) == 1 else None,
        "method_gap",
        all_methods,
        [
            "The lab lists this test, but not all selected requested methods. Related methods "
            "are not automatically interchangeable."
        ],
    )


def _line(
    requirement: CustomerRequirement,
    assessment: RequirementAssessment,
    catalog: ReferenceCatalog,
    lab: LabProfile,
    choice: MappingChoice | None,
) -> GapLine:
    methods, reasons = _selected_methods(requirement, choice)
    method_ids: list[str] = []
    unknown: list[str] = []
    for requested in methods:
        method_id, problem = _method_resolution(requested, catalog)
        if method_id is not None and method_id not in method_ids:
            method_ids.append(method_id)
        if problem:
            unknown.append(requested.code)
            reasons.append(problem)
    source_defined = not methods and is_source_defined_procedure(requirement, catalog)
    if not methods and not source_defined:
        reasons.append("No requested method was extracted; no lab method has been inferred.")
    test_id, candidates, basis, identity_reasons = _canonical(
        requirement, catalog, method_ids, choice
    )
    reasons.extend(identity_reasons)
    capability = None
    lab_methods: list[str] = []
    status = "needs_review"
    if test_id is not None and not reasons:
        if source_defined:
            # Knowing the customer procedure's identity is enough to report an
            # absent declaration, but never enough to assert a lab method match.
            declared = [cap for cap in lab.capabilities if cap.canonical_test_id == test_id]
            status = "needs_review" if declared else "test_gap"
            lab_methods = list(dict.fromkeys(x for cap in declared for x in cap.method_ids))
            reasons.append(
                "Source-defined customer procedure identified; no standard method was inferred. "
                + (
                    "A lab declaration exists, but applicability to this procedure needs review."
                    if declared
                    else "The lab catalog has no declaration for this procedure; this is not "
                    "proof it is impossible."
                )
            )
        else:
            capability, status, lab_methods, capability_reasons = _choose_capability(
                test_id, method_ids, lab, choice
            )
            reasons.extend(capability_reasons)
    elif test_id is not None:
        declared = [cap for cap in lab.capabilities if cap.canonical_test_id == test_id]
        lab_methods = list(dict.fromkeys(method for cap in declared for method in cap.method_ids))
        if not identity_reasons and not declared:
            # An absent test declaration is already a reportable catalog gap.
            # Resolving every requested method first cannot make an undeclared
            # test available. Keep method problems intact for later review;
            # this neither resolves them nor establishes physical inability.
            status = "test_gap"
            reasons.append(
                "The lab catalog has no declaration for this test; this is not proof "
                "it is impossible. Unresolved method details are retained and have not "
                "been treated as equivalent or supported."
            )
    operating = CapabilityAssessment()
    if status == "in_house" and capability is not None:
        operating = assess_capability(requirement, capability)
        scope_current = choice is not None and choice.lab_fingerprint == lab_fingerprint(lab)
        if operating.status == "gap":
            status = "condition_gap"
            reasons.extend(operating.reasons)
        elif (
            choice is not None
            and choice.capability_verdict in {"supported", "unsupported"}
            and not scope_current
        ):
            reasons.append(
                "The saved conditions / scope decision is not bound to this lab declaration. "
                "Renew the scope review after checking the current lab methods, limits and rates."
            )
        elif scope_current and choice.capability_verdict == "unsupported":
            status = "condition_gap"
            reasons.append("Human review marked the requested conditions / scope unsupported.")
        elif scope_current and choice.capability_verdict == "supported":
            reasons.append("Human review confirmed the requested conditions / scope for this line.")
        elif operating.status == "supported":
            reasons.extend(operating.reasons)
        else:
            reasons.append(
                "Method is listed in-house, but some operating conditions / scope remain "
                "unresolved. Material pass/fail and accreditation are not predicted."
            )
    if assessment.state == "excluded":
        status = "excluded"
        reasons.append("Content review excluded this source requirement from the current scope.")
    return GapLine(
        requirement=requirement.model_copy(deep=True),
        canonical_test_id=test_id,
        canonical_test_name=catalog.test_by_id(test_id).name if test_id is not None else None,
        candidate_test_ids=candidates,
        match_basis=basis,
        method_ids=method_ids,
        unknown_methods=unknown,
        considered_method_codes=[method.code for method in methods],
        capability=capability.model_copy(deep=True) if capability is not None else None,
        lab_method_identifiers=[catalog.method_by_id(method).identifier for method in lab_methods],
        status=status,
        reasons=reasons,
        extraction_review_state=assessment.state,
        extraction_review_reasons=list(assessment.reasons),
        mapping_choice=choice.model_copy(deep=True) if choice is not None else None,
        operation_assessment=operating.status,
        operation_checks=[
            f"{check.label}: {check.requested} — {check.declared or 'Not declared'} "
            f"({check.status})"
            for check in operating.checks
        ],
        operation_reasons=operating.reasons,
    )


def support_basis(line: GapLine, current_lab_fingerprint: str) -> str | None:
    """An operating match is distinct from human approval and material pass/fail."""

    if line.status != "in_house":
        return None
    choice = line.mapping_choice
    if choice is not None and choice.capability_verdict != "unreviewed":
        if (
            choice.capability_verdict == "supported"
            and choice.lab_fingerprint == current_lab_fingerprint
        ):
            return "human"
        return None
    return "automatic" if line.operation_assessment == "supported" else None


def _attach_quote(
    line: GapLine, workspace: ReportWorkspace, current_lab_fingerprint: str
) -> str | None:
    quantity = workspace.quantities.get(line.requirement.id)
    if quantity is None:
        return None
    capability = line.capability
    if not (
        line.status == "in_house"
        and line.extraction_review_state in {"ready", "confirmed"}
        and support_basis(line, current_lab_fingerprint) is not None
        and quantity.lab_fingerprint == current_lab_fingerprint
        and capability is not None
        and capability.price is not None
        and capability.currency
        and capability.pricing_basis
    ):
        return (
            f"{line.requirement.id}: saved quantity was not priced because matching, content "
            "review, condition/scope confirmation or catalog pricing is incomplete / changed."
        )
    subtotal = multiply_amounts(capability.price, quantity.quantity)
    line.quote = QuoteLine(
        quantity=quantity.quantity,
        unit_price=capability.price,
        currency=capability.currency,
        pricing_basis=capability.pricing_basis,
        subtotal=subtotal,
        note=quantity.note,
    )
    return None


def build_report(
    stored: StoredSpecification,
    run: CustomerExtractionRun,
    review: RequirementReviewSet,
    lab: LabProfile,
    catalog: ReferenceCatalog,
    workspace: ReportWorkspace,
    *,
    pdf_bytes: bytes | None = None,
    report_id: str | None = None,
    created_at: datetime | None = None,
) -> ReportSnapshot:
    """Create a complete, immutable draft snapshot without IO or provider calls."""

    if not stored.document_id == run.document_id == review.document_id == workspace.document_id:
        raise ValueError("Report inputs refer to different source documents.")
    if not run.run_id == review.run_id == workspace.run_id:
        raise ValueError("Report inputs refer to different extraction runs.")
    source_ids = {requirement.id for requirement in run.extraction.requirements}
    if not set(review.decisions) <= source_ids or any(
        key != decision.requirement_id for key, decision in review.decisions.items()
    ):
        raise ValueError("Content review decisions do not belong to their source requirements.")
    validate_lab_references(lab, catalog)
    assessments = assess_requirements(run, stored.document, review, pdf_bytes=pdf_bytes)
    warnings = [
        "Draft only: matching identifies lab-declared methods, not independently verified "
        "conditions, acceptance capability or accreditation. Human engineering approval "
        "is required.",
        "Quantities are explicitly entered and scope / pricing-basis confirmed; extracted record "
        "counts are not quantities. Overlapping requirements have not been automatically deducted.",
        "Regional / alternative methods are not assumed jointly applicable or interchangeable.",
        "Totals cover only eligible, explicitly priced lines, grouped by currency. Missing prices, "
        "partner costs, taxes, sample preparation and lead times are not estimated.",
        "Draft amounts are exact, unrounded Decimal products; currency-specific minor-unit "
        "rounding has not been applied. This is not a final invoice.",
        "Unlisted tests or methods are catalog gaps, not proof of physical inability. Partner "
        "availability and new-method development are unknown.",
        *stored.document.warnings,
        *run.extraction.review_issues,
        *coverage_issues(run.extraction, stored.document),
    ]
    if catalog.synthetic:
        warnings.append(
            "The reference catalog is synthetic demonstration metadata, not authoritative."
        )
    for key in sorted((workspace.mappings.keys() | workspace.quantities.keys()) - source_ids):
        warnings.append(
            f"{key}: stale saved mapping / quantity does not belong to this extraction."
        )
    lines: list[GapLine] = []
    totals: dict[str, Decimal] = {}
    current_lab_fingerprint = lab_fingerprint(lab)
    for requirement in run.extraction.requirements:
        line = _line(
            requirement,
            assessments[requirement.id],
            catalog,
            lab,
            workspace.mappings.get(requirement.id),
        )
        problem = _attach_quote(line, workspace, current_lab_fingerprint)
        if problem is not None:
            warnings.append(problem)
        if line.quote is not None:
            totals[line.quote.currency] = sum_amounts(
                totals.get(line.quote.currency, Decimal("0")), line.quote.subtotal
            )
        lines.append(line)
    return ReportSnapshot(
        report_id=report_id or str(uuid4()),
        created_at=created_at or datetime.now(UTC),
        document_id=stored.document_id,
        original_name=stored.original_name,
        document_sha256=stored.document.sha256,
        source_run=run.model_copy(deep=True),
        source_review=review.model_copy(deep=True),
        lab=lab.model_copy(deep=True),
        catalog=catalog.model_copy(deep=True),
        workspace=workspace.model_copy(deep=True),
        input_fingerprint=input_fingerprint(stored, run, review, lab, catalog, workspace),
        lines=lines,
        totals_by_currency=totals,
        warnings=list(dict.fromkeys(warnings)),
    )

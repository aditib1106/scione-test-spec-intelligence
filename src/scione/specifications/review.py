"""Conservative, deterministic triage for an extracted customer draft.

``ready`` means no automatic *row-level* flag was found. It is neither calibrated
AI confidence nor evidence of complete extraction, a catalog match, or lab support.
Document-level coverage warnings must remain visible independently of these states.
Human decisions are stored separately and never change the original extraction.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from scione.catalogs.normalization import is_source_defined_procedure, normalized_name
from scione.catalogs.seed import load_reference_catalog
from scione.ingestion.method_inventory import literal_method_designations
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.chunk_review_projection import source_chunk_review_projection
from scione.specifications.conditional_notes import conditional_source_notices
from scione.specifications.extraction import validate_customer_sources
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.specifications.pdf_preview import (
    PDFPreviewError,
    inspect_pdf_highlights,
    matched_geometric_table_row,
    pdf_quote_tokens,
)
from scione.specifications.provenance import nonblocking_notice_issues, provenance_source_issues
from scione.specifications.review_audits import (
    has_explicit_measurement_request,
    native_scientific_review_reason,
    nonblocking_shared_clause_issues,
    scoped_specimen_review_reasons,
)
from scione.specifications.review_comments import (
    method_reference_comment_audits,
    review_model_comment_audits,
)
from scione.specifications.revision_notes import source_revision_notes
from scione.specifications.store import RequirementReviewSet
from scione.specifications.summary_reconciliation import source_summary_reconciliation

REVIEW_POLICY_VERSION = "0.21"

RequirementState = Literal["needs_attention", "ready", "confirmed", "excluded"]
_REQUIREMENT_ID = re.compile(
    r"(?<![A-Za-z0-9_-])req_[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?![A-Za-z0-9_-])"
)


def _method_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _method_issue_targets(
    issue: str, requirements: list[CustomerRequirement]
) -> set[str] | None:
    """Route an unnumbered method-specific concern without interpreting its truth.

    Every literal mentioned edition must belong to a row. Unknown references or
    explicit document-wide conflicts keep the conservative global route. This
    does not translate prose, resolve method aliases or infer equivalence.
    """
    if re.search(
        r"\b(?:all|every)\s+(?:test|requirement|row)|\b(?:document|revision|metadata)\s+"
        r"(?:conflict|ambigu|inconsisten)|(?:整份|所有测试|全部测试|版本冲突)", issue, re.I
    ):
        return None
    mentions = {_method_key(code) for code in literal_method_designations(issue)}
    if not mentions:
        return None
    owners = {
        code: {
            row.id for row in requirements
            if any(_method_key(method.code) == code for method in row.requested_methods)
        }
        for code in mentions
    }
    if any(not identifiers for identifiers in owners.values()):
        return None
    return set().union(*owners.values())


def _catalogued_name_issue_targets(
    issue: str, requirements: list[CustomerRequirement]
) -> set[str] | None:
    """Route a literal, curated property phrase shared by issue and row title.

    This is lexical location of a concern, not semantic approval of the test.
    Generic one-word quantities and explicit multi-row/document concerns stay
    conservative. Unknown method designations are handled by the caller first.
    """
    # A quoted source clause may itself say "other tests" while the model's
    # concern explicitly names only one row. Scope the concern, not the quote.
    outside = re.sub(r"'[^']+'|\"[^\"]+\"|«[^»]+»", " ", issue)
    if re.search(
        r"\b(?:all|every|multiple|several|remaining|other)\s+(?:test|requirement|row|method)|"
        r"\brevision\b|\bmetadata\b|(?:整份|所有测试|全部测试|版本冲突)", outside, re.I,
    ):
        return None

    def contains(text: str, phrase: str) -> bool:
        return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))

    normalized_issue = normalized_name(outside)
    titles = {row.id: normalized_name(row.original_test_name) for row in requirements}
    targets = set()
    for test in load_reference_catalog().tests:
        for name in (test.name, *(alias.text for alias in test.aliases)):
            phrase = normalized_name(name)
            if len(phrase) < 8 or len(phrase.split()) < 2:
                continue
            if contains(normalized_issue, phrase):
                targets.update(key for key, title in titles.items() if contains(title, phrase))
    return targets or None


def document_review_issues(
    run: CustomerExtractionRun, document: IngestedDocument
) -> tuple[str, ...]:
    """Document control questions are shown once, not as one error per test.

    This does not resolve the question or approve a final report. Scientific
    method revisions, conditions, limits and identity concerns remain row-level
    review. The original source warning stays in the saved run/report audit.
    """
    result = []
    for issue in run.extraction.review_issues:
        if literal_method_designations(issue):
            continue
        quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"|«([^»]+)»", issue)
        fragments = [next(value for value in group if value) for group in quoted]
        outside = re.sub(r"'[^']+'|\"[^\"]+\"|«[^»]+»", " ", issue)
        if (
            re.search(r"\b(?:document|header|revision|metadata)\b", outside, re.I)
            and re.search(r"\brevision\b", outside, re.I)
            and re.search(r"\b(?:conflict|ambiguous|controlling|confirmation)\b", outside, re.I)
            and not re.search(
                r"\b(?:method|test|condition|criterion|parameter|unit|temperature|pressure|"
                r"duration|acceptance|limit|procedure)\b|[°%±≤≥<>]", outside, re.I,
            )
            and len(fragments) >= 2
            and all(any(
                re.sub(r"\s+", "", unicodedata.normalize("NFKC", part))
                in re.sub(r"\s+", "", unicodedata.normalize("NFKC", page.text))
                for page in document.pages
            )
                    for part in fragments)
        ):
            result.append(issue)
    return tuple(dict.fromkeys((
        *result, *source_chunk_review_projection(run, document).document_issues,
    )))


def _literal_title_issue_targets(issue, requirements, document):
    """Locate every word of a multiword row title in an explicit concern.

    Word order can differ in a heading and a sentence. All title words must be
    present both in the unquoted concern and on that row's physical source page;
    generic quantities and partial subject overlaps cannot identify an owner.
    This never resolves the scientific question or changes the saved warning.
    """
    outside = re.sub(r"'[^']+'|\"[^\"]+\"|«[^»]+»", " ", issue)
    if re.search(
        r"\b(?:all|every|multiple|several|remaining|other)\s+(?:test|requirement|row|method)|"
        r"\brevision\b|\bmetadata\b", outside, re.I,
    ):
        return None

    def words(text):
        return set(re.findall(r"[^\W_]+", re.sub(
            r"\bexposures\b", "exposure", normalized_name(text),
        )))

    concern = words(outside)
    physical = {page.page_number: words(page.text) for page in document.pages}
    targets = set()
    for row in requirements:
        title = words(row.original_test_name)
        if len(title) < 3 or not title <= concern:
            continue
        if any(title <= physical.get(item.page, set()) for item in row.evidence):
            targets.add(row.id)
    return targets or None


def _quoted_source_issue_targets(
    issue: str, requirements: list[CustomerRequirement], document: IngestedDocument
) -> set[str] | None:
    """Locate literal scientific phrases before falling back to global review."""
    if re.search(
        r"\b(?:all|every|multiple|several|remaining|other)\s+(?:test|requirement|row|method)",
        issue, re.I,
    ):
        return None
    quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"|«([^»]+)»", issue)
    fragments = [next(value for value in group if value) for group in quoted]
    fragments = [part for part in fragments if len(part.strip()) >= 8]
    if not fragments or not all(
        any(citation_contains(page.text, part) for page in document.pages)
        for part in fragments
    ):
        return None
    targets = set()
    for part in fragments:
        titles = {
            row.id for row in requirements if citation_contains(row.original_test_name, part)
        }
        if len(titles) == 1:
            targets.update(titles)
            continue
        owners = {
            row.id for row in requirements
            if any(citation_contains(value, part) for value in [
                *(item.raw_value for item in row.conditions),
                *(item.raw_value for item in row.acceptance_criteria),
                *row.applicability, *row.special_instructions,
                *(item.quote for item in row.evidence),
            ])
        }
        if not owners:
            return None
        targets.update(owners)
    return targets or None


def is_provenance_notice(issue: str, document: IngestedDocument) -> bool:
    """Recognize narrow source-backed disclaimer templates as document notices.

    The original issue remains stored/visible. A warning mixing provenance with
    omitted facts, scientific ambiguity or a decision requirement never matches
    these full-string templates and remains actionable. Synthetic data alone is
    not a reason to make every otherwise valid extraction row require approval.
    """
    source = "\n".join(page.text for page in document.pages).casefold()
    if not re.search(
        r"\bsynthetic\s+(?:benchmark\s+sample|method\s+ids?|test-method|bank)|"
        r"\bmethod\s+(?:ids|identifiers|designations)\s+are\s+(?:synthetic|fictional)",
        source,
    ):
        return False
    text = _REQUIREMENT_ID.sub("", issue).lstrip(" :").strip()
    # Replacing only lexically recognized full designations makes the templates
    # independent of the fixture's particular method numbers, not their meaning.
    for code in sorted(literal_method_designations(text), key=len, reverse=True):
        text = re.sub(re.escape(code).replace(r"\ ", r"\s+"), "METHOD", text, flags=re.I)
    for family in re.findall(r"\b[A-Z]{3}-\d{2}\b", text):
        if family.casefold() in source:
            text = text.replace(family, "METHOD")
    text = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    text = text.replace("’", "'")
    methods = r"method(?:\s*(?:,|and|et|和|与)\s*method)*"
    patterns = (
        r"the document is a synthetic benchmark sample; all cited method designations "
        r"are fictional and no equivalence between " + methods + r" is declared\.?",
        r"the source states that all cited test-method designations \(" + methods + r"\) "
        r"are fictional and that test conditions and limits are buyer-set; "
        r"no published-standard equivalence is asserted\.?",
        r"les identifiants de méthodes cités \(" + methods + r"\) sont déclarés "
        r"synthétiques par le document; aucune équivalence officielle n'est revendiquée\.?",
        r"the source states all method ids \(" + methods + r"\) are synthetic and "
        r"no official equivalence is claimed; method designations are retained exactly "
        r"as printed\.?",
        r"el documento indica que los identificadores de métodos citados son entradas "
        r"sintéticas del banco y no se afirma equivalencia oficial; los códigos se "
        r"conservan tal como están impresos\.?",
        r"方法编号为\s*scione ai\s*方法库\s*synthetic reference[,，]\s*非正式标准[。.]?",
    )
    return any(re.fullmatch(pattern, text) for pattern in patterns)


@dataclass(frozen=True)
class RequirementAssessment:
    """A workflow state with retained, explanatory automatic review flags."""

    state: RequirementState
    reasons: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


def assess_requirements(
    run: CustomerExtractionRun,
    document: IngestedDocument,
    review: RequirementReviewSet | None = None,
    *,
    pdf_bytes: bytes | None = None,
) -> dict[str, RequirementAssessment]:
    """Assess rows without API calls or model self-confidence.

    Source citations are checked again so historical runs receive the same checks
    as new ones. Optional matching PDF bytes allow a formatting-only literal-source
    warning to be resolved when quotation segments form one exact source span or
    identify a unique detected table row, including repeated values in its cells. This
    is source location, not validation that an extracted value is semantically right.
    Model issues naming exact requirement IDs affect only those rows. A method-
    specific issue with fully recognized literal references goes to their owners;
    narrow provenance disclaimers stay document notices. Other unscoped or
    unrouteable issues conservatively flag every row. An explicit human
    confirmation or exclusion determines the displayed state, but does not erase
    automatic reasons or certify completeness. ``needs_review`` remains actionable.
    """

    if run.document_id != document.document_id:
        raise ValueError("Extraction and source document do not match.")
    if review is not None and (
        review.document_id != run.document_id or review.run_id != run.run_id
    ):
        raise ValueError("Human review belongs to a different extraction run.")
    if pdf_bytes is not None and (
        not isinstance(pdf_bytes, bytes) or hashlib.sha256(pdf_bytes).hexdigest() != document.sha256
    ):
        raise ValueError("PDF bytes do not match the source document.")

    requirements = run.extraction.requirements
    reasons: dict[str, list[str]] = {requirement.id: [] for requirement in requirements}
    source_issues = validate_customer_sources(run.extraction, document)
    located_issues = _located_source_issues(run, source_issues, pdf_bytes)
    metadata_diagnostics = nonblocking_notice_issues(run, document)
    shared_diagnostics = nonblocking_shared_clause_issues(run)
    metadata_questions = set(document_review_issues(run, document))
    chunk_projection = source_chunk_review_projection(run, document)
    metadata_questions.difference_update(chunk_projection.row_targets)
    conditional_notes = conditional_source_notices(run, document)
    reference_notes: dict[str, list[str]] = {}
    for audit in method_reference_comment_audits(run, document):
        reference_notes.setdefault(audit["requirement_id"], []).append(audit["original_comment"])
    revision_projection = source_revision_notes(run, document)
    summary_projection = source_summary_reconciliation(run, document)
    unused_model_comments = {
        audit["original_comment"] for audit in review_model_comment_audits(run)
    }
    issues = [
        *run.extraction.review_issues,
        *source_issues,
        *[
            issue for notice in run.extraction.document_notices
            for issue in provenance_source_issues(notice, document)
        ],
    ]
    for issue in issues:
        # Only an exact generated source-check message can be suppressed. Other
        # model cautions remain actionable, even when their source text is located.
        if issue in located_issues:
            continue
        if issue in metadata_diagnostics:
            continue
        if issue in shared_diagnostics:
            continue
        if issue in metadata_questions:
            continue
        if issue in revision_projection.nonblocking_issues:
            continue
        if issue in unused_model_comments:
            continue
        if issue in summary_projection.nonblocking_issues:
            continue
        if is_provenance_notice(issue, document):
            continue
        named = set(_REQUIREMENT_ID.findall(issue))
        # Unknown IDs cannot safely be routed. Do not silently discard such issues.
        if issue in chunk_projection.row_targets:
            targets = chunk_projection.row_targets[issue]
        elif named:
            targets = named if named <= reasons.keys() else reasons.keys()
        else:
            targets = _method_issue_targets(issue, requirements)
            if targets is None and not literal_method_designations(issue):
                targets = (
                    _catalogued_name_issue_targets(issue, requirements)
                    or _literal_title_issue_targets(issue, requirements, document)
                    or _quoted_source_issue_targets(issue, requirements, document)
                )
            targets = targets or reasons.keys()
        for requirement_id in targets:
            if issue in conditional_notes.get(requirement_id, ()):
                continue
            if issue in reference_notes.get(requirement_id, ()):
                continue
            if issue not in reasons[requirement_id]:
                reasons[requirement_id].append(issue)

    assessed: dict[str, RequirementAssessment] = {}
    source_defined = _proved_source_procedures(run, pdf_bytes)
    from scione.specifications.parameter_views import physical_parameter_views
    from scione.structured import normalize_requirement

    views, proofs = physical_parameter_views(requirements, document, pdf_bytes=pdf_bytes)
    scoped_conditions = scoped_specimen_review_reasons(list(views.values()), document)

    coverage_issues = {
        requirement.id: normalize_requirement(
            views[requirement.id], source_unit_proofs=proofs[requirement.id], native_first=True
        ).review_issues
        for requirement in requirements
    }
    for requirement in requirements:
        requirement = views[requirement.id]
        row_reasons = reasons[requirement.id]
        if any(
            action.get("requires_review")
            for action in proofs[requirement.id].get(
                "__source_derivations__", {}
            ).get("actions", [])
        ):
            row_reasons.append(
                "A manually reviewed condition conflicts with its source section; "
                "check its applicability."
            )
        # Omission audits are source-review work, not invented lab demands.
        # Preserve their stable audit IDs and original clauses in the derived
        # normalization artifact while showing the actionable issue once here.
        row_reasons.extend(
            issue for issue in coverage_issues.get(requirement.id, ())
            if "_source_audit_" in issue and issue not in row_reasons
        )
        if requirement.id in scoped_conditions:
            row_reasons.append(scoped_conditions[requirement.id])
        if native_problem := native_scientific_review_reason(requirement):
            row_reasons.append(native_problem)
        if not requirement.requested_methods and requirement.id not in source_defined:
            row_reasons.append("No test method was identified; check the source.")
        if (not requirement.conditions and not requirement.acceptance_criteria
                and not has_explicit_measurement_request(requirement, document)):
            row_reasons.append(
                "No conditions or acceptance criteria were extracted; check the source."
            )

        state: RequirementState = "needs_attention" if row_reasons else "ready"
        decision = review.decisions.get(requirement.id) if review is not None else None
        if decision is not None:
            if decision.status == "confirmed":
                state = "confirmed"
            elif decision.status in {"excluded", "rejected"}:
                state = "excluded"
            elif decision.status == "needs_review":
                state = "needs_attention"
                row_reasons.append("Marked for review by a user.")
        assessed[requirement.id] = RequirementAssessment(
            state, tuple(row_reasons), tuple(dict.fromkeys((
                *conditional_notes.get(requirement.id, ()),
                *reference_notes.get(requirement.id, ()),
                *revision_projection.notices.get(requirement.id, ()),
            )))
        )
    return assessed


def _proved_source_procedures(run: CustomerExtractionRun, pdf_bytes: bytes | None) -> set[str]:
    """A complete located custom-procedure row need not have an ISO/ASTM number.

    Curated identity plus exact source location is required; an omitted standard
    reference, invented/partial citation, or a generic unknown name still needs review.
    This never declares lab support or suppresses other model/source warnings.
    """

    if pdf_bytes is None:
        return set()
    catalog = load_reference_catalog()
    proved = set()
    standard = re.compile(r"\b(?:ISO|ASTM|SAE|VDMA|VDA|DIN|GB\s*/?\s*T?)\s*[A-Z]?\s*\d", re.I)
    for requirement in run.extraction.requirements:
        if _proved_customer_table_method(requirement, catalog, pdf_bytes):
            proved.add(requirement.id)
            continue
        if not is_source_defined_procedure(requirement, catalog):
            continue
        if any(
            standard.search(evidence.quote)
            or len([part for part in evidence.quote.split("|") if part.strip()]) < 2
            or normalized_name(evidence.quote.split("|")[0])
            != normalized_name(requirement.original_test_name)
            for evidence in requirement.evidence
        ):
            continue

        def literal(value: str) -> str:
            normalized = unicodedata.normalize("NFKC", value).casefold().replace("−", "-")
            return " ".join(normalized.split())

        if not all(
            any(
                re.search(
                    r"(?<![\w.+-])" + re.escape(literal(condition.raw_value)) + r"(?![\w.])",
                    literal(evidence.quote),
                )
                for evidence in requirement.evidence
            )
            for condition in requirement.conditions
        ):
            continue
        try:
            if all(
                inspect_pdf_highlights(
                    pdf_bytes, page_number=evidence.page, quotes=(evidence.quote,)
                )
                .matches[0]
                .status
                == "matched"
                for evidence in requirement.evidence
            ):
                proved.add(requirement.id)
        except PDFPreviewError:
            continue
    return proved


def _proved_customer_table_method(requirement, catalog, pdf_bytes: bytes) -> bool:
    """Locate a printed custom procedure without inventing a standard reference.

    A unique complete physical row must own the exact test label and an exact
    curated customer-procedure phrase in its method column. Shared conditions,
    source-quantity audits and all other concerns are still checked separately.
    This only removes the missing-method review prompt; it establishes no lab
    capability and never changes the machine extraction or requested methods.
    """
    if requirement.requested_methods or not requirement.conditions:
        return False
    name = normalized_name(requirement.original_test_name)
    tests = [test for test in catalog.tests if name in {
        normalized_name(test.name), *(normalized_name(alias.text) for alias in test.aliases)
    }]
    if len(tests) != 1:
        return False
    methods = [
        catalog.method_by_id(identifier) for identifier in tests[0].method_ids
        if identifier.startswith("customer_")
    ]
    if not methods:
        return False
    phrases = {
        pdf_quote_tokens(text.removeprefix("Customer procedure: "))
        for method in methods for text in [method.identifier, *(a.text for a in method.aliases)]
    }
    for evidence in requirement.evidence:
        try:
            cells = matched_geometric_table_row(
                pdf_bytes, evidence.page, evidence.quote,
                expected_label=requirement.original_test_name,
            )
        except PDFPreviewError:
            continue
        if cells is not None and len(cells) >= 2 and cells[1] in phrases:
            return True
    return False


def _located_source_issues(
    run: CustomerExtractionRun, source_issues: list[str], pdf_bytes: bytes | None
) -> set[str]:
    """Recognize harmless quotation-format differences without rewriting warnings."""

    if pdf_bytes is None or not source_issues:
        return set()
    located: set[str] = set()
    by_page: dict[int, list[tuple[str, list[str]]]] = {}
    for requirement in run.extraction.requirements:
        for index, evidence in enumerate(requirement.evidence, start=1):
            prefix = f"{requirement.id} evidence {index}: quoted text was not found "
            matching_issues = [issue for issue in source_issues if issue.startswith(prefix)]
            if not matching_issues:
                continue
            by_page.setdefault(evidence.page, []).append((evidence.quote, matching_issues))
    for page, entries in by_page.items():
        for offset in range(0, len(entries), 100):
            batch = entries[offset : offset + 100]
            try:
                result = inspect_pdf_highlights(
                    pdf_bytes, page_number=page, quotes=tuple(quote for quote, _ in batch)
                )
            except PDFPreviewError:
                # Rendering/locator limitations are never evidence of a good row.
                continue
            if len(result.matches) != len(batch):
                continue
            for match, (_, matching_issues) in zip(result.matches, batch, strict=True):
                if match.status == "matched":
                    located.update(matching_issues)
    return located

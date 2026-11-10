"""Join saved source artifacts, the SQL archive and explainable comparisons."""

from __future__ import annotations

from collections import Counter

from fastapi import HTTPException

from scione.library.candidate_display import candidate_lab_entries
from scione.library.store import LibraryStore, digest
from scione.parameters import REGISTRY_VERSION
from scione.reports.matching import lab_fingerprint
from scione.specifications.extraction import coverage_issues
from scione.specifications.review import (
    REVIEW_POLICY_VERSION,
    assess_requirements,
    document_review_issues,
)
from scione.structured import compare_requirement, normalize_capability
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim

COMPARISON_POLICY_VERSION = "decisive_rules_first_v5"


class LibraryWorkspace:
    def __init__(self, workspace):
        self.workspace = workspace
        settings = workspace.settings
        self.store = LibraryStore(
            settings.scione_workspace_dir,
            settings.scione_database_url.get_secret_value()
            if settings.scione_database_url
            else None,
        )

    def index(self, document_id: str):
        stored = self.workspace.current_document(document_id)
        run = self.workspace.specs.load_latest_extraction(document_id)
        self.store.ensure_document(stored, run.extraction.document if run else None)
        if run:
            _, normalized = self.workspace.comparison_source(document_id, run)
            self.store.save_requirements(run, normalized)
        return stored, run

    def document(self, document_id: str) -> dict:
        stored, run = self.index(document_id)
        run, review, _revision = (
            self.workspace.effective_source(document_id, run) if run else (None, None, None)
        )
        return self._document_view(stored, run, review)

    def _document_view(self, stored, run, review) -> dict:
        """Build metadata around one captured source/review, not another latest read."""
        row = self.store.document(stored.document_id)
        assessments = (
            assess_requirements(
                run,
                stored.document,
                review,
                pdf_bytes=self.workspace.specs.read_pdf(stored.document_id),
            )
            if run
            else {}
        )
        return {
            **row,
            "page_count": stored.document.page_count,
            "run_id": run.run_id if run else None,
            "provider": run.provider if run else None,
            "requirement_count": len(run.extraction.requirements) if run else 0,
            "review_count": sum(item.state == "needs_attention" for item in assessments.values()),
            "document_review_issues": list(document_review_issues(run, stored.document))
            if run
            else [],
        }

    def listing(self) -> dict:
        return {
            "documents": [
                self.document(item.document_id) for item in self.workspace.specs.list_documents()
            ]
        }

    def detail(self, document_id: str) -> dict:
        stored, machine = self.index(document_id)
        effective, review, revision = (
            self.workspace.effective_source(document_id, machine) if machine else (None, None, None)
        )
        document = self._document_view(stored, effective, review)
        normalized_effective = (
            {
                row.requirement_id: row
                for row in self.workspace.comparison_source(document_id, effective)[1]
            }
            if effective and revision
            else {}
        )
        rows = (
            [
                {
                    "requirement_id": item.id,
                    "test_name": item.original_test_name,
                    "methods": [method.code for method in item.requested_methods],
                    "claims": [
                        claim.model_dump(mode="json")
                        for claim in normalized_effective[item.id].claims
                    ],
                }
                for item in effective.extraction.requirements
            ]
            if revision
            else self.store.load_requirements(document["run_id"])
            if document["run_id"]
            else []
        )
        for row in rows:
            row["claims"] = [
                self.present_claim(AtomicClaim.model_validate(item)) for item in row["claims"]
            ]
        versions = [
            item
            for item in self.listing()["documents"]
            if item["family_id"] == document["family_id"]
        ]
        return {
            "document": document,
            "versions": versions,
            "requirements": rows,
            "facts_revision_id": revision.revision_id if revision else None,
        }

    @staticmethod
    def present_claim(claim: AtomicClaim) -> dict:
        return {
            **claim.model_dump(mode="json"),
            "display_value": claim.display_value,
            "source_page": claim.source_pages[0] if claim.source_pages else None,
            "status": claim.normalization_state,
        }

    def metadata(self, document_id: str, body) -> dict:
        self.index(document_id)
        values = body.model_dump(exclude={"expected_version"})
        self.store.edit_metadata(document_id, values, body.expected_version)
        return self.detail(document_id)

    def comparison(self, document_id: str) -> dict:
        # The report captures exactly one immutable extraction plus its review,
        # lab and matching decisions. Derive every exported field from it.
        report = self.workspace.draft(document_id)
        run = report.source_run
        stored = self.workspace.current_document(document_id)
        self.store.ensure_document(stored, run.extraction.document)
        document = self._document_view(stored, run, report.source_review)
        # Machine extraction rows are immutable. Reviewed facts have their own
        # SQL revision and normalized snapshot, never overwrite this raw index.
        machine = self.workspace.specs.load_extraction(document_id, run.run_id)
        self.store.save_requirements(
            machine, self.workspace.comparison_source(document_id, machine)[1]
        )
        rule_normalized = {
            row.requirement_id: row for row in self.workspace.comparison_source(document_id, run)[1]
        }
        fingerprint = lab_fingerprint(report.lab)
        self.store.save_capability_revision(report.lab, fingerprint)
        matching_run, semantic_comparisons, semantic_capabilities = (
            self.workspace.matching.compiled(report)
        )
        rows = []
        for line in report.lines:
            requirement = line.requirement
            method_status = "unknown"
            if line.status in {"test_gap", "method_gap"}:
                method_status = "gap"
            elif line.capability and not line.unknown_methods and line.status != "needs_review":
                method_status = "supported"
            rule_claims = normalize_capability(line.capability) if line.capability else []
            rule_comparison = compare_requirement(
                rule_normalized[requirement.id], rule_claims, method_status
            )
            # An AI deferral must not undo a complete, independently grounded
            # comparison. This is deliberately item-scoped: unresolved claims
            # still use only validated semantic links, never a per-claim fallback
            # to another similarly named or differently configured declaration.
            decisive_rules = (
                line.mapping_choice is None
                and line.match_basis == "Exact curated name / alias"
                and line.status in {"in_house", "condition_gap"}
                and line.capability is not None
                and not line.unknown_methods
                and len({claim.configuration for claim in rule_claims if claim.configuration}) <= 1
                and rule_comparison.unknown_count == 0
                and line.extraction_review_state != "needs_attention"
            )
            # Explicit human identity choices remain authoritative. Machine
            # proposals are independently saved and must not replace reviews.
            use_semantic = (
                matching_run is not None
                and line.mapping_choice is None
                and not decisive_rules
                and line.status != "excluded"
                and line.extraction_review_state != "excluded"
            )
            capability = (
                semantic_capabilities.get(requirement.id) if use_semantic else line.capability
            )
            methods = line.considered_method_codes or [
                item.code for item in requirement.requested_methods
            ]
            if line.status == "excluded" or line.extraction_review_state == "excluded":
                comparison = {
                    "requirement_id": requirement.id,
                    "status": "excluded",
                    "checks": [],
                    "supported_count": 0,
                    "gap_count": 0,
                    "unknown_count": 0,
                }
            else:
                result = (
                    semantic_comparisons[requirement.id].model_copy(deep=True)
                    if use_semantic
                    else rule_comparison
                )
                if capability is None and method_status == "unknown":
                    for check in result.checks:
                        claim = check.requirement_claim
                        if (
                            check.status == "unknown"
                            and check.kind != "method"
                            and claim is not None
                            and claim.parameter_id
                            and claim.normalization_state != "unresolved"
                        ):
                            check.declared = "Not compared: confirm test/method"
                            check.reason = (
                                "Confirm the requested test/method before comparing this "
                                "requirement with lab declarations."
                            )
                if result.checks:
                    result.checks[0].requested = ", ".join(methods) or "Customer-defined procedure"
                    result.checks[0].declared = (
                        ", ".join(
                            report.catalog.method_by_id(identifier).identifier
                            for identifier in capability.method_ids
                        )
                        if use_semantic and capability
                        else ", ".join(line.lab_method_identifiers)
                    ) or None
                comparison = result.model_dump(mode="json")
                suggestions = candidate_lab_entries(
                    matching_run if use_semantic else None,
                    requirement.id,
                    capability.id if capability else None,
                )
                for check in comparison["checks"]:
                    if check["status"] == "unknown" and not check.get("declared"):
                        entries = suggestions.get(check["id"], [])
                        if entries:
                            check["candidate_lab_entries"] = entries
                choice = line.mapping_choice
                if (
                    choice
                    and choice.lab_fingerprint == fingerprint
                    and choice.capability_verdict == "unsupported"
                ):
                    comparison["checks"].append(
                        {
                            "id": f"{requirement.id}_lab_review",
                            "parameter_id": "lab_scope_review",
                            "label": "Lab scope decision",
                            "kind": "scope",
                            "stage": "general",
                            "requested": "The complete requested test scope",
                            "declared": choice.note,
                            "status": "gap",
                            "unit": None,
                            "reason": "The lab recorded that it cannot meet this scope.",
                            "source_pages": [],
                        }
                    )
                    comparison["gap_count"] += 1
                    comparison["status"] = (
                        "partially_matched" if comparison["supported_count"] else "not_matched"
                    )
                if len(requirement.requested_methods) > len(methods):
                    comparison["checks"].append(
                        {
                            "id": f"{requirement.id}_alternative",
                            "parameter_id": "conditional_method",
                            "label": "Conditional alternative",
                            "kind": "instruction",
                            "stage": "general",
                            "requested": "; ".join(
                                f"{item.code}: {item.role or 'Conditional source reference'}"
                                for item in requirement.requested_methods
                                if item.code not in methods
                            ),
                            "declared": None,
                            "status": "informational",
                            "unit": None,
                            "reason": (
                                "The required route is assessed. Conditional alternatives remain "
                                "subject to the source's approval requirements."
                            ),
                            "source_pages": sorted({item.page for item in requirement.evidence}),
                        }
                    )
                if line.extraction_review_state == "needs_attention":
                    comparison["checks"].append(
                        {
                            "id": f"{requirement.id}_source",
                            "parameter_id": "source_review",
                            "label": "Source text",
                            "kind": "source",
                            "stage": "general",
                            "requested": "Check the extracted requirement against the PDF",
                            "declared": None,
                            "status": "unknown",
                            "unit": None,
                            "reason": "; ".join(line.extraction_review_reasons)
                            or "Source verification is pending.",
                            "source_pages": sorted({item.page for item in requirement.evidence}),
                        }
                    )
                    comparison["unknown_count"] += 1
                    if comparison["status"] == "fully_matched":
                        comparison["status"] = "needs_information"
            rows.append(
                {
                    "requirement_id": requirement.id,
                    "test_name": requirement.original_test_name,
                    "methods": methods,
                    "capability_id": capability.id if capability else None,
                    "source_review_state": line.extraction_review_state,
                    "comparison_engine": (
                        "ai_semantic_matching" if use_semantic else "deterministic_rules"
                    ),
                    "report_scope_exclusion": (
                        line.report_scope_exclusion.model_dump(mode="json")
                        if line.report_scope_exclusion
                        else None
                    ),
                    "comparison": comparison,
                }
            )
        summary = {
            key: 0
            for key in (
                "fully_matched",
                "partially_matched",
                "not_matched",
                "needs_information",
                "excluded",
            )
        }
        summary.update(Counter(item["comparison"]["status"] for item in rows))
        source_coverage = coverage_issues(run.extraction, stored.document)
        payload = {
            "document": document,
            "lab_name": report.lab.name,
            "lab_version": report.lab.version,
            "normalizer_version": NORMALIZER_VERSION,
            "parameter_policy_version": REGISTRY_VERSION,
            "comparison_policy_version": COMPARISON_POLICY_VERSION,
            "source_review_policy_version": REVIEW_POLICY_VERSION,
            "requirements": rows,
            "summary": summary,
            "source_coverage_issues": source_coverage,
            "notes": [
                "Draft capability assessment. It does not predict specimen pass/fail.",
                "Missing lab information remains unresolved; it is not an explicit limitation.",
                *document["document_review_issues"],
                *source_coverage,
            ],
            "source_run_id": run.run_id,
            "source_facts_revision_id": report.source_facts_revision_id,
            "lab_fingerprint": fingerprint,
            "matching_run_id": matching_run.run_id if matching_run else None,
            "comparison_engine": "ai_semantic_matching" if matching_run else "deterministic_rules",
            "report_input_fingerprint": report.input_fingerprint,
            "report_workspace_version": report.workspace.version,
        }
        self._assert_comparison_inputs_current(report, document)
        # A policy upgrade can change derived warnings or presentation while
        # source/lab inputs stay identical. Address the complete computed view
        # so it creates a new immutable assessment instead of trying to replace
        # the earlier snapshot with the same input-based identifier.
        identity = digest(payload)
        saved = self.store.save_comparison(identity, payload)
        # Pagination is presentation metadata; the persisted comparison remains immutable.
        return saved

    def _assert_comparison_inputs_current(self, report, document: dict) -> None:
        """Reject a stale analysis/review/lab/metadata race before publishing it.

        All payload data already comes from the captured report, so even a
        change after this optimistic check cannot mix different source runs.
        """
        self.workspace.latest(report.document_id, report.source_run.run_id)
        current = self.workspace.draft(report.document_id)
        if current.input_fingerprint != report.input_fingerprint:
            raise HTTPException(
                409, "Source, review or lab information changed. Recompare the latest saved data."
            )
        metadata = self.store.document(report.document_id)
        if any(document.get(key) != value for key, value in metadata.items()):
            raise HTTPException(
                409, "Specification details changed. Reload before comparing again."
            )

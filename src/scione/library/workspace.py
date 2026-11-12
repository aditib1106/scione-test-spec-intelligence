"""Join saved source artifacts, the SQL archive and explainable comparisons."""

from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from threading import Lock, RLock

from fastapi import HTTPException

from scione.library.assessment_reviews import SQLAssessmentReviews
from scione.library.candidate_display import candidate_lab_entries
from scione.library.comparison_explanations import present_comparison_explanations
from scione.library.store import LibraryStore, digest
from scione.matching.batching import MATCHING_BATCH_POLICY_VERSION
from scione.matching.models import MATCHING_PROMPT_VERSION, MATCHING_VALIDATION_VERSION
from scione.matching.projection import MATCHING_PROJECTION_VERSION
from scione.matching.retrieval import RETRIEVAL_POLICY_VERSION
from scione.parameters import REGISTRY_VERSION
from scione.reports.matching import lab_fingerprint
from scione.specifications.document_notes import document_notes_view
from scione.specifications.extraction import coverage_issues
from scione.specifications.review import (
    REVIEW_POLICY_VERSION,
    assess_requirements,
    document_review_issues,
)
from scione.structured import compare_requirement, normalize_capability
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim

COMPARISON_POLICY_VERSION = "native_atoms_first_v6"
EXPORT_PRESENTATION_VERSION = "lab_gap_pdf_v2"
_READ_CACHE_INITIALIZATION = Lock()
_READ_CACHE_LIMIT = 32
_COMPARISON_CACHE_LIMIT = 8


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

    def _read_caches(self):
        # Tenant library subclasses reuse a provisioned SQL store without
        # calling this constructor. Initialize per-instance caches lazily;
        # there is never a process-wide cache of another lab's source data.
        if not hasattr(self, "_read_cache_lock"):
            with _READ_CACHE_INITIALIZATION:
                if not hasattr(self, "_read_cache_lock"):
                    self._indexed_sources = OrderedDict()
                    self._source_assessments = OrderedDict()
                    self._comparison_views = OrderedDict()
                    self._read_cache_lock = RLock()
        return self._read_cache_lock, self._indexed_sources, self._source_assessments

    @staticmethod
    def _remember(cache, key, value, *, limit=_READ_CACHE_LIMIT):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _source_signature(self, stored, run, review=None):
        # Hash the actual, freshly validated source/facts/review contents, not
        # timestamps. SQL facts revisions and human decisions can change while
        # the machine run and PDF remain identical.
        return digest(
            {
                "document": stored.document.model_dump(mode="json"),
                "extraction": run.extraction.model_dump(mode="json") if run else None,
                "run_id": run.run_id if run else None,
                "review": review.model_dump(mode="json") if review else None,
                "normalizer": NORMALIZER_VERSION,
                "registry": REGISTRY_VERSION,
                "review_policy": REVIEW_POLICY_VERSION,
                "catalog": [self.workspace.catalog.id, self.workspace.catalog.version],
            }
        )

    def index(self, document_id: str):
        stored, run, _pdf = self._index_source(document_id)
        return stored, run

    def _index_source(self, document_id: str):
        """Return a request-local PDF for the adjacent source review to reuse.

        The bytes come from this authorized source store and never enter a
        longer-lived cache. A source-index miss needs physical normalization
        and review; both should inspect the same immutable PDF once.
        """
        stored = self.workspace.current_document(document_id)
        run = self.workspace.specs.load_latest_extraction(document_id)
        self.store.ensure_document(stored, run.extraction.document if run else None)
        pdf = None
        if run:
            signature = self._source_signature(stored, run)
            lock, indexed, _assessments = self._read_caches()
            with lock:
                if indexed.get(document_id) != signature:
                    pdf = self.workspace.specs.read_pdf(document_id)
                    _, normalized = self.workspace.comparison_source(
                        document_id, run, pdf_bytes=pdf
                    )
                    self.store.save_requirements(run, normalized)
                    self._remember(indexed, document_id, signature)
                else:
                    indexed.move_to_end(document_id)
        return stored, run, pdf

    def document(self, document_id: str) -> dict:
        stored, run, pdf = self._index_source(document_id)
        run, review, _revision = (
            self.workspace.effective_source(document_id, run) if run else (None, None, None)
        )
        return self._document_view(stored, run, review, pdf_bytes=pdf)

    def _document_view(self, stored, run, review, *, pdf_bytes=None) -> dict:
        """Build metadata around one captured source/review, not another latest read."""
        row = self.store.document(stored.document_id)
        signature = self._source_signature(stored, run, review)
        lock, _indexed, cached = self._read_caches()
        with lock:
            summary = cached.get(signature)
            if summary is None:
                assessments = (
                    assess_requirements(
                        run,
                        stored.document,
                        review,
                        pdf_bytes=(
                            self.workspace.specs.read_pdf(stored.document_id)
                            if pdf_bytes is None else pdf_bytes
                        ),
                    )
                    if run
                    else {}
                )
                summary = (
                    sum(item.state == "needs_attention" for item in assessments.values()),
                    tuple(document_review_issues(run, stored.document)) if run else (),
                )
                self._remember(cached, signature, summary)
            else:
                cached.move_to_end(signature)
        return {
            **row,
            "page_count": stored.document.page_count,
            "run_id": run.run_id if run else None,
            "provider": run.provider if run else None,
            "requirement_count": len(run.extraction.requirements) if run else 0,
            "review_count": summary[0],
            "document_review_issues": list(summary[1]),
        }

    def listing(self) -> dict:
        return {
            "documents": [
                self.document(item.document_id) for item in self.workspace.active_documents()
            ]
        }

    def detail(self, document_id: str) -> dict:
        stored, machine, pdf = self._index_source(document_id)
        effective, review, revision = (
            self.workspace.effective_source(document_id, machine) if machine else (None, None, None)
        )
        document = self._document_view(stored, effective, review, pdf_bytes=pdf)
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
        if "specification_date" not in body.model_fields_set:
            values.pop("specification_date", None)
        self.store.edit_metadata(document_id, values, body.expected_version)
        return self.detail(document_id)

    def comparison(self, document_id: str) -> dict:
        # The report captures exactly one immutable extraction plus its review,
        # lab and matching decisions. Derive every exported field from it.
        inputs = self.workspace.capture_draft_inputs(document_id)
        stored, run = inputs["stored"], inputs["run"]
        self.store.ensure_document(stored, run.extraction.document)
        document = self._document_view(stored, run, inputs["review"])
        matching_revision = digest(self.workspace.matching.store.listing(document_id))
        signature = digest({
            "inputs": self.workspace.captured_draft_fingerprint(inputs),
            "metadata": document,
            "matching_revision": matching_revision,
            # A patched/reloaded implementation cannot reuse an earlier pure
            # derivation within this process, even before a policy bump.
            "implementations": [id(coverage_issues), id(type(self.workspace).draft_from_inputs)],
            "policies": [
                COMPARISON_POLICY_VERSION, NORMALIZER_VERSION, REGISTRY_VERSION,
                REVIEW_POLICY_VERSION, MATCHING_PROMPT_VERSION, MATCHING_VALIDATION_VERSION,
                MATCHING_PROJECTION_VERSION, MATCHING_BATCH_POLICY_VERSION,
                RETRIEVAL_POLICY_VERSION,
            ],
        })
        lock, _indexed, _assessments = self._read_caches()
        with lock:
            cached = self._comparison_views.get(signature)
            if cached is not None:
                self._comparison_views.move_to_end(signature)
                report, saved, explanations = deepcopy(cached)
        if cached is not None:
            self._assert_comparison_inputs_current(report, document, matching_revision)
            return self._reviewed_comparison(saved, explanations=explanations, source_run=run)
        report = self.workspace.draft_from_inputs(inputs)
        run = report.source_run
        # Machine extraction rows are immutable. Reviewed facts have their own
        # SQL revision and normalized snapshot, never overwrite this raw index.
        machine = self.workspace.specs.load_extraction(document_id, run.run_id)
        self.store.save_requirements(
            machine, self.workspace.comparison_source(document_id, machine)[1]
        )
        rule_normalized = {
            row.requirement_id: row
            for row in self.workspace.comparison_source(document_id, run, native_first=True)[1]
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
        self._assert_comparison_inputs_current(report, document, matching_revision)
        # A policy upgrade can change derived warnings or presentation while
        # source/lab inputs stay identical. Address the complete computed view
        # so it creates a new immutable assessment instead of trying to replace
        # the earlier snapshot with the same input-based identifier.
        identity = digest(payload)
        saved = self.store.save_comparison(identity, payload)
        explanations = present_comparison_explanations(saved, matching_run)
        with lock:
            self._remember(
                self._comparison_views, signature, deepcopy((report, saved, explanations)),
                limit=_COMPARISON_CACHE_LIMIT,
            )
        return self._reviewed_comparison(saved, explanations=explanations, source_run=run)

    def _reviewed_comparison(
        self, saved: dict, *, explanations: dict | None = None, source_run=None
    ) -> dict:
        # Pagination is presentation metadata; the persisted comparison remains immutable.
        reviews = SQLAssessmentReviews(
            self.store, self.workspace.labs.workspace_id
        ).latest_for_assessment(saved)
        # Human decisions do not alter machine identity or its immutable
        # checks. Export a separately addressed snapshot so PDF/JSON includes
        # the exact saved decision versions, while earlier exports stay fixed.
        export = self.store.save_comparison(
            digest([EXPORT_PRESENTATION_VERSION, saved["comparison_id"], reviews]),
            {**saved, "machine_comparison_id": saved["comparison_id"],
             "assessment_reviews": reviews, "export_format_version": EXPORT_PRESENTATION_VERSION},
        )
        return {**(explanations if explanations is not None else saved),
                **({"assessment_reviews": reviews} if reviews else {}),
                "document_notes_review": document_notes_view(
                    self.workspace, saved["document"]["document_id"], run=source_run
                ),
                "export_snapshot_id": export["comparison_id"]}

    def review_assessment(self, document_id: str, body) -> dict:
        current = self.comparison(document_id)
        if (
            current["comparison_id"] != body.comparison_id
            or current["report_input_fingerprint"] != body.input_fingerprint
        ):
            raise HTTPException(409, "The assessment changed. Refresh before reviewing it.")
        requirement = next(
            (
                row
                for row in current["requirements"]
                if row["requirement_id"] == body.requirement_id
            ),
            None,
        )
        if requirement is None:
            raise HTTPException(400, "Select a test from this assessment.")
        if requirement["comparison"]["status"] == "excluded":
            raise HTTPException(400, "Restore this test before reviewing its assessment.")
        SQLAssessmentReviews(self.store, self.workspace.labs.workspace_id).save(
            current, requirement, body
        )
        updated = self.comparison(document_id)
        if updated["comparison_id"] != current["comparison_id"]:
            raise HTTPException(
                409, "The assessment changed while saving. Review the current result."
            )
        return updated

    def _assert_comparison_inputs_current(
        self, report, document: dict, matching_revision: str | None = None
    ) -> None:
        """Reject a stale analysis/review/lab/metadata race before publishing it.

        All payload data already comes from the captured report, so even a
        change after this optimistic check cannot mix different source runs.
        """
        self.workspace.latest(report.document_id, report.source_run.run_id)
        current = self.workspace.capture_draft_inputs(report.document_id)
        if self.workspace.captured_draft_fingerprint(current) != report.input_fingerprint:
            raise HTTPException(
                409, "Source, review or lab information changed. Recompare the latest saved data."
            )
        metadata = self.store.document(report.document_id)
        if any(document.get(key) != value for key, value in metadata.items()):
            raise HTTPException(
                409, "Specification details changed. Reload before comparing again."
            )
        if matching_revision is not None and digest(
            self.workspace.matching.store.listing(report.document_id)
        ) != matching_revision:
            raise HTTPException(409, "The saved analysis changed. Refresh the current report.")

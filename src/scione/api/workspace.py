"""Translate local domain operations to HTTP without changing persisted contracts."""

from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from scione.api.contracts import (
    ExclusionReview,
    MappingReview,
    QuantityReview,
    ReportEdit,
    ScopeReview,
    SourceFactsEdit,
    SourceRequirementAdd,
)
from scione.api.public_views import current_actor
from scione.catalogs import load_reference_catalog
from scione.catalogs.sql_lab_store import SQLLabStore
from scione.config import Settings
from scione.ingestion import IngestionError
from scione.ingestion.source_views import current_document_view
from scione.library.store import LibraryConflict, digest
from scione.library.workspace import LibraryWorkspace
from scione.matching.workspace import MatchingWorkspace
from scione.reports.matching import (
    build_report,
    lab_fingerprint,
    support_basis,
)
from scione.reports.matching import (
    input_fingerprint as report_input_fingerprint,
)
from scione.reports.models import (
    GapLine,
    MappingChoice,
    QuoteChoice,
    ReportScopeAction,
    ReportSnapshot,
)
from scione.reports.review import batch_eligible, needs_review, quote_eligible, scope_review_state
from scione.reports.store import FileReportStore
from scione.specifications.context_coverage import source_context_coverage_audits
from scione.specifications.corrections import SQLFactsStore, effective_facts
from scione.specifications.extraction import coverage_issues
from scione.specifications.manual_atoms import validate_parameter_edits
from scione.specifications.parameter_view import parameter_review_view
from scione.specifications.parameter_views import (
    physical_normalized_requirements,
    physical_parameter_views,
    physical_unit_proofs,
)
from scione.specifications.pdf_preview import inspect_pdf_highlights
from scione.specifications.public_errors import public_analysis_error
from scione.specifications.review import (
    REVIEW_POLICY_VERSION,
    assess_requirements,
    document_review_issues,
)
from scione.specifications.review_comments import (
    method_reference_comment_audits,
    review_model_comment_audits,
)
from scione.specifications.revision_notes import source_revision_notes
from scione.specifications.store import FileSpecStore, SpecStoreError
from scione.specifications.summary_reconciliation import source_summary_reconciliation
from scione.structured import normalize_requirement


class Workspace:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog = load_reference_catalog()
        self.specs = FileSpecStore(settings.scione_workspace_dir)
        self.reports = FileReportStore(settings.scione_workspace_dir)
        self.library = LibraryWorkspace(self)
        self.labs = SQLLabStore(
            self.library.store, self.catalog, legacy_root=settings.scione_workspace_dir
        )
        self.matching = MatchingWorkspace(self)
        self.facts = SQLFactsStore(self.library.store, self.labs.workspace_id)
        self.specs.added_requirement_ids = self._added_requirement_ids

    def current_document(self, document_id: str):
        """Use a versioned readable view; the originally ingested file stays saved."""
        stored = self.specs.load_document(document_id)
        try:
            document = current_document_view(
                self.settings.scione_workspace_dir,
                stored.document,
                self.specs.root / document_id / "original.pdf",
            )
        except IngestionError as exc:
            raise SpecStoreError(
                "This PDF's readable text could not be prepared. The original file "
                "and earlier results are preserved."
            ) from exc
        return replace(stored, document=document)

    def comparison_source(self, document_id, run, *, native_first=True):
        """A detached, physically checked normalization input, never an archive edit."""
        document = self.current_document(document_id).document
        views, proofs = physical_parameter_views(run.extraction.requirements, document)
        normalized = [
            normalize_requirement(
                views[row.id], source_unit_proofs=proofs[row.id], native_first=native_first
            )
            for row in run.extraction.requirements
        ]
        extraction = run.extraction.model_copy(update={"requirements": list(views.values())})
        return run.model_copy(update={"extraction": extraction}), normalized

    def source_review_audits(self, document_id, run):
        """Export the evidence behind derived triage, separately from raw output.

        Matching captures this bundle before inference. Developer diagnostics
        expose a current-policy replay; neither path rewrites a source run.
        """
        document = self.current_document(document_id).document
        return {
            "review_policy_version": REVIEW_POLICY_VERSION,
            "source_run_id": run.run_id,
            "document_sha256": document.sha256,
            "summary_reconciliation": list(source_summary_reconciliation(run, document).proofs),
            "revision_notes": list(source_revision_notes(run, document).proofs),
            "unused_model_comments": review_model_comment_audits(run),
            "method_reference_notes": method_reference_comment_audits(run, document),
            "context_retention_hints": source_context_coverage_audits(
                run.extraction, document
            ),
            "physical_unit_citations": [
                {"requirement_id": row.id, **proof}
                for row in run.extraction.requirements
                for proof in physical_unit_proofs(
                    row, document, run.extraction.requirements
                ).values()
            ],
        }

    def _added_requirement_ids(self, document_id: str, run_id: str) -> set[str]:
        revision = self.facts.latest(document_id, run_id)
        return (
            {item.id for item in revision.requirements if item.id.startswith("req_human_")}
            if revision
            else set()
        )

    def effective_source(self, document_id: str, run=None):
        run = run or self.specs.load_latest_extraction(document_id)
        if run is None:
            return None, None, None
        review = self.specs.load_review(document_id, run.run_id)
        revision = self.facts.latest(document_id, run.run_id)
        effective, review = effective_facts(run, review, revision)
        return effective, review, revision

    def correct_facts(self, document_id: str, body: SourceFactsEdit) -> dict:
        # The shared file guard also blocks extraction/review writes while the
        # SQL correction is committed. No two-store update is required.
        with self.specs.write_guard():
            run = self.latest(document_id, body.run_id)
            review = self.specs.load_review(document_id, run.run_id)
            if review.version != body.expected_review_version:
                raise LibraryConflict("Source review changed. Reload before correcting content.")
            current, _, revision = self.effective_source(document_id, run)
            if (revision.version if revision else 0) != body.expected_version:
                raise LibraryConflict(
                    "Extracted content changed. Reload before saving corrections."
                )
            self._validate_source_edits(document_id, current, body.requirements)
            self.facts.save(
                run,
                body.requirements,
                body.note,
                expected_version=body.expected_version,
                normalized_requirements=physical_normalized_requirements(
                    body.requirements, self.current_document(document_id).document
                ),
            )
        return self.specification(document_id)

    def add_source_requirement(self, document_id: str, body: SourceRequirementAdd) -> dict:
        """Recover an omitted source test without rewriting the AI's raw output."""
        with self.specs.write_guard():
            run = self.latest(document_id, body.run_id)
            review = self.specs.load_review(document_id, run.run_id)
            if review.version != body.expected_review_version:
                raise LibraryConflict("Source review changed. Reload before adding a test.")
            current, _, revision = self.effective_source(document_id, run)
            if (revision.version if revision else 0) != body.expected_version:
                raise LibraryConflict("Extracted content changed. Reload before adding a test.")
            if len(current.extraction.requirements) >= 1000:
                raise HTTPException(400, "This specification already has 1,000 test items.")
            added = body.requirement.model_copy(
                deep=True, update={"id": f"req_human_{uuid4().hex}", "atomic_claims": []}
            )
            # Identical source-backed entries should be edited, not added twice.
            if any(
                item.model_dump(exclude={"id", "atomic_claims"})
                == added.model_dump(exclude={"id", "atomic_claims"})
                for item in current.extraction.requirements
            ):
                raise HTTPException(400, "This test is already listed. Edit the existing item.")
            requirements = [*current.extraction.requirements, added]
            self._validate_source_edits(document_id, current, requirements)
            self.facts.save(
                run,
                requirements,
                body.note,
                expected_version=body.expected_version,
                allow_additions=True,
                normalized_requirements=physical_normalized_requirements(
                    requirements, self.current_document(document_id).document
                ),
            )
        return self.specification(document_id)

    def _validate_source_edits(self, document_id, current, requirements):
        previous = {item.id: item for item in current.extraction.requirements}
        pdf = self.specs.read_pdf(document_id)
        document = self.current_document(document_id).document
        for item in requirements:
            old = previous.get(item.id)
            if item == old:
                continue
            if (
                old
                and (
                    item.conditions != old.conditions
                    or item.acceptance_criteria != old.acceptance_criteria
                    or item.applicability != old.applicability
                    or item.special_instructions != old.special_instructions
                )
                and item.atomic_claims == old.atomic_claims
            ):
                item.atomic_claims = []
            for evidence in item.evidence:
                located = inspect_pdf_highlights(
                    pdf, page_number=evidence.page, quotes=(evidence.quote,)
                )
                if not located.matches or located.matches[0].status != "matched":
                    raise HTTPException(
                        400,
                        "Locate each test's quotation in the source PDF before saving. "
                        "Copy the source wording and select its page.",
                    )
            try:
                validate_parameter_edits(
                    old, item, source_unit_proofs=physical_unit_proofs(item, document, requirements)
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

    def overview(self) -> dict:
        providers = []
        choices = [
            ("groq", "Groq"),
            ("gemini", "Gemini"),
            ("fireworks", "Fireworks"),
            ("sail", "Sail"),
        ]
        preferred = self.settings.model_provider.strip().casefold()
        if self.settings.provider_configured(preferred):
            choices.sort(key=lambda item: item[0] != preferred)
        for provider, label in choices:
            available = self.settings.provider_configured(provider)
            providers.append(
                {
                    "id": provider,
                    "label": label,
                    "model": getattr(self.settings, f"{provider}_model"),
                    "available": available,
                    "output_limit": (
                        self.settings.groq_max_completion_tokens
                        if provider == "groq"
                        else self.settings.scione_customer_gemini_output_tokens
                        if provider == "gemini"
                        else getattr(self.settings, f"{provider}_max_output_tokens")
                    ),
                }
            )
        return {
            "lab": self.labs.load_or_create(),
            "catalog": self.catalog,
            "documents": [asdict(item) for item in self.active_documents()],
            "providers": providers,
        }

    def active_documents(self):
        archived = self.library.store.archived_document_ids()
        return [item for item in self.specs.list_documents() if item.document_id not in archived]

    def specification(self, document_id: str, run_id: str | None = None) -> dict:
        stored = self.current_document(document_id)
        run = (
            self.specs.load_extraction(document_id, run_id)
            if run_id
            else self.specs.load_latest_extraction(document_id)
        )
        run, review, revision = (
            self.effective_source(document_id, run) if run else (None, None, None)
        )
        assessments = (
            assess_requirements(
                run, stored.document, review, pdf_bytes=self.specs.read_pdf(document_id)
            )
            if run
            else {}
        )
        failure = self.specs.load_failure(document_id)
        if failure:
            failure = failure.model_copy(
                update={"message": public_analysis_error(failure.message, failure.error_code)}
            )
        views, unit_proofs = (
            physical_parameter_views(run.extraction.requirements, stored.document)
            if run
            else ({}, {})
        )
        return {
            "document_id": document_id,
            "original_name": stored.original_name,
            "document": stored.document,
            "run": run,
            "review": review,
            "facts_revision": revision,
            "facts_version": revision.version if revision else 0,
            "assessments": {key: asdict(value) for key, value in assessments.items()},
            "normalized_requirements": {
                item.id: parameter_review_view(
                    views[item.id],
                    self.library.present_claim,
                    self.catalog,
                    source_unit_proofs=unit_proofs[item.id],
                )
                for item in run.extraction.requirements
            }
            if run
            else {},
            "coverage_issues": coverage_issues(run.extraction, stored.document) if run else [],
            "document_review_issues": list(document_review_issues(run, stored.document))
            if run
            else [],
            "failure": failure,
        }

    def latest(self, document_id: str, run_id: str):
        run = self.specs.load_latest_extraction(document_id)
        if run is None or run.run_id != run_id:
            raise HTTPException(409, "Extraction changed. Reload before saving your decision.")
        return run

    def capture_draft_inputs(self, document_id: str) -> dict:
        """Read every mutable report input without deriving or matching it.

        Each call reads the current file/SQL revisions. Consumers can reuse a
        pure derivation only when the complete captured fingerprint agrees.
        """
        stored = self.current_document(document_id)
        run = self.specs.load_latest_extraction(document_id)
        if run is None:
            raise HTTPException(400, "Analyze this PDF in Specifications before creating a report.")
        run, review, revision = self.effective_source(document_id, run)
        if not run.extraction.requirements:
            raise HTTPException(
                400, "Add a source test in Specifications before creating a report."
            )
        report_workspace = self.reports.load_workspace(document_id, run.run_id)
        if revision:
            # Earlier manual support/quantity decisions cannot approve changed
            # facts. Their files remain intact; the effective view ignores them.
            by_id = {item.id: item for item in run.extraction.requirements}
            for field in ("mappings", "quantities"):
                choices = getattr(report_workspace, field)
                setattr(
                    report_workspace,
                    field,
                    {
                        key: choice
                        for key, choice in choices.items()
                        if key not in revision.item_updated_at
                        or (
                            key in by_id
                            and choice.source_requirement_fingerprint
                            == digest(by_id[key].model_dump(mode="json"))
                        )
                    },
                )
        return {
            "stored": stored,
            "run": run,
            "review": review,
            "revision": revision,
            "lab": self.labs.load_or_create(),
            "workspace": report_workspace,
        }

    def captured_draft_fingerprint(self, inputs: dict) -> str:
        fingerprint = report_input_fingerprint(
            inputs["stored"], inputs["run"], inputs["review"], inputs["lab"],
            self.catalog, inputs["workspace"],
        )
        revision = inputs["revision"]
        return digest([fingerprint, revision.revision_id]) if revision else fingerprint

    def draft_from_inputs(self, inputs: dict) -> ReportSnapshot:
        report = build_report(
            inputs["stored"], inputs["run"], inputs["review"], inputs["lab"],
            self.catalog, inputs["workspace"],
            pdf_bytes=self.specs.read_pdf(inputs["stored"].document_id),
        )
        revision = inputs["revision"]
        report.source_facts_revision_id = revision.revision_id if revision else None
        report.input_fingerprint = self.captured_draft_fingerprint(inputs)
        return report

    def draft(self, document_id: str) -> ReportSnapshot:
        return self.draft_from_inputs(self.capture_draft_inputs(document_id))

    def report(self, document_id: str) -> dict:
        report = self.draft(document_id)
        current_lab = lab_fingerprint(report.lab)
        return {
            "report": report,
            "line_states": {
                line.requirement.id: {
                    "needs_review": needs_review(report, line),
                    "support_basis": support_basis(line, current_lab),
                    "scope_state": scope_review_state(report, line),
                    "quote_eligible": quote_eligible(report, line),
                    "batch_eligible": batch_eligible(line),
                }
                for line in report.lines
            },
            "saved_reports": [
                {
                    "report_id": item.report_id,
                    "created_at": item.created_at,
                    "current": item.input_fingerprint == report.input_fingerprint,
                }
                for item in self.reports.list_reports(document_id)
            ],
            "coverage_issues": coverage_issues(
                report.source_run.extraction, self.current_document(document_id).document
            ),
        }

    def checked_draft(self, document_id: str, body: ReportEdit) -> ReportSnapshot:
        self.latest(document_id, body.run_id)
        report = self.draft(document_id)
        if (
            report.input_fingerprint != body.input_fingerprint
            or report.workspace.version != body.version
        ):
            raise HTTPException(409, "Source, lab or review data changed. Reload before saving.")
        return report

    @staticmethod
    def line(report: ReportSnapshot, requirement_id: str) -> GapLine:
        line = next((row for row in report.lines if row.requirement.id == requirement_id), None)
        if line is None:
            raise HTTPException(400, "Select a requirement from the current extraction.")
        return line

    def save(self, report: ReportSnapshot, workspace) -> None:
        # Re-check source and lab inputs immediately before compare-and-swap on the
        # report store. Store locks/versioning still arbitrate competing reviewers.
        if self.draft(report.document_id).input_fingerprint != report.input_fingerprint:
            raise HTTPException(409, "Inputs changed while saving. Reload before trying again.")
        self.reports.save_workspace(workspace, expected_version=report.workspace.version)

    def scope(self, document_id: str, body: ScopeReview) -> dict:
        report = self.checked_draft(document_id, body)
        identifiers = body.requirement_ids
        if len(set(identifiers)) != len(identifiers):
            raise HTTPException(400, "Select each requirement only once.")
        if len(identifiers) > 1 and not body.acknowledged:
            raise HTTPException(400, "Acknowledge the explicitly selected batch before saving.")
        if not body.supported and not body.note:
            raise HTTPException(400, "Explain the limitation before recording a scope gap.")
        candidate = report.workspace.model_copy(deep=True)
        for identifier in identifiers:
            line = self.line(report, identifier)
            eligible = batch_eligible(line)
            if body.supported and line.operation_assessment == "gap":
                eligible = False
            if not eligible:
                raise HTTPException(
                    400,
                    "Resolve this test and method first. Recorded limit gaps "
                    "cannot be approved as lab support.",
                )
            candidate.mappings[identifier] = MappingChoice(
                canonical_test_id=line.canonical_test_id,
                selected_method_codes=list(line.considered_method_codes),
                capability_id=line.capability.id,
                capability_verdict="supported" if body.supported else "unsupported",
                lab_fingerprint=lab_fingerprint(report.lab),
                source_requirement_fingerprint=digest(line.requirement.model_dump(mode="json")),
                note=body.note or "Lab reviewer checked this request's conditions and scope.",
            )
        self.save(report, candidate)
        return self.report(document_id)

    def exclusions(self, document_id: str, body: ExclusionReview) -> dict:
        """Change this report's scope without editing source or machine outputs."""

        report = self.checked_draft(document_id, body)
        if len(body.requirement_ids) != len(set(body.requirement_ids)):
            raise HTTPException(400, "Select each test only once.")
        candidate = report.workspace.model_copy(deep=True)
        active = candidate.active_exclusions
        for identifier in body.requirement_ids:
            line = self.line(report, identifier)
            previous_excluded = identifier in active
            if previous_excluded == body.excluded:
                continue
            if body.excluded and line.extraction_review_state == "excluded":
                raise HTTPException(
                    400,
                    "This test was excluded during source review. "
                    "Restore it in specification review before changing its report scope.",
                )
            candidate.scope_actions.append(
                ReportScopeAction(
                    action_id=str(uuid4()),
                    requirement_id=identifier,
                    excluded=body.excluded,
                    actor=current_actor(),
                    occurred_at=utc_now(),
                    note=body.note
                    or (
                        "Removed from this report by the lab reviewer."
                        if body.excluded
                        else "Restored to this report by the lab reviewer."
                    ),
                    previous_status=line.status,
                    previous_excluded=previous_excluded,
                )
            )
        if len(candidate.scope_actions) != len(report.workspace.scope_actions):
            self.save(report, candidate)
        return self.library.comparison(document_id)

    def mapping(self, document_id: str, body: MappingReview) -> dict:
        report = self.checked_draft(document_id, body)
        line = self.line(report, body.requirement_id)
        if line.status == "excluded":
            raise HTTPException(400, "Restore the source requirement before changing its match.")
        choice = body.mapping
        if choice.canonical_test_id:
            self.catalog.test_by_id(choice.canonical_test_id)
        original = {method.code for method in line.requirement.requested_methods}
        selected = (
            set(choice.selected_method_codes)
            if choice.selected_method_codes is not None
            else original
        )
        if not selected <= original or (original and not selected):
            raise HTTPException(400, "Retain at least one method from the original request.")
        if selected != original and not choice.note:
            raise HTTPException(400, "Explain why the removed source method does not apply.")
        capability = next(
            (cap for cap in report.lab.capabilities if cap.id == choice.capability_id), None
        )
        if choice.capability_id and (
            capability is None or capability.canonical_test_id != choice.canonical_test_id
        ):
            raise HTTPException(400, "Select a lab capability for the chosen test.")
        if choice.capability_verdict != "unreviewed" and capability is None:
            raise HTTPException(400, "Choose a lab capability before assessing its scope.")
        if choice.capability_verdict == "unsupported" and not choice.note:
            raise HTTPException(400, "Explain the limitation before recording a scope gap.")
        if choice.capability_verdict == "supported" and (
            line.status not in {"in_house", "condition_gap"}
            or line.capability is None
            or line.operation_assessment == "gap"
            or choice.capability_id != line.capability.id
            or choice.canonical_test_id != line.canonical_test_id
            or selected != set(line.considered_method_codes)
        ):
            raise HTTPException(
                400,
                "Save the identity and method selection as unreviewed first, "
                "then confirm the displayed lab scope. Limit gaps stay gaps.",
            )
        candidate = report.workspace.model_copy(deep=True)
        candidate.mappings[body.requirement_id] = MappingChoice(
            **{
                **choice.model_dump(),
                "selected_method_codes": choice.selected_method_codes if original else None,
                "note": choice.note or "Saved test and method selection.",
                "lab_fingerprint": lab_fingerprint(report.lab),
                "source_requirement_fingerprint": digest(line.requirement.model_dump(mode="json")),
            },
        )
        self.save(report, candidate)
        return self.report(document_id)

    def quantity(self, document_id: str, body: QuantityReview) -> dict:
        report = self.checked_draft(document_id, body)
        line = self.line(report, body.requirement_id)
        candidate = report.workspace.model_copy(deep=True)
        if body.quantity is None:
            candidate.quantities.pop(body.requirement_id, None)
        else:
            if not quote_eligible(report, line):
                raise HTTPException(
                    400,
                    "Resolve source checks and lab support, and supply a "
                    "reference rate before entering a quantity.",
                )
            if not body.scope_and_basis_confirmed:
                raise HTTPException(400, "Confirm applicability, overlap and pricing basis first.")
            candidate.quantities[body.requirement_id] = QuoteChoice(
                quantity=Decimal(body.quantity),
                scope_and_basis_confirmed=True,
                lab_fingerprint=lab_fingerprint(report.lab),
                source_requirement_fingerprint=digest(line.requirement.model_dump(mode="json")),
                note=body.note,
            )
        self.save(report, candidate)
        return self.report(document_id)


def utc_now():
    return datetime.now(UTC)

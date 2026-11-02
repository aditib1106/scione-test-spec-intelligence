"""Translate local domain operations to HTTP without changing persisted contracts."""

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException

from scione.api.contracts import MappingReview, QuantityReview, ReportEdit, ScopeReview
from scione.catalogs import FileLabStore, load_reference_catalog
from scione.config import Settings
from scione.reports.matching import build_report, lab_fingerprint, support_basis
from scione.reports.models import GapLine, MappingChoice, QuoteChoice, ReportSnapshot
from scione.reports.review import batch_eligible, needs_review, quote_eligible, scope_review_state
from scione.reports.store import FileReportStore
from scione.specifications.extraction import coverage_issues
from scione.specifications.review import assess_requirements
from scione.specifications.store import FileSpecStore


class Workspace:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog = load_reference_catalog()
        self.labs = FileLabStore(settings.scione_workspace_dir, self.catalog, seed_demo=False)
        self.specs = FileSpecStore(settings.scione_workspace_dir)
        self.reports = FileReportStore(settings.scione_workspace_dir)

    def overview(self) -> dict:
        providers = []
        for provider, label in (("groq", "Groq"), ("gemini", "Gemini")):
            key = getattr(self.settings, f"{provider}_api_key")
            available = bool(key and key.get_secret_value().strip())
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
                    ),
                }
            )
        return {
            "lab": self.labs.load_or_create(),
            "catalog": self.catalog,
            "documents": [asdict(item) for item in self.specs.list_documents()],
            "providers": providers,
        }

    def specification(self, document_id: str, run_id: str | None = None) -> dict:
        stored = self.specs.load_document(document_id)
        run = (
            self.specs.load_extraction(document_id, run_id)
            if run_id
            else self.specs.load_latest_extraction(document_id)
        )
        review = self.specs.load_review(document_id, run.run_id) if run else None
        assessments = (
            assess_requirements(
                run, stored.document, review, pdf_bytes=self.specs.read_pdf(document_id)
            )
            if run
            else {}
        )
        return {
            "document_id": document_id,
            "original_name": stored.original_name,
            "document": stored.document,
            "run": run,
            "review": review,
            "assessments": {key: asdict(value) for key, value in assessments.items()},
            "coverage_issues": coverage_issues(run.extraction, stored.document) if run else [],
            "failure": self.specs.load_failure(document_id),
        }

    def latest(self, document_id: str, run_id: str):
        run = self.specs.load_latest_extraction(document_id)
        if run is None or run.run_id != run_id:
            raise HTTPException(409, "Extraction changed. Reload before saving your decision.")
        return run

    def draft(self, document_id: str) -> ReportSnapshot:
        stored = self.specs.load_document(document_id)
        run = self.specs.load_latest_extraction(document_id)
        if run is None or not run.extraction.requirements:
            raise HTTPException(400, "Analyze this PDF in Specifications before creating a report.")
        return build_report(
            stored,
            run,
            self.specs.load_review(document_id, run.run_id),
            self.labs.load_or_create(),
            self.catalog,
            self.reports.load_workspace(document_id, run.run_id),
            pdf_bytes=self.specs.read_pdf(document_id),
        )

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
                report.source_run.extraction, self.specs.load_document(document_id).document
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
                note=body.note or "Lab reviewer checked this request's conditions and scope.",
            )
        self.save(report, candidate)
        return self.report(document_id)

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
                note=body.note,
            )
        self.save(report, candidate)
        return self.report(document_id)


def utc_now():
    return datetime.now(UTC)

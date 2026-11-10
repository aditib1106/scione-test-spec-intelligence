"""Local HTTP and static frontend entry point for the MUI lab workspace."""

import logging
from dataclasses import asdict
from decimal import InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pymupdf
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from scione.api.contracts import (
    AnalysisRequest,
    ExclusionReview,
    LabExample,
    LibraryMetadata,
    MappingReset,
    MappingReview,
    MatchingRequest,
    QuantityReview,
    ReportInput,
    ResetRequest,
    ScopeReview,
    SourceFactsEdit,
    SourceRequirementAdd,
    SourceReview,
)
from scione.api.inference_gate import InferenceBusyError, InferenceGate
from scione.api.method_bank import router as method_bank_router
from scione.api.workspace import Workspace, utc_now
from scione.catalogs import CatalogError, LabProfile, LabStoreError, default_demo_lab
from scione.catalogs.examples import (
    demonstration_csp301_lab,
    detailed_csp301_lab,
    typed_csp301_lab,
)
from scione.catalogs.lab_store import LabVersionConflict
from scione.catalogs.seed import default_csp301_lab
from scione.config import ConfigurationError, Settings
from scione.library.reset import reset_library_workspace
from scione.library.store import LibraryConflict, LibraryStoreError
from scione.providers.factory import create_configured_provider
from scione.reports.pdf import ReportPDFError, render_report_pdf
from scione.reports.store import ReportStoreError, ReportVersionConflict
from scione.specifications.extraction import (
    CustomerExtractionError,
    CustomerRequirementRunner,
    build_plan,
)
from scione.specifications.parameter_views import physical_parameter_views
from scione.specifications.pdf_preview import (
    PDFPreviewError,
    inspect_pdf_highlights,
    render_pdf_page,
)
from scione.specifications.preview_quotes import (
    PreviewField,
    field_preview_quotes,
    parameter_preview_quotes,
)
from scione.specifications.public_errors import public_analysis_error
from scione.specifications.store import (
    MAX_PDF_BYTES,
    ExtractionFailure,
    SpecStoreError,
    SpecVersionConflict,
)
from scione.structured import normalize_requirement
from scione.structured.pdf import ComparisonPDFError, render_comparison_pdf
from scione.web.diagnostics_data import (
    comparison_rows,
    evidence_rows,
    requirement_rows,
    run_summary,
)
from scione.workspace_reset import DemoResetError

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_SPEC = REPOSITORY_ROOT / "docs" / "samples" / "MS-PNT-410_demo_spec.pdf"
CSP301_SPEC = REPOSITORY_ROOT / "docs" / "samples" / "CSP-EN-301_demo_spec.pdf"


def create_app(settings: Settings | None = None, frontend_dir: Path | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    workspace = Workspace(config)
    app = FastAPI(title="SciOne local workspace", version="0.1.0")
    app.include_router(method_bank_router)
    inference_gate = InferenceGate()
    app.state.inference_gate = inference_gate
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        # The prototype has no accounts: keep writes on the local application
        # origin, including Vite's same-origin development proxy.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlsplit(origin)
            same_origin = (
                parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
            )
            if not same_origin and origin not in {
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            }:
                return JSONResponse({"detail": "Open the local workspace to make changes."}, 403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def domain_error(_request: Request, exc: Exception):
        conflict = isinstance(
            exc, (LabVersionConflict, SpecVersionConflict, ReportVersionConflict, LibraryConflict)
        )
        return JSONResponse({"detail": str(exc)}, 409 if conflict else 400)

    for error_type in (
        LabStoreError,
        SpecStoreError,
        ReportStoreError,
        CatalogError,
        PDFPreviewError,
        ReportPDFError,
        ComparisonPDFError,
        DemoResetError,
        LibraryStoreError,
    ):
        app.add_exception_handler(error_type, domain_error)

    @app.exception_handler(InferenceBusyError)
    async def inference_busy(_request: Request, exc: InferenceBusyError):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        # Log a useful correlation/code without SQL, bound customer data or
        # connection credentials. A query resource limit is not a DB outage.
        reference = uuid4().hex[:12]
        args = getattr(getattr(exc, "orig", None), "args", ())
        code = args[0] if args and isinstance(args[0], int) else None
        logging.getLogger(__name__).error(
            "Workspace database error ref=%s path=%s type=%s driver_code=%s",
            reference,
            request.url.path,
            type(exc).__name__,
            code,
        )
        message = (
            "Saved comparison history could not be loaded. Your source files and "
            "earlier reports are preserved. Try again or contact the workspace administrator."
            if code == 1038
            else "The specification database could not complete this action. "
            "Check the database service and retry. Existing source files have been preserved."
        )
        return JSONResponse(
            {
                "detail": message,
                "error_reference": reference,
            },
            503,
        )

    @app.exception_handler(ValidationError)
    async def validation_error(_request: Request, exc: ValidationError):
        return JSONResponse(
            {"detail": exc.errors(include_input=False, include_context=False, include_url=False)},
            422,
        )

    @app.exception_handler(InvalidOperation)
    async def invalid_decimal(_request: Request, _exc: InvalidOperation):
        return JSONResponse(
            {"detail": "Enter a finite numeric quantity, for example 12 or 2.5."}, 422
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/workspace")
    def overview():
        return jsonable_encoder(workspace.overview())

    @app.get("/api/lab")
    def lab():
        return workspace.labs.load_or_create()

    @app.get("/api/lab/records")
    def lab_records():
        return workspace.labs.records()

    @app.get("/api/parameters")
    def parameter_definitions():
        from scione.parameters.registry import definitions

        return {"parameters": [item.model_dump(mode="json") for item in definitions()]}

    @app.put("/api/lab")
    def save_lab(body: LabProfile):
        workspace.labs.save(body)
        return workspace.labs.load_or_create()

    @app.post("/api/lab/example")
    def example_lab(body: LabExample):
        lab = workspace.labs.load_or_create()
        if lab.version != body.version:
            raise HTTPException(409, "Lab changed. Reload before loading the example.")
        if body.replace_existing and not body.acknowledged:
            raise HTTPException(
                400, "Confirm replacing lab capabilities with synthetic example data."
            )
        if lab.capabilities and not (body.replace_existing and body.acknowledged):
            raise HTTPException(409, "Examples can only be loaded into an empty lab.")
        examples = {
            "csp301": default_csp301_lab,
            "csp301_typed": typed_csp301_lab,
            "csp301_detailed": detailed_csp301_lab,
            "csp301_demo": demonstration_csp301_lab,
            "legacy": default_demo_lab,
        }
        workspace.labs.save(
            lab.model_copy(update={"capabilities": examples[body.example]().capabilities})
        )
        return workspace.labs.load_or_create()

    @app.get("/api/catalog")
    def catalog():
        return workspace.catalog

    @app.get("/api/library")
    def library():
        return workspace.library.listing()

    @app.get("/api/library/{document_id}")
    def library_document(document_id: str):
        return workspace.library.detail(document_id)

    @app.put("/api/library/{document_id}/metadata")
    def library_metadata(document_id: str, body: LibraryMetadata):
        return workspace.library.metadata(document_id, body)

    @app.get("/api/comparisons/{document_id}")
    def comparison(document_id: str):
        return workspace.library.comparison(document_id)

    @app.get("/api/specifications/{document_id}/matching")
    def matching_status(document_id: str):
        return workspace.matching.overview(document_id)

    @app.post("/api/specifications/{document_id}/matching")
    def matching_run(document_id: str, body: MatchingRequest):
        with inference_gate.claim(document_id, "matching"):
            return matching_unlocked(document_id, body)

    def matching_unlocked(document_id: str, body: MatchingRequest):
        if not body.permission:
            raise HTTPException(
                400,
                "Allow sending these requirements and lab declarations "
                "to the selected model API before running matching.",
            )
        selected = config.model_copy(
            update={
                "groq_max_completion_tokens": body.output_limit,
                "gemini_max_output_tokens": body.output_limit,
                "fireworks_max_output_tokens": body.output_limit,
                "sail_max_output_tokens": body.output_limit,
                "groq_temperature": 0.0,
                "gemini_temperature": 0.0,
                "fireworks_temperature": 0.0,
                "sail_temperature": 0.0,
                "scione_single_attempt_mode": True,
            }
        )
        try:
            provider = create_configured_provider(
                selected, provider_name=body.provider, model_name=body.model
            )
        except ConfigurationError as exc:
            raise HTTPException(400, str(exc)) from exc
        return workspace.matching.run(document_id, body, provider)

    @app.get("/api/specifications/{document_id}/matching/{run_id}/artifacts")
    def matching_artifacts(document_id: str, run_id: str):
        import json

        return Response(
            json.dumps(workspace.matching.artifacts(document_id, run_id), ensure_ascii=False),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="scione-matching-artifacts.json"'
            },
        )

    @app.get("/api/comparison-snapshots/{comparison_id}")
    def comparison_snapshot(comparison_id: str):
        payload = workspace.library.store.comparison(comparison_id)
        pdf = workspace.library.store.comparison_pdf(comparison_id, render_comparison_pdf)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            return {"comparison": payload, "page_count": len(document)}

    @app.get("/api/comparison-snapshots/{comparison_id}/pdf")
    def comparison_pdf(comparison_id: str, download: bool = False):
        pdf = workspace.library.store.comparison_pdf(comparison_id, render_comparison_pdf)
        return Response(
            pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"{'attachment' if download else 'inline'}; "
                f'filename="scione-gap-{comparison_id[:8]}.pdf"'
            },
        )

    @app.get("/api/comparison-snapshots/{comparison_id}/pages/{page}")
    def comparison_page(comparison_id: str, page: int):
        pdf = workspace.library.store.comparison_pdf(comparison_id, render_comparison_pdf)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            count = len(document)
        return Response(
            render_pdf_page(pdf, page_number=page, scale=2.4, max_dimension=2000),
            media_type="image/png",
            headers={"X-Page-Count": str(count)},
        )

    @app.get("/api/comparison-snapshots/{comparison_id}/json")
    def comparison_json(comparison_id: str):
        import json

        payload = workspace.library.store.comparison(comparison_id)
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="scione-gap-{comparison_id[:8]}.json"'
            },
        )

    @app.get("/api/specifications")
    def documents():
        return [asdict(item) for item in workspace.specs.list_documents()]

    @app.post("/api/specifications")
    async def upload_specification(file: UploadFile):
        try:
            data = await file.read(MAX_PDF_BYTES + 1)
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(413, "Choose a PDF smaller than 25 MB.")
            saved = workspace.specs.import_pdf(file.filename or "specification.pdf", data)
            return {"document_id": saved.document_id}
        finally:
            await file.close()

    @app.post("/api/specifications/demo")
    def import_demo(example: str = "legacy"):
        sample = CSP301_SPEC if example == "csp301" else SAMPLE_SPEC
        saved = workspace.specs.import_pdf(sample.name, sample.read_bytes())
        return {"document_id": saved.document_id}

    @app.post("/api/specifications/worked-example")
    def import_worked_example():
        from scione.structured.examples import csp301_reference_run

        saved = workspace.specs.import_pdf(CSP301_SPEC.name, CSP301_SPEC.read_bytes())
        # An explicit reference example is never allowed to replace a live model run.
        if workspace.specs.load_latest_extraction(saved.document_id) is None:
            workspace.specs.save_extraction(saved.document_id, csp301_reference_run(saved))
        document = workspace.library.document(saved.document_id)
        if document["version"] == 0 and not document["company_name"]:
            workspace.library.store.edit_metadata(
                saved.document_id,
                {
                    "company_name": "Synthetic example customer",
                    "specification_id": "CSP-EN-301",
                    "title": document["title"] or "Exterior Fascia Finish - Change Approval Rider",
                    "revision": document["revision"] or "Issue A",
                },
                expected_version=0,
            )
        return {"document_id": saved.document_id}

    @app.get("/api/specifications/{document_id}")
    def specification(document_id: str, run_id: str | None = None):
        return jsonable_encoder(workspace.specification(document_id, run_id))

    @app.get("/api/specifications/{document_id}/pdf")
    def source_pdf(document_id: str):
        return Response(workspace.specs.read_pdf(document_id), media_type="application/pdf")

    @app.get("/api/specifications/{document_id}/pages/{page}")
    def source_preview(
        document_id: str,
        page: int,
        requirement_id: str | None = None,
        run_id: str | None = None,
        field: PreviewField | None = None,
        claim_id: str | None = None,
    ):
        quotes = []
        highlight_scope = "none"
        located = None
        pdf = workspace.specs.read_pdf(document_id)
        if requirement_id:
            run = (
                workspace.specs.load_extraction(document_id, run_id)
                if run_id
                else workspace.specs.load_latest_extraction(document_id)
            )
            if run:
                run, _, _ = workspace.effective_source(document_id, run)
            requirement = (
                next((row for row in run.extraction.requirements if row.id == requirement_id), None)
                if run
                else None
            )
            if requirement is None:
                raise HTTPException(404, "This requirement is not in the selected extraction.")
            if claim_id:
                views, unit_proofs = physical_parameter_views(
                    run.extraction.requirements, workspace.current_document(document_id).document
                )
                requirement = views[requirement_id]
                claim = next(
                    (
                        item
                        for item in normalize_requirement(
                            requirement, source_unit_proofs=unit_proofs[requirement_id]
                        ).claims
                        if item.id == claim_id
                    ),
                    None,
                )
                if claim is None:
                    raise HTTPException(404, "This parameter is not in the selected test.")
                quotes, highlight_scope = parameter_preview_quotes(requirement, page, claim)
            else:
                quotes, highlight_scope = field_preview_quotes(requirement, page, field)
            if highlight_scope in {"field", "parameter"}:
                located = inspect_pdf_highlights(pdf, page_number=page, quotes=quotes)
                if not located.matches or any(
                    match.status != "matched" for match in located.matches
                ):
                    quotes, highlight_scope = field_preview_quotes(requirement, page, None)
                    located = None
            if located is None and quotes:
                located = inspect_pdf_highlights(pdf, page_number=page, quotes=quotes)
        elif claim_id:
            raise HTTPException(422, "Select a test before locating its parameter.")
        if located is not None and not located.rectangles:
            highlight_scope = "none"
        headers = {"X-Highlight-Scope": highlight_scope}
        if located is not None and located.focus_y is not None:
            headers["X-Highlight-Focus"] = str(round(located.focus_y, 6))
        return Response(
            render_pdf_page(
                pdf,
                page_number=page,
                scale=2.4,
                max_dimension=2000,
                highlight_quotes=quotes,
            ),
            media_type="image/png",
            headers=headers,
        )

    @app.post("/api/specifications/{document_id}/review")
    def source_review(document_id: str, body: SourceReview):
        workspace.latest(document_id, body.run_id)
        if len(body.requirement_ids) > 1 and not body.acknowledged:
            raise HTTPException(400, "Acknowledge the explicitly selected batch before saving.")
        if body.decision == "excluded" and not body.note:
            raise HTTPException(400, "Explain why these requirements should be excluded.")

        def validate_facts():
            revision = workspace.facts.latest(document_id, body.run_id)
            if (revision.version if revision else 0) != body.facts_version:
                raise LibraryConflict("Extracted content changed. Reload before confirming it.")

        workspace.specs.save_reviews(
            document_id,
            body.run_id,
            body.requirement_ids,
            body.decision,
            body.note,
            expected_version=body.version,
            require_latest=True,
            validate_current=validate_facts,
        )
        return jsonable_encoder(workspace.specification(document_id))

    @app.post("/api/specifications/{document_id}/facts")
    def source_facts(document_id: str, body: SourceFactsEdit):
        return jsonable_encoder(workspace.correct_facts(document_id, body))

    @app.post("/api/specifications/{document_id}/requirements")
    def source_requirement_add(document_id: str, body: SourceRequirementAdd):
        return jsonable_encoder(workspace.add_source_requirement(document_id, body))

    @app.get("/api/specifications/{document_id}/analysis-failures/{attempt_id}/artifacts")
    def analysis_failure_artifacts(document_id: str, attempt_id: str):
        artifact = workspace.specs.load_failure_artifact(document_id, attempt_id)
        return Response(
            artifact.model_dump_json(indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="scione-failed-analysis.json"'},
        )

    @app.post("/api/specifications/{document_id}/analysis")
    def analyze(document_id: str, body: AnalysisRequest):
        with inference_gate.claim(document_id, "analysis"):
            return analyze_unlocked(document_id, body)

    def analyze_unlocked(document_id: str, body: AnalysisRequest):
        usage_acknowledged = body.billing_acknowledged or (
            body.provider == "gemini" and body.free_tier_confirmed
        )
        if not body.permission or (
            body.provider in {"gemini", "fireworks", "sail"} and not usage_acknowledged
        ):
            raise HTTPException(
                400, "Confirm sending permission and acknowledge the provider's API usage terms."
            )
        stored = workspace.current_document(document_id)
        previous = workspace.specs.load_latest_extraction(document_id)
        try:
            build_plan(stored.document, body.strategy)
            adapter = create_configured_provider(
                config.model_copy(
                    update={
                        "groq_max_completion_tokens": body.output_limit,
                        "groq_temperature": 0.0,
                        "gemini_max_output_tokens": body.output_limit,
                        "gemini_temperature": 0.0,
                        "fireworks_max_output_tokens": body.output_limit,
                        "sail_max_output_tokens": body.output_limit,
                        "fireworks_temperature": 0.0,
                        "sail_temperature": 0.0,
                        "scione_single_attempt_mode": True,
                    }
                ),
                provider_name=body.provider,
                model_name=body.model,
            )
            run = CustomerRequirementRunner(
                adapter,
                max_input_characters=config.scione_customer_input_character_limit,
            ).run(stored.document, strategy=body.strategy)
            workspace.specs.save_extraction(document_id, run)
        except (CustomerExtractionError, ConfigurationError, SpecStoreError) as exc:
            message = str(exc)
            try:
                workspace.specs.save_failure(
                    ExtractionFailure(
                        document_id=document_id,
                        occurred_at=utc_now(),
                        provider=body.provider,
                        model=body.model,
                        strategy=body.strategy,
                        output_limit=body.output_limit,
                        message=message[:4000],
                        error_code=getattr(exc, "error_code", None),
                        previous_run_id=previous.run_id if previous else None,
                    ),
                    artifact=exc.artifact if isinstance(exc, CustomerExtractionError) else None,
                )
            except SpecStoreError:
                message += " Attempt details could not be saved. Existing data was not reset."
            raise HTTPException(
                400,
                public_analysis_error(message, getattr(exc, "error_code", None))
                + " No automatic retry was made. Your previous saved result is unchanged. "
                "Review the failed attempt before starting another analysis.",
            ) from exc
        workspace.library.index(document_id)
        return jsonable_encoder(workspace.specification(document_id))

    @app.get("/api/reports/{document_id}")
    def report(document_id: str):
        return jsonable_encoder(workspace.report(document_id))

    @app.post("/api/reports/{document_id}/scope")
    def scope(document_id: str, body: ScopeReview):
        return jsonable_encoder(workspace.scope(document_id, body))

    @app.post("/api/reports/{document_id}/exclusions")
    def exclusions(document_id: str, body: ExclusionReview):
        return jsonable_encoder(workspace.exclusions(document_id, body))

    @app.post("/api/reports/{document_id}/mapping")
    def mapping(document_id: str, body: MappingReview):
        return jsonable_encoder(workspace.mapping(document_id, body))

    @app.post("/api/reports/{document_id}/mapping/reset")
    def reset_mapping(document_id: str, body: MappingReset):
        report = workspace.checked_draft(document_id, body)
        workspace.line(report, body.requirement_id)
        candidate = report.workspace.model_copy(deep=True)
        candidate.mappings.pop(body.requirement_id, None)
        workspace.save(report, candidate)
        return jsonable_encoder(workspace.report(document_id))

    @app.post("/api/reports/{document_id}/quantity")
    def quantity(document_id: str, body: QuantityReview):
        return jsonable_encoder(workspace.quantity(document_id, body))

    @app.post("/api/reports/{document_id}/snapshots")
    def generate_report(document_id: str, body: ReportInput):
        workspace.latest(document_id, body.run_id)
        report = workspace.draft(document_id)
        if report.input_fingerprint != body.input_fingerprint:
            raise HTTPException(409, "Inputs changed. Reload before generating a report.")
        snapshot = report.model_copy(update={"report_id": str(uuid4()), "created_at": utc_now()})
        workspace.reports.save_report(snapshot, render_report_pdf(snapshot))
        return {"report_id": snapshot.report_id}

    @app.get("/api/snapshots/{report_id}")
    def snapshot(report_id: str):
        report = workspace.reports.load_report(report_id)
        with pymupdf.open(stream=workspace.reports.read_pdf(report_id), filetype="pdf") as pdf:
            return {"report": report, "page_count": len(pdf)}

    @app.get("/api/snapshots/{report_id}/pdf")
    def report_pdf(report_id: str, download: bool = False):
        disposition = "attachment" if download else "inline"
        return Response(
            workspace.reports.read_pdf(report_id),
            media_type="application/pdf",
            headers={"Content-Disposition": f'{disposition}; filename="scione-{report_id}.pdf"'},
        )

    @app.get("/api/snapshots/{report_id}/pages/{page}")
    def report_preview(report_id: str, page: int):
        pdf = workspace.reports.read_pdf(report_id)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            count = len(document)
        return Response(
            render_pdf_page(pdf, page_number=page, scale=2.4, max_dimension=2000),
            media_type="image/png",
            headers={"X-Page-Count": str(count)},
        )

    @app.get("/api/snapshots/{report_id}/json")
    def report_json(report_id: str):
        saved = workspace.reports.load_report(report_id)
        return Response(
            saved.model_dump_json(indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.json"'},
        )

    @app.get("/api/diagnostics/{document_id}")
    def diagnostics(document_id: str, run_id: str | None = None, baseline_id: str | None = None):
        runs = workspace.specs.list_extractions(document_id)
        selected = workspace.specification(document_id, run_id)
        run = selected["run"]
        if run is None:
            return {
                "runs": [],
                "selected": selected,
                "summary": None,
                "requirements": [],
                "evidence": [],
                "comparison": [],
            }
        from scione.specifications.review import assess_requirements

        assessments = assess_requirements(
            run,
            selected["document"],
            selected["review"],
            pdf_bytes=workspace.specs.read_pdf(document_id),
        )
        baseline = (
            workspace.specs.load_extraction(document_id, baseline_id) if baseline_id else None
        )
        return jsonable_encoder(
            {
                "runs": [run_summary(item) for item in runs],
                "selected": selected,
                "summary": run_summary(run),
                "requirements": requirement_rows(run, assessments, selected["review"]),
                "evidence": evidence_rows(
                    run, selected["document"], pdf_bytes=workspace.specs.read_pdf(document_id)
                ),
                "comparison": comparison_rows(baseline, run) if baseline else [],
                "source_review_audits": workspace.source_review_audits(document_id, run),
            }
        )

    @app.get("/api/diagnostics/{document_id}/raw")
    def raw_response(document_id: str, run_id: str):
        run = workspace.specs.load_extraction(document_id, run_id)
        return Response(
            run.raw_response.encode("utf-8"),
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="provider-response.txt"'},
        )

    @app.post("/api/workspace/reset")
    def reset(body: ResetRequest):
        result = reset_library_workspace(config, workspace.library.store)
        return {"backup_path": str(result.backup_path) if result.backup_path else None}

    static = frontend_dir if frontend_dir is not None else REPOSITORY_ROOT / "frontend" / "dist"
    if (static / "index.html").is_file():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        def frontend():
            return FileResponse(static / "index.html")

        for route in (
            "/",
            "/my-lab",
            "/catalog",
            "/specifications",
            "/specifications/library",
            "/reports",
            "/reports/advanced",
            "/developer",
        ):
            app.add_api_route(route, frontend, methods=["GET"], include_in_schema=False)

    return app


app = create_app()

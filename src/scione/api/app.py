"""Local HTTP and static frontend entry point for the MUI lab workspace."""

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
from starlette.middleware.trustedhost import TrustedHostMiddleware

from scione.api.contracts import (
    AnalysisRequest,
    LabExample,
    MappingReset,
    MappingReview,
    QuantityReview,
    ReportInput,
    ResetRequest,
    ScopeReview,
    SourceReview,
)
from scione.api.workspace import Workspace, utc_now
from scione.catalogs import CatalogError, LabProfile, LabStoreError, default_demo_lab
from scione.catalogs.lab_store import LabVersionConflict
from scione.config import ConfigurationError, Settings
from scione.providers.factory import create_configured_provider
from scione.reports.pdf import ReportPDFError, render_report_pdf
from scione.reports.store import ReportStoreError, ReportVersionConflict
from scione.specifications.extraction import (
    CustomerExtractionError,
    CustomerRequirementRunner,
    build_plan,
)
from scione.specifications.pdf_preview import PDFPreviewError, render_pdf_page
from scione.specifications.store import (
    MAX_PDF_BYTES,
    ExtractionFailure,
    SpecStoreError,
    SpecVersionConflict,
)
from scione.web.diagnostics_data import (
    comparison_rows,
    evidence_rows,
    requirement_rows,
    run_summary,
)
from scione.workspace_reset import DemoResetError, reset_demo_workspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_SPEC = REPOSITORY_ROOT / "docs" / "samples" / "MS-PNT-410_demo_spec.pdf"


def create_app(settings: Settings | None = None, frontend_dir: Path | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    workspace = Workspace(config)
    app = FastAPI(title="SciOne local workspace", version="0.1.0")
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
        conflict = isinstance(exc, (LabVersionConflict, SpecVersionConflict, ReportVersionConflict))
        return JSONResponse({"detail": str(exc)}, 409 if conflict else 400)

    for error_type in (
        LabStoreError,
        SpecStoreError,
        ReportStoreError,
        CatalogError,
        PDFPreviewError,
        ReportPDFError,
        DemoResetError,
    ):
        app.add_exception_handler(error_type, domain_error)

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

    @app.put("/api/lab")
    def save_lab(body: LabProfile):
        workspace.labs.save(body)
        return workspace.labs.load_or_create()

    @app.post("/api/lab/example")
    def example_lab(body: LabExample):
        lab = workspace.labs.load_or_create()
        if lab.version != body.version:
            raise HTTPException(409, "Lab changed. Reload before loading the example.")
        if lab.capabilities:
            raise HTTPException(409, "Examples can only be loaded into an empty lab.")
        workspace.labs.save(
            lab.model_copy(update={"capabilities": default_demo_lab().capabilities})
        )
        return workspace.labs.load_or_create()

    @app.get("/api/catalog")
    def catalog():
        return workspace.catalog

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
    def import_demo():
        saved = workspace.specs.import_pdf(SAMPLE_SPEC.name, SAMPLE_SPEC.read_bytes())
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
    ):
        quotes = []
        if requirement_id:
            run = (
                workspace.specs.load_extraction(document_id, run_id)
                if run_id
                else workspace.specs.load_latest_extraction(document_id)
            )
            requirement = (
                next((row for row in run.extraction.requirements if row.id == requirement_id), None)
                if run
                else None
            )
            if requirement is None:
                raise HTTPException(404, "This requirement is not in the selected extraction.")
            quotes = [evidence.quote for evidence in requirement.evidence if evidence.page == page]
        return Response(
            render_pdf_page(
                workspace.specs.read_pdf(document_id),
                page_number=page,
                highlight_quotes=quotes,
            ),
            media_type="image/png",
        )

    @app.post("/api/specifications/{document_id}/review")
    def source_review(document_id: str, body: SourceReview):
        workspace.latest(document_id, body.run_id)
        if len(body.requirement_ids) > 1 and not body.acknowledged:
            raise HTTPException(400, "Acknowledge the explicitly selected batch before saving.")
        if body.decision == "excluded" and not body.note:
            raise HTTPException(400, "Explain why these requirements should be excluded.")
        workspace.specs.save_reviews(
            document_id,
            body.run_id,
            body.requirement_ids,
            body.decision,
            body.note,
            expected_version=body.version,
            require_latest=True,
        )
        return jsonable_encoder(workspace.specification(document_id))

    @app.post("/api/specifications/{document_id}/analysis")
    def analyze(document_id: str, body: AnalysisRequest):
        if not body.permission or (body.provider == "gemini" and not body.free_tier_confirmed):
            raise HTTPException(
                400, "Confirm sending permission and the provider's tier terms first."
            )
        stored = workspace.specs.load_document(document_id)
        previous = workspace.specs.load_latest_extraction(document_id)
        try:
            build_plan(stored.document, body.strategy)
            adapter = create_configured_provider(
                config.model_copy(
                    update={
                        "groq_max_completion_tokens": body.output_limit,
                        "groq_temperature": 0.0,
                        "gemini_max_output_tokens": body.output_limit,
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
                        previous_run_id=previous.run_id if previous else None,
                    )
                )
            except SpecStoreError:
                message += " Attempt details could not be saved. Existing data was not reset."
            raise HTTPException(400, message) from exc
        return jsonable_encoder(workspace.specification(document_id))

    @app.get("/api/reports/{document_id}")
    def report(document_id: str):
        return jsonable_encoder(workspace.report(document_id))

    @app.post("/api/reports/{document_id}/scope")
    def scope(document_id: str, body: ScopeReview):
        return jsonable_encoder(workspace.scope(document_id, body))

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
            render_pdf_page(pdf, page_number=page),
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
        result = reset_demo_workspace(config.scione_workspace_dir)
        return {"backup_path": str(result.backup_path) if result.backup_path else None}

    static = frontend_dir if frontend_dir is not None else REPOSITORY_ROOT / "frontend" / "dist"
    if (static / "index.html").is_file():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        def frontend():
            return FileResponse(static / "index.html")

        for route in ("/", "/my-lab", "/catalog", "/specifications", "/reports", "/developer"):
            app.add_api_route(route, frontend, methods=["GET"], include_in_schema=False)

    return app


app = create_app()

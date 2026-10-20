"""Read-only customer-spec workbench, separate from the lab-facing review UI."""

from __future__ import annotations

from collections import Counter
from datetime import UTC

import streamlit as st

from scione.config import Settings
from scione.schemas import IngestedDocument
from scione.specifications.extraction import coverage_issues
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.specifications.pdf_preview import PDFPreviewError, render_pdf_page
from scione.specifications.review import assess_requirements
from scione.specifications.store import (
    ExtractionFailure,
    FileSpecStore,
    RequirementReviewSet,
    SpecStoreError,
    StoredSpecification,
)
from scione.web.diagnostics_data import (
    comparison_rows,
    evidence_rows,
    requirement_rows,
    run_summary,
)


@st.cache_data(show_spinner=False, max_entries=24)
def _checks(run_json: str, document_json: str, review_json: str, pdf: bytes) -> tuple:
    run = CustomerExtractionRun.model_validate_json(run_json)
    document = IngestedDocument.model_validate_json(document_json)
    review = RequirementReviewSet.model_validate_json(review_json)
    return (
        assess_requirements(run, document, review, pdf_bytes=pdf),
        evidence_rows(run, document, pdf),
    )


@st.cache_data(show_spinner=False, max_entries=24)
def _page_image(pdf: bytes, page: int, quotes: tuple[str, ...]) -> bytes:
    return render_pdf_page(pdf, page_number=page, highlight_quotes=quotes)


def _display_number(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _run_label(run: CustomerExtractionRun) -> str:
    timestamp = (
        run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
    )
    return (
        f"{timestamp.astimezone(UTC):%b %d %H:%M UTC} · {run.provider}/{run.model} "
        f"· {run.run_id[:8]}"
    )


def _locate_requirement(run: CustomerExtractionRun, page_count: int) -> None:
    selected = st.session_state.get("spec_debug_requirement")
    requirement = next((row for row in run.extraction.requirements if row.id == selected), None)
    if requirement:
        page = next((item.page for item in requirement.evidence if 1 <= item.page <= page_count), 1)
        st.session_state["spec_debug_text_page"] = page


def _prepare_run(stored: StoredSpecification, run: CustomerExtractionRun) -> None:
    context = (stored.document_id, run.run_id)
    if st.session_state.get("_spec_debug_snapshot") != context:
        st.session_state["_spec_debug_snapshot"] = context
        st.session_state["spec_debug_requirement"] = (
            run.extraction.requirements[0].id if run.extraction.requirements else None
        )
        st.session_state["spec_debug_text_page"] = 1
        _locate_requirement(run, stored.document.page_count)
    page = st.session_state.get("spec_debug_text_page", 1)
    if not isinstance(page, int) or not 1 <= page <= stored.document.page_count:
        st.session_state["spec_debug_text_page"] = 1


def _overview(
    stored: StoredSpecification,
    run: CustomerExtractionRun,
    assessments: dict,
    source_rows: list[dict],
    failure: ExtractionFailure | None,
) -> None:
    # Actual saved metadata, never the provider selected for a future request.
    st.markdown(f"**Saved model:** {run.provider} / {run.model}")
    st.caption(f"{run.prompt_version} · {run.strategy} · run {run.run_id}")
    counts = Counter(item.state for item in assessments.values())
    columns = st.columns(4)
    columns[0].metric("Records", len(run.extraction.requirements))
    columns[1].metric("Needs review", counts["needs_attention"])
    columns[2].metric("Latency", f"{run.latency_ms / 1000:.2f}s")
    columns[3].metric("Total tokens", _display_number(run.usage.total_tokens))
    call_count = run_summary(run)["Calls"]
    st.caption(
        f"{stored.document.page_count} PDF pages · {_display_number(call_count)} model requests · "
        f"input {_display_number(run.usage.input_tokens)} / "
        f"output {_display_number(run.usage.output_tokens)} tokens"
    )
    left, right = st.columns(2)
    with left:
        st.markdown("**Review states**")
        st.dataframe(
            [
                {"State": label, "Records": counts[state]}
                for state, label in [
                    ("ready", "No row flags"),
                    ("needs_attention", "Needs attention"),
                    ("confirmed", "Human-confirmed"),
                    ("excluded", "Human-excluded"),
                ]
            ],
            hide_index=True,
            width="stretch",
        )
    with right:
        st.markdown("**Source location**")
        locations = Counter(str(row["Highlight"]) for row in source_rows)
        st.dataframe(
            [{"Check": key, "Quotations": value} for key, value in locations.items()],
            hide_index=True,
            width="stretch",
        )
    st.caption(
        "These are record counts and local checks, not accuracy or unique canonical tests. "
        "No row flags does not mean human confirmation or verified lab support."
    )
    coverage = coverage_issues(run.extraction, stored.document)
    if coverage:
        st.warning("Coverage hints remain: some source method references are not represented.")
        with st.expander("Inspect coverage hints"):
            for issue in coverage:
                st.text(issue)
    if failure:
        with st.expander("Last failed attempt — separate from this saved run"):
            st.caption("Document-level attempt history; it does not change the viewed run's model.")
            st.json(failure.model_dump(mode="json"))


def _requirements(
    run: CustomerExtractionRun, assessments: dict, review: RequirementReviewSet
) -> None:
    st.markdown("**Extracted requirements**")
    search = st.text_input("Filter test name or method", key="spec_debug_search")
    rows = requirement_rows(run, assessments, review)
    if search.strip():
        query = search.strip().casefold()
        rows = [row for row in rows if query in f"{row['Test']} {row['Methods']}".casefold()]
    st.caption(f"Showing {len(rows)} / {len(run.extraction.requirements)} records")
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        height=420,
        column_order=[
            "ID",
            "Test",
            "Methods",
            "Conditions",
            "Criteria",
            "Scope",
            "Instructions",
            "Source pages",
            "Review state",
            "Review flags",
        ],
        column_config={
            "Test": st.column_config.TextColumn(width="medium"),
            "Conditions": st.column_config.TextColumn(width="large"),
            "Criteria": st.column_config.TextColumn(width="large"),
        },
    )
    st.caption("Raw source values are retained. Use Evidence & checks to inspect one record.")


def _source_preview(
    stored: StoredSpecification,
    pdf: bytes,
    requirement: CustomerRequirement | None = None,
) -> None:
    page = st.number_input(
        "Text page",
        min_value=1,
        max_value=stored.document.page_count,
        key="spec_debug_text_page",
        help="Physical PDF page, not a section or printed page label.",
    )
    quotes = (
        tuple(item.quote for item in requirement.evidence if item.page == page)
        if requirement
        else ()
    )
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("**PDF · located quotation fragments**")
        try:
            st.image(_page_image(pdf, page, quotes), width="stretch")
        except PDFPreviewError as exc:
            st.warning(str(exc))
    with right:
        st.markdown("**Extracted page text seen by the model**")
        st.code(stored.document.pages[page - 1].text, language=None, wrap_lines=True, height=520)
        if requirement:
            with st.expander("Selected requirement fields"):
                st.json(requirement.model_dump(mode="json"), expanded=False)
        st.download_button(
            "Download original PDF",
            pdf,
            file_name=stored.original_name,
            mime="application/pdf",
            key="spec_debug_download_original",
        )


def _evidence(
    stored: StoredSpecification, pdf: bytes, run: CustomerExtractionRun, rows: list
) -> None:
    by_id = {row.id: row for row in run.extraction.requirements}
    requirement = None
    if by_id:
        identifier = st.selectbox(
            "Inspect requirement",
            list(by_id),
            key="spec_debug_requirement",
            format_func=lambda value: f"{value} · {by_id[value].original_test_name}",
            on_change=_locate_requirement,
            args=(run, stored.document.page_count),
        )
        requirement = by_id[identifier]
        st.button(
            "Relocate citation",
            key="spec_debug_locate",
            type="tertiary",
            icon=":material/my_location:",
            on_click=_locate_requirement,
            args=(run, stored.document.page_count),
        )
        selected = [row for row in rows if row["ID"] == identifier]
        st.dataframe(selected, hide_index=True, width="stretch")
    st.caption(
        "Text check = literal quotation after whitespace normalization. "
        "Highlight = local PDF-word location (typography/table separators normalized). "
        "Neither proves that the extracted interpretation is correct."
    )
    _source_preview(stored, pdf, requirement)
    with st.expander(f"All quotation checks ({len(rows)})"):
        st.dataframe(rows, hide_index=True, width="stretch", height=320)
    with st.expander(f"Stored extraction warnings ({len(run.extraction.review_issues)})"):
        st.caption(
            "Original flags are preserved; current triage may resolve format-only location issues."
        )
        st.dataframe(
            [{"Warning": issue} for issue in run.extraction.review_issues],
            hide_index=True,
            width="stretch",
        )


def _comparison(run: CustomerExtractionRun, runs: list[CustomerExtractionRun]) -> None:
    st.caption(
        "Saved runs of the same PDF only. Neither is ground truth: this is not an accuracy score. "
        "More records can also mean duplicates, split scopes or incorrect extractions."
    )
    alternatives = {item.run_id: item for item in runs if item.run_id != run.run_id}
    if not alternatives:
        st.info("Save another analysis of this document to compare models, prompts or strategies.")
        return
    if st.session_state.get("spec_debug_compare_run") not in alternatives:
        st.session_state["spec_debug_compare_run"] = next(iter(alternatives))
    baseline_id = st.selectbox(
        "Baseline saved run",
        list(alternatives),
        key="spec_debug_compare_run",
        format_func=lambda value: _run_label(alternatives[value]),
    )
    baseline = alternatives[baseline_id]
    baseline_summary, selected_summary = run_summary(baseline), run_summary(run)
    st.dataframe(
        [
            {
                "Field": field,
                "Baseline": "—"
                if baseline_summary[field] is None
                else str(baseline_summary[field]),
                "Selected": "—"
                if selected_summary[field] is None
                else str(selected_summary[field]),
            }
            for field in [
                "Model",
                "Provider",
                "Prompt",
                "Strategy",
                "Records",
                "Calls",
                "Latency (s)",
                "Input tokens",
                "Output tokens",
                "Total tokens",
                "Method references",
                "Cited pages",
                "Run",
                "Started",
            ]
        ],
        hide_index=True,
        width="stretch",
        height=350,
    )
    changes = comparison_rows(baseline, run)
    st.markdown("**Requirement differences**")
    st.caption(
        "Links use only normalized name + exact method-code set, not canonical mapping. "
        "Repeated signatures stay ambiguous; an unmatched item may be a rename or scope split."
    )
    st.dataframe(changes, hide_index=True, width="stretch", height=400)


def _raw(run: CustomerExtractionRun, stored: StoredSpecification) -> None:
    prediction, raw = st.columns(2)
    with prediction:
        st.markdown("**Validated / expanded prediction**")
        st.caption("Compact responses are expanded locally; this is not untouched model text.")
        st.download_button(
            "Download prediction JSON",
            run.extraction.model_dump_json(indent=2),
            file_name=f"{run.run_id}-prediction.json",
            mime="application/json",
            key="spec_debug_download_prediction",
        )
        with st.container(height=450, border=True):
            st.json(run.extraction.model_dump(mode="json"), expanded=2)
    with raw:
        manifest = run.raw_response_kind == "focused_pass_manifest"
        st.markdown("**Response manifest**" if manifest else "**Original provider response**")
        st.caption(
            "Local manifest, NOT one model's raw response."
            if manifest
            else "Stored provider text, unchanged; whitespace is preserved in the download."
        )
        st.download_button(
            "Download response manifest" if manifest else "Download raw response",
            run.raw_response.encode("utf-8"),
            file_name=f"{run.run_id}-{'manifest.json' if manifest else 'raw.txt'}",
            mime="application/json" if manifest else "text/plain",
            key="spec_debug_download_raw",
        )
        st.code(run.raw_response, language="json", height=450, wrap_lines=True)
    with st.expander("Run metadata"):
        st.json(run.model_dump(mode="json", exclude={"raw_response", "extraction", "calls"}))
    if manifest or len(run.calls) > 1:
        for call in run.calls:
            with st.expander(f"Request {call.call_index} · focus pages {call.focus_pages}"):
                st.caption(f"{call.provider}/{call.model} · {call.latency_ms / 1000:.2f}s")
                st.download_button(
                    f"Download request {call.call_index} raw response",
                    call.raw_response.encode("utf-8"),
                    file_name=f"{run.run_id}-call-{call.call_index}-raw.txt",
                    mime="text/plain",
                    key=f"spec_debug_download_call_{call.call_index}",
                )
                st.code(call.raw_response, language="json", height=300, wrap_lines=True)
    rejected = st.session_state.get("_spec_failed_response")
    if rejected and rejected[0] == stored.document_id:
        with st.expander("Rejected response — not accepted as an extraction"):
            st.code(rejected[1], language="json", height=300)


def render_developer_diagnostics(settings: Settings) -> None:
    st.subheader("Customer-spec diagnostics")
    store = FileSpecStore(settings.scione_workspace_dir)
    try:
        summaries = store.list_documents()
        if not summaries:
            st.info("No customer documents saved. Add a PDF in Specifications first.")
            return
        by_id = {item.document_id: item for item in summaries}
        if st.session_state.get("spec_debug_document") not in by_id:
            st.session_state["spec_debug_document"] = next(iter(by_id))
        doc_column, run_column = st.columns([1, 1])
        with doc_column:
            document_id = st.selectbox(
                "Document",
                list(by_id),
                key="spec_debug_document",
                format_func=lambda value: by_id[value].original_name,
            )
        stored = store.load_document(document_id)
        pdf = store.read_pdf(document_id)
        latest = store.load_latest_extraction(document_id)
        runs = store.list_extractions(document_id)
        failure = store.load_failure(document_id)
        if st.session_state.get("_spec_debug_document_context") != document_id:
            st.session_state["_spec_debug_document_context"] = document_id
            st.session_state["spec_debug_run"] = latest.run_id if latest else None
            st.session_state["spec_debug_text_page"] = 1
        if not runs:
            st.info("This PDF is saved, but no successful extraction has been saved yet.")
            if failure:
                st.warning("The last analysis failed; it is not a successful model result.")
                with st.expander("Last failed attempt"):
                    st.json(failure.model_dump(mode="json"))
            _source_preview(stored, pdf)
            return
        by_run = {item.run_id: item for item in runs}
        if st.session_state.get("spec_debug_run") not in by_run:
            st.session_state["spec_debug_run"] = latest.run_id if latest else runs[0].run_id
        with run_column:
            run_id = st.selectbox(
                "Saved run",
                list(by_run),
                key="spec_debug_run",
                format_func=lambda value: _run_label(by_run[value]),
            )
        run = by_run[run_id]
        _prepare_run(stored, run)
        review = store.load_review(document_id, run_id)
        assessments, checks = _checks(
            run.model_dump_json(),
            stored.document.model_dump_json(exclude_computed_fields=True),
            review.model_dump_json(),
            pdf,
        )
        if latest and run_id != latest.run_id:
            st.info("Viewing a historical run. The lab-facing result and its review are unchanged.")
        overview, requirements, evidence, comparison, raw = st.tabs(
            [
                "Overview",
                "Requirements",
                "Evidence & checks",
                "Compare runs",
                "Raw & metadata",
            ]
        )
        with overview:
            _overview(stored, run, assessments, checks, failure)
        with requirements:
            _requirements(run, assessments, review)
        with evidence:
            _evidence(stored, pdf, run, checks)
        with comparison:
            _comparison(run, runs)
        with raw:
            _raw(run, stored)
    except (SpecStoreError, ValueError) as exc:
        st.error(f"Unable to open customer diagnostics: {exc}")
        st.caption("Saved data has not been changed or reset.")

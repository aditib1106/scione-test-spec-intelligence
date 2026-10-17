"""Lab-facing matching review, explicit quote inputs and immutable PDF/JSON reports."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import pymupdf
import streamlit as st
from pydantic import ValidationError

from scione.catalogs import FileLabStore, LabProfile, ReferenceCatalog, load_reference_catalog
from scione.catalogs.lab_store import LabStoreError
from scione.config import Settings
from scione.reports.matching import build_report, lab_fingerprint
from scione.reports.models import (
    STATUS_LABELS,
    GapLine,
    MappingChoice,
    QuoteChoice,
    ReportSnapshot,
    ReportWorkspace,
)
from scione.reports.pdf import ReportPDFError, render_report_pdf
from scione.reports.store import FileReportStore, ReportStoreError
from scione.schemas import IngestedDocument
from scione.specifications.extraction import coverage_issues
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.pdf_preview import PDFPreviewError, render_pdf_page
from scione.specifications.store import (
    FileSpecStore,
    RequirementReviewSet,
    SpecStoreError,
    StoredSpecification,
)

_TABS = ["Capability review", "PDF preview", "Saved reports", "Developer"]
_VERDICTS = {
    "unreviewed": "Conditions and scope not assessed",
    "supported": "Lab can meet the selected conditions and scope",
    "unsupported": "Lab cannot meet the selected conditions or scope",
}


@st.cache_data(show_spinner=False, max_entries=12)
def _draft(
    document_json: str,
    original_name: str,
    imported_at: datetime,
    run_json: str,
    review_json: str,
    lab_json: str,
    catalog_json: str,
    workspace_json: str,
    pdf: bytes,
) -> ReportSnapshot:
    document = IngestedDocument.model_validate_json(document_json)
    stored = StoredSpecification(document, original_name, imported_at, Path("unused.pdf"))
    return build_report(
        stored,
        CustomerExtractionRun.model_validate_json(run_json),
        RequirementReviewSet.model_validate_json(review_json),
        LabProfile.model_validate_json(lab_json),
        ReferenceCatalog.model_validate_json(catalog_json),
        ReportWorkspace.model_validate_json(workspace_json),
        pdf_bytes=pdf,
    )


@st.cache_data(show_spinner=False, max_entries=16)
def _preview(pdf: bytes, page: int) -> bytes:
    return render_pdf_page(pdf, page_number=page)


def _open_source(
    document_id: str, run_id: str, line: GapLine | None = None, page_count: int = 1
) -> None:
    st.session_state["workspace_page"] = "Specifications"
    st.session_state["_spec_open_after_import"] = document_id
    if line:
        st.session_state["_spec_requirement_run"] = (document_id, run_id)
        st.session_state["_spec_preview_document"] = document_id
        st.session_state["spec_requirement_choice"] = line.requirement.id
        page = next(
            (item.page for item in line.requirement.evidence if 1 <= item.page <= page_count), 1
        )
        st.session_state["spec_pdf_page"] = page
        st.session_state["spec_evidence_page"] = page


def _reset_editor() -> None:
    st.session_state.pop("_report_editor_context", None)


def _save_workspace(store: FileReportStore, workspace: ReportWorkspace) -> None:
    store.save_workspace(workspace, expected_version=st.session_state["_report_editor_version"])
    _reset_editor()
    st.session_state["_report_notice"] = "Review choices saved. Generate a new PDF to capture them."
    st.rerun()


def _prepare_editor(report: ReportSnapshot, line: GapLine) -> bool:
    context = (report.document_id, report.source_run.run_id, line.requirement.id)
    if (
        st.session_state.get("_report_editor_context") != context
        or "report_mapping_note" not in st.session_state
    ):
        choice = line.mapping_choice
        quantity = report.workspace.quantities.get(line.requirement.id)
        known_tests = {test.id for test in report.catalog.tests}
        chosen_test = choice.canonical_test_id if choice else line.canonical_test_id
        original_codes = {method.code for method in line.requirement.requested_methods}
        st.session_state.update(
            {
                "_report_editor_context": context,
                "_report_editor_version": report.workspace.version,
                "_report_editor_fingerprint": report.input_fingerprint,
                "report_mapping_test": chosen_test if chosen_test in known_tests else None,
                "report_mapping_methods": (
                    [code for code in choice.selected_method_codes if code in original_codes]
                    if choice and choice.selected_method_codes is not None
                    else list(
                        dict.fromkeys(method.code for method in line.requirement.requested_methods)
                    )
                ),
                "report_mapping_capability": (
                    choice.capability_id
                    if choice
                    else line.capability.id
                    if line.capability
                    else None
                ),
                "report_mapping_verdict": choice.capability_verdict if choice else "unreviewed",
                "report_mapping_note": choice.note if choice else "",
                "report_quote_quantity": str(quantity.quantity) if quantity else "",
                "report_quote_note": quantity.note if quantity else "",
                "report_quote_confirm": False,
            }
        )
    stale = st.session_state["_report_editor_fingerprint"] != report.input_fingerprint
    if stale:
        st.warning("Source data or review choices changed. Reload this editor before saving.")
        st.button("Reload item", key="report_reload_editor", on_click=_reset_editor)
    return stale


def _mapping_editor(
    report: ReportSnapshot, line: GapLine, store: FileReportStore, stale: bool
) -> None:
    with st.expander(
        "Adjust test matching / confirm lab scope", expanded=_needs_review(report, line)
    ):
        st.caption(
            "This is separate from confirming extracted content. Method substitutions are not "
            "automatically equivalent. A scope confirmation cannot make a missing method available."
        )
        tests = {test.id: test for test in report.catalog.tests}
        st.markdown("**Test identity**")
        candidates = [tests[value].name for value in line.candidate_test_ids if value in tests]
        if candidates:
            st.caption("Candidate identities: " + "; ".join(candidates))
        else:
            st.caption("No catalog candidate identified. A new catalog entry may be needed.")
        st.selectbox(
            "Catalog test identity",
            [None, *tests],
            format_func=lambda value: tests[value].name if value else "Unresolved / not in catalog",
            key="report_mapping_test",
        )
        original_codes = list(
            dict.fromkeys(method.code for method in line.requirement.requested_methods)
        )
        st.multiselect(
            "Requested methods to assess for this scope",
            original_codes,
            key="report_mapping_methods",
            help=(
                "All original references remain in the report. Select a subset only "
                "with a source-based explanation."
            ),
        )
        capabilities = {
            item.id: item
            for item in report.lab.capabilities
            if item.canonical_test_id == st.session_state["report_mapping_test"]
        }
        if st.session_state.get("report_mapping_capability") not in capabilities:
            st.session_state["report_mapping_capability"] = None
        st.markdown("**Lab capability for this request**")
        st.selectbox(
            "Lab declaration to assess",
            [None, *capabilities],
            key="report_mapping_capability",
            format_func=lambda value: (
                f"{value} · "
                + ", ".join(
                    report.catalog.method_by_id(mid).identifier
                    for mid in capabilities[value].method_ids
                )
                if value
                else "No specific declaration selected"
            ),
        )
        st.selectbox(
            "Conditions and scope decision",
            list(_VERDICTS),
            key="report_mapping_verdict",
            format_func=_VERDICTS.__getitem__,
        )
        st.text_area("Matching / scope review note", key="report_mapping_note", max_chars=4000)
        save, reset = st.columns(2)
        if save.button(
            "Save matching review", key="report_save_mapping", type="primary", disabled=stale
        ):
            candidate = report.workspace.model_copy(deep=True)
            candidate.mappings[line.requirement.id] = MappingChoice(
                canonical_test_id=st.session_state["report_mapping_test"],
                selected_method_codes=st.session_state["report_mapping_methods"],
                capability_id=st.session_state["report_mapping_capability"],
                capability_verdict=st.session_state["report_mapping_verdict"],
                lab_fingerprint=lab_fingerprint(report.lab),
                note=st.session_state["report_mapping_note"],
            )
            _save_workspace(store, candidate)
        if reset.button(
            "Use automatic matching",
            key="report_reset_mapping",
            disabled=stale or line.mapping_choice is None,
        ):
            candidate = report.workspace.model_copy(deep=True)
            candidate.mappings.pop(line.requirement.id, None)
            _save_workspace(store, candidate)


def _quote_editor(
    report: ReportSnapshot, line: GapLine, store: FileReportStore, stale: bool
) -> None:
    with st.expander("Optional quote quantity"):
        cap = line.capability
        if cap and cap.price is not None:
            st.write(
                f"Catalog reference rate: **{cap.currency} {cap.price:,f} / {cap.pricing_basis}**"
            )
        else:
            st.info("No catalog rate for this selection. No amount can be calculated.")
        eligible = (
            line.status == "in_house"
            and line.extraction_review_state in {"ready", "confirmed"}
            and line.mapping_choice is not None
            and line.mapping_choice.capability_verdict == "supported"
            and line.mapping_choice.lab_fingerprint == lab_fingerprint(report.lab)
            and cap is not None
            and cap.price is not None
        )
        st.caption(
            "Add a quantity only after reviewing this scope and confirming lab support above. "
            "Resolve flagged source content in Specifications first. Records may overlap or refer "
            "to alternative regions; no quantity or full-job total is inferred."
        )
        with st.form("report_quote_form", border=False):
            st.text_input("Quantity in the catalog pricing basis", key="report_quote_quantity")
            st.text_area(
                "Quantity derivation / applicable scope", key="report_quote_note", max_chars=4000
            )
            st.checkbox(
                "I checked applicability, overlapping records and the quantity's pricing basis",
                key="report_quote_confirm",
            )
            save = st.form_submit_button("Save quote quantity", disabled=stale or not eligible)
        if save:
            if not st.session_state["report_quote_confirm"]:
                st.error("Confirm scope and pricing basis before adding this line.")
            else:
                try:
                    quantity = Decimal(st.session_state["report_quote_quantity"])
                    candidate = report.workspace.model_copy(deep=True)
                    candidate.quantities[line.requirement.id] = QuoteChoice(
                        quantity=quantity,
                        scope_and_basis_confirmed=True,
                        lab_fingerprint=lab_fingerprint(report.lab),
                        note=st.session_state["report_quote_note"],
                    )
                    _save_workspace(store, candidate)
                except InvalidOperation:
                    st.error("Enter a numeric quantity, for example 12 or 2.5.")
        if line.requirement.id in report.workspace.quantities:
            if st.button("Remove quote quantity", key="report_remove_quote", disabled=stale):
                candidate = report.workspace.model_copy(deep=True)
                candidate.quantities.pop(line.requirement.id, None)
                _save_workspace(store, candidate)


def _line_detail(
    report: ReportSnapshot, line: GapLine, store: FileReportStore, page_count: int
) -> None:
    st.divider()
    st.subheader(line.requirement.original_test_name)
    st.caption(f"{line.requirement.id} · {STATUS_LABELS[line.status]}")
    left, right = st.columns(2)
    with left:
        st.markdown("**Customer requirement**")
        st.write(
            "Requested method: "
            + (
                ", ".join(
                    method.code + (f" · revision {method.revision}" if method.revision else "")
                    for method in line.requirement.requested_methods
                )
                or "Not stated"
            )
        )
        for label, values in [
            ("Conditions", line.requirement.conditions),
            ("Acceptance criteria", line.requirement.acceptance_criteria),
        ]:
            st.markdown(f"**{label}**")
            if not values:
                st.caption("Not extracted")
            for value in values:
                st.text(
                    f"{value.name}: {value.raw_value}"
                    + (f" [{value.unit}]" if value.unit else "")
                    + (f" — {value.context}" if value.context else "")
                )
        if line.requirement.applicability:
            st.markdown("**Applicability**")
            for value in line.requirement.applicability:
                st.text(value)
        if line.requirement.special_instructions:
            st.markdown("**Special instructions**")
            for value in line.requirement.special_instructions:
                st.text(value)
    with right:
        st.markdown("**Lab declaration**")
        st.write("Catalog test: " + (line.canonical_test_name or "Unresolved"))
        st.write(
            "Lab methods: "
            + (", ".join(line.lab_method_identifiers) or "None listed for this selection")
        )
        if line.capability:
            st.text(line.capability.declared_constraints or "No operating limits declared.")
            if line.capability.notes:
                st.caption(line.capability.notes)
        for reason in line.reasons:
            st.text(reason)
        if line.mapping_choice:
            st.caption("Human review: " + line.mapping_choice.note)
        if line.extraction_review_state == "needs_attention":
            st.warning(
                "The extracted content still needs review. A match does not resolve that review."
            )
        with st.expander("Source evidence / content review"):
            for evidence in line.requirement.evidence:
                st.caption(f"Source PDF page {evidence.page}")
                st.text(evidence.quote)
            for reason in line.extraction_review_reasons:
                st.caption(reason)
            st.button(
                "Open item in Specifications",
                key="report_open_source",
                on_click=_open_source,
                args=(report.document_id, report.source_run.run_id, line, page_count),
            )
    stale = _prepare_editor(report, line)
    _mapping_editor(report, line, store, stale)
    _quote_editor(report, line, store, stale)


def _lab_scope_review(report: ReportSnapshot, line: GapLine) -> str:
    """Do not mistake an inventory match for a completed capability review."""

    if line.status == "excluded":
        return "Excluded"
    if line.status == "condition_gap":
        return "Outside lab scope"
    if line.status != "in_house":
        return "Resolve test / method first"
    choice = line.mapping_choice
    if (
        choice is not None
        and choice.capability_verdict == "supported"
        and choice.lab_fingerprint == lab_fingerprint(report.lab)
    ):
        return "Confirmed by lab reviewer"
    return "Pending lab review"


def _needs_review(report: ReportSnapshot, line: GapLine) -> bool:
    stale_scope = (
        line.mapping_choice is not None
        and line.mapping_choice.capability_verdict != "unreviewed"
        and line.mapping_choice.lab_fingerprint != lab_fingerprint(report.lab)
    )
    return line.status != "excluded" and (
        line.status == "needs_review"
        or line.extraction_review_state == "needs_attention"
        or stale_scope
        or _lab_scope_review(report, line) == "Pending lab review"
    )


def _capability_review(report: ReportSnapshot, store: FileReportStore, page_count: int) -> None:
    counts = Counter(line.status for line in report.lines)
    pending = sum(_needs_review(report, line) for line in report.lines)
    for column, label, value in zip(
        st.columns(4),
        ["Requirement records", "Methods listed", "Catalog / scope gaps", "Need review"],
        [
            len(report.lines),
            counts["in_house"],
            counts["test_gap"] + counts["method_gap"] + counts["condition_gap"],
            pending,
        ],
        strict=True,
    ):
        column.metric(label, value)
    st.caption(
        "Method listed ≠ all conditions verified. Not listed ≠ proven impossible. "
        "Records may overlap or apply to different regions; they are not a billable test count."
    )
    st.caption(
        "Source content is checked in Specifications. Here, review the test identity and "
        "whether the lab can meet this request's conditions. Clear matches do not need "
        "another identity decision; unresolved matches and lab scope remain visible below."
    )
    groups = ["All active", "Methods listed", "Gaps", "Needs review", "Excluded"]
    selected = st.selectbox("Show requirements", groups, key="report_group")
    lines = [
        line
        for line in report.lines
        if (
            (selected == "All active" and line.status != "excluded")
            or (selected == "Methods listed" and line.status == "in_house")
            or (selected == "Gaps" and line.status in {"test_gap", "method_gap", "condition_gap"})
            or (selected == "Needs review" and _needs_review(report, line))
            or (selected == "Excluded" and line.status == "excluded")
        )
    ]
    st.dataframe(
        [
            {
                "ID": line.requirement.id,
                "Customer test": line.requirement.original_test_name,
                "Requested method": "; ".join(
                    item.code for item in line.requirement.requested_methods
                ),
                "Lab method": "; ".join(line.lab_method_identifiers),
                "Result": STATUS_LABELS[line.status],
                "Content review": {
                    "ready": "No row flags",
                    "confirmed": "Confirmed",
                    "needs_attention": "Needs review",
                    "excluded": "Excluded",
                }[line.extraction_review_state],
                "Lab scope review": _lab_scope_review(report, line),
            }
            for line in lines
        ],
        hide_index=True,
        width="stretch",
        height=350,
        column_order=[
            "Customer test",
            "Result",
            "Content review",
            "Lab scope review",
            "Requested method",
            "Lab method",
            "ID",
        ],
    )
    by_id = {line.requirement.id: line for line in lines}
    if st.session_state.get("report_item") not in by_id:
        st.session_state["report_item"] = None
    identifier = st.selectbox(
        "Inspect a requirement",
        list(by_id),
        index=None,
        key="report_item",
        placeholder="Choose a test to see the requirement, lab declaration and review options",
        format_func=lambda value: f"{value} · {by_id[value].requirement.original_test_name}",
    )
    if identifier:
        _line_detail(report, by_id[identifier], store, page_count)
    else:
        st.info("Select a requirement above to inspect its details or adjust the matching.")
    if report.totals_by_currency:
        st.markdown("**Partial quote subtotal · manually selected lines only**")
        for currency, value in report.totals_by_currency.items():
            st.write(f"{currency} {value:,f}")
    st.caption(
        "Draft amounts use exact decimal arithmetic without currency-specific rounding. "
        "Partner sourcing, unknown rates, preparation costs and lead time are not estimated."
    )


def _saved_label(report: ReportSnapshot) -> str:
    return f"{report.created_at:%b %d %H:%M} · {report.lab.name} · {report.report_id[:8]} · Draft"


def _pdf_preview(
    store: FileReportStore, reports: list[ReportSnapshot], current: ReportSnapshot
) -> None:
    if not reports:
        st.info("No report saved for this specification. Use Generate PDF + JSON above.")
        return
    by_id = {report.report_id: report for report in reports}
    if st.session_state.get("report_snapshot") not in by_id:
        st.session_state["report_snapshot"] = reports[0].report_id
    identifier = st.selectbox(
        "Saved report",
        list(by_id),
        key="report_snapshot",
        format_func=lambda value: _saved_label(by_id[value]),
    )
    report = by_id[identifier]
    if report.input_fingerprint != current.input_fingerprint:
        st.warning(
            "Saved snapshot: source, lab or review data has changed. "
            "Generate a new report for the current state."
        )
    st.caption("Draft for engineer review · not an approved quote or proof of capability.")
    pdf = store.read_pdf(identifier)
    downloads = st.columns(2)
    downloads[0].download_button(
        "Download PDF",
        pdf,
        file_name=f"scione-gap-{identifier[:8]}.pdf",
        mime="application/pdf",
        key="report_download_pdf",
        width="stretch",
    )
    downloads[1].download_button(
        "Download JSON",
        report.model_dump_json(indent=2),
        file_name=f"scione-gap-{identifier[:8]}.json",
        mime="application/json",
        key="report_download_json",
        width="stretch",
    )
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        page_count = len(document)
    if st.session_state.get("_report_preview_snapshot") != identifier:
        st.session_state["_report_preview_snapshot"] = identifier
        st.session_state["report_pdf_page"] = 1
    page = st.number_input("Report page", min_value=1, max_value=page_count, key="report_pdf_page")
    st.caption(f"Page {page} of {page_count} · preview of the exact downloadable PDF")
    with st.container(height=850, border=True):
        st.image(_preview(pdf, page), width="stretch")


def render_reports(settings: Settings) -> None:
    st.title("Reports")
    st.caption(
        "Compare the customer request with your lab catalog, review gaps, "
        "then save a PDF + JSON snapshot."
    )
    spec_store = FileSpecStore(settings.scione_workspace_dir)
    report_store = FileReportStore(settings.scione_workspace_dir)
    try:
        notice = st.session_state.pop("_report_notice", None)
        if notice:
            st.success(notice)
        summaries = spec_store.list_documents()
        if not summaries:
            st.info("No specifications saved yet. Add and analyze a PDF in Specifications first.")
            return
        by_id = {item.document_id: item for item in summaries}
        if st.session_state.get("report_document") not in by_id:
            st.session_state["report_document"] = next(iter(by_id))
        doc_column, action = st.columns([4, 1], vertical_alignment="bottom")
        with doc_column:
            document_id = st.selectbox(
                "Customer specification",
                list(by_id),
                key="report_document",
                format_func=lambda value: by_id[value].original_name,
            )
        stored = spec_store.load_document(document_id)
        run = spec_store.load_latest_extraction(document_id)
        if run is None or not run.extraction.requirements:
            st.info("Analyze this PDF in Specifications before creating a gap report.")
            st.button(
                "Open Specifications",
                key="report_analyze_source",
                on_click=_open_source,
                args=(document_id, ""),
            )
            return
        review = spec_store.load_review(document_id, run.run_id)
        catalog = load_reference_catalog()
        lab = FileLabStore(settings.scione_workspace_dir, catalog).load_or_create()
        workspace = report_store.load_workspace(document_id, run.run_id)
        current = _draft(
            stored.document.model_dump_json(exclude_computed_fields=True),
            stored.original_name,
            stored.created_at,
            run.model_dump_json(),
            review.model_dump_json(),
            lab.model_dump_json(),
            catalog.model_dump_json(),
            workspace.model_dump_json(),
            spec_store.read_pdf(document_id),
        )
        with action:
            if st.button(
                "Generate PDF + JSON", type="primary", key="report_generate", width="stretch"
            ):
                snapshot = current.model_copy(
                    update={"report_id": str(uuid4()), "created_at": datetime.now(UTC)}
                )
                with st.spinner("Building the report locally…"):
                    pdf = render_report_pdf(snapshot)
                    report_store.save_report(snapshot, pdf)
                st.session_state["report_snapshot"] = snapshot.report_id
                st.session_state["report_view_tabs"] = "PDF preview"
                st.session_state["_report_notice"] = (
                    "Draft report saved. PDF and JSON describe the same snapshot."
                )
                st.rerun()
        st.caption(
            f"Lab: {lab.name} · latest analysis: {run.run_id[:8]} · "
            "No model call is needed for matching or export."
        )
        if coverage_issues(run.extraction, stored.document):
            st.warning(
                "Some source method references may not be represented in the extraction. "
                "Check the original specification for omissions before relying on this draft."
            )
        reports = report_store.list_reports(document_id)
        capability, preview, history, developer = st.tabs(
            _TABS, key="report_view_tabs", on_change="rerun"
        )
        with capability:
            _capability_review(current, report_store, stored.document.page_count)
        with preview:
            _pdf_preview(report_store, reports, current)
        with history:
            if not reports:
                st.info("No saved reports for this specification.")
            else:
                st.dataframe(
                    [
                        {
                            "Report": item.report_id[:8],
                            "Created (UTC)": item.created_at.isoformat(),
                            "Lab": item.lab.name,
                            "Lab version": item.lab.version,
                            "Run": item.source_run.run_id[:8],
                            "Records": len(item.lines),
                            "State": "Current inputs"
                            if item.input_fingerprint == current.input_fingerprint
                            else "Historical snapshot",
                        }
                        for item in reports
                    ],
                    hide_index=True,
                    width="stretch",
                )
                st.caption(
                    "Saved reports are immutable. Choose a saved report in PDF preview "
                    "to view or download it."
                )
        with developer:
            st.caption(
                "Local deterministic aliases / identifier matching, "
                "not LLM confidence or standard-content comparison."
            )
            st.json(
                {
                    "fingerprint": current.input_fingerprint,
                    "run": run.run_id,
                    "review_version": review.version,
                    "lab_version": lab.version,
                    "catalog_version": catalog.version,
                    "workspace_version": workspace.version,
                }
            )
            with st.expander("Report warnings"):
                for warning in current.warnings:
                    st.text(warning)
            with st.expander("Matching decisions / source fields"):
                st.json([line.model_dump(mode="json") for line in current.lines], expanded=False)
    except (
        SpecStoreError,
        LabStoreError,
        ReportStoreError,
        ReportPDFError,
        PDFPreviewError,
        ValidationError,
        ValueError,
    ) as exc:
        st.error(f"Unable to complete this report action: {exc}")
        st.caption(
            "Existing source data and saved reports have not been reset. "
            "Check the inputs and retry."
        )

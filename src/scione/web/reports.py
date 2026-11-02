"""Streamlit adapter for matching review, quote inputs and immutable draft reports."""

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
from scione.reports.matching import (
    MATCHING_POLICY_VERSION,
    build_report,
    lab_fingerprint,
    support_basis,
)
from scione.reports.models import (
    STATUS_LABELS,
    GapLine,
    MappingChoice,
    QuoteChoice,
    ReportSnapshot,
    ReportWorkspace,
)
from scione.reports.pdf import ReportPDFError, render_report_pdf
from scione.reports.review import needs_review as _needs_review
from scione.reports.review import scope_review_state as _lab_scope_review
from scione.reports.store import FileReportStore, ReportStoreError
from scione.schemas import IngestedDocument
from scione.specifications.extraction import coverage_issues
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.pdf_preview import PDFPreviewError, render_pdf_page
from scione.specifications.review import REVIEW_POLICY_VERSION
from scione.specifications.store import (
    FileSpecStore,
    RequirementReviewSet,
    SpecStoreError,
    StoredSpecification,
)

_TABS = ["Review & confirm", "Report", "Developer"]
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
    policy_version: str,
) -> ReportSnapshot:
    """Cache separately for source-review policy changes, even with the same input PDF."""
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


def _show_report() -> None:
    st.session_state["report_view_tabs"] = "Report"


def _save_workspace(
    store: FileReportStore, workspace: ReportWorkspace, *, next_item: str | None = None
) -> None:
    store.save_workspace(workspace, expected_version=st.session_state["_report_editor_version"])
    _reset_editor()
    if not st.session_state.get("report_bulk_items"):
        _reset_bulk()
    if next_item:
        st.session_state["_report_next_item"] = next_item
    st.session_state["_report_notice"] = "Review saved. The report will use your updated decision."
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
                "report_scope_note": "",
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
        "Change test or method / record a gap", expanded=line.status == "needs_review"
    ):
        st.caption(
            "Use this only to correct a match or clarify which source methods apply. "
            "A missing lab entry is already reported as a gap; you do not need to confirm it. "
            "Choosing a different method does not make it equivalent to the customer's method."
        )
        tests = {test.id: test for test in report.catalog.tests}
        st.markdown("**Test identity**")
        candidates = [tests[value].name for value in line.candidate_test_ids if value in tests]
        if candidates:
            st.caption("Candidate identities: " + "; ".join(candidates))
        else:
            st.caption("No catalog candidate identified. A new catalog entry may be needed.")
        st.selectbox(
            "Test in the reference catalog",
            [None, *tests],
            format_func=lambda value: tests[value].name if value else "Unresolved / not in catalog",
            key="report_mapping_test",
        )
        original_codes = list(
            dict.fromkeys(method.code for method in line.requirement.requested_methods)
        )
        st.multiselect(
            "Methods required for this request",
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
        if not capabilities:
            st.caption(
                "Your lab has no entry for this test. It will appear as a catalog gap in the "
                "report without any confirmation. Add a capability in My Lab only if it exists."
            )
        st.selectbox(
            "Your lab's matching capability",
            [None, *capabilities],
            key="report_mapping_capability",
            disabled=not capabilities,
            format_func=lambda value: (
                ", ".join(
                    report.catalog.method_by_id(mid).identifier
                    for mid in capabilities[value].method_ids
                )
                + " · "
                + (capabilities[value].declared_constraints or "No operating limits recorded")
                if value
                else "No specific declaration selected"
            ),
        )
        st.selectbox(
            "Can your lab meet these conditions?",
            list(_VERDICTS),
            key="report_mapping_verdict",
            format_func=_VERDICTS.__getitem__,
        )
        st.text_area(
            "Note (optional unless removing a method or recording a limitation)",
            key="report_mapping_note",
            max_chars=4000,
            help=(
                "Explain why a source method does not apply if you remove it. "
                "If your lab cannot meet the scope, describe the limitation. "
                "Otherwise you can leave this blank."
            ),
        )
        save, reset = st.columns(2)
        if save.button(
            "Save matching review", key="report_save_mapping", type="primary", disabled=stale
        ):
            selected_codes = st.session_state["report_mapping_methods"]
            capability_id = st.session_state["report_mapping_capability"]
            verdict = st.session_state["report_mapping_verdict"]
            note = st.session_state["report_mapping_note"].strip()
            if original_codes and not selected_codes:
                st.error(
                    "Keep at least one method from the customer request. "
                    "To leave an unavailable test in the report, keep its original methods."
                )
                return
            if set(selected_codes) != set(original_codes) and not note:
                st.error(
                    "Add a note explaining why the removed method does not apply to this request."
                )
                return
            if verdict in {"supported", "unsupported"} and capability_id not in capabilities:
                st.error(
                    "Choose a lab capability before assessing its operating conditions. "
                    "If your lab has no entry, leave the conditions unassessed; "
                    "the missing capability will remain visible in the report."
                )
                return
            if verdict == "unsupported" and not note:
                st.error("Add a note describing the limitation your lab cannot meet.")
                return
            if verdict == "supported":
                if line.operation_assessment == "gap":
                    st.error(
                        "The request exceeds the recorded lab limits. Update an incorrect lab "
                        "entry in My Lab first; this review cannot override those limits."
                    )
                    return
                if (
                    line.status not in {"in_house", "condition_gap"}
                    or line.capability is None
                    or capability_id != line.capability.id
                    or st.session_state["report_mapping_test"] != line.canonical_test_id
                    or set(selected_codes) != set(line.considered_method_codes)
                ):
                    st.error(
                        "Save the test and method selection with conditions not assessed first. "
                        "Then confirm the displayed lab scope. A missing or different method "
                        "cannot be approved as a match."
                    )
                    return
            if not note:
                note = (
                    "Saved the test and method selection; original method references retained. "
                    + (
                        "Lab reviewer marked the selected operating scope as supported."
                        if verdict == "supported"
                        else "Operating scope was not assessed."
                    )
                )
            candidate = report.workspace.model_copy(deep=True)
            candidate.mappings[line.requirement.id] = MappingChoice(
                canonical_test_id=st.session_state["report_mapping_test"],
                selected_method_codes=selected_codes if original_codes else None,
                capability_id=capability_id,
                capability_verdict=verdict,
                lab_fingerprint=lab_fingerprint(report.lab),
                note=note,
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


def _scope_actions(
    report: ReportSnapshot, line: GapLine, store: FileReportStore, stale: bool
) -> None:
    """Confirm the displayed, persisted match, never an unsaved identity edit."""

    eligible = line.status in {"in_house", "condition_gap"} and line.capability is not None
    automatic = support_basis(line, lab_fingerprint(report.lab)) == "automatic"
    with st.container(height=90, border=False, key="report_scope_status"):
        if line.status == "needs_review":
            st.info(
                "The applicable test or method is unclear. Check the specification, "
                "then use the open matching form below. No lab-support decision is needed yet."
            )
        elif line.status in {"test_gap", "method_gap"}:
            st.info(
                "This test or requested method is not listed in your current lab catalog. "
                "It is already included as a gap in the report — no confirmation needed. "
                "If your lab offers it, update My Lab or correct the matching below."
            )
        elif _lab_scope_review(report, line) == "Confirmed by lab reviewer":
            st.success("Lab support confirmed for this requirement.")
        elif _lab_scope_review(report, line) == "Operating conditions matched automatically":
            st.success("Operating conditions matched automatically. No extra confirmation needed.")
        elif line.status == "condition_gap":
            st.warning(
                "The requested operations exceed declared lab limits. This is already a "
                "report gap — no confirmation needed. Update My Lab if the limits are incorrect."
                if line.operation_assessment == "gap"
                else "Your review records that the lab cannot meet this scope."
            )
        else:
            st.info(
                "Check the displayed requirements, then confirm whether your lab can meet them."
            )
    # A catalog gap or unresolved identity is not a lab operating decision.
    # Do not show two unusable buttons where the user has nothing to approve.
    if not eligible or line.operation_assessment == "gap":
        return
    confirm, gap = st.columns(2)
    supported = False
    if not automatic:
        supported = confirm.button(
            "Confirm lab support",
            key="report_confirm_scope",
            type="primary",
            disabled=stale,
            width="stretch",
        )
    else:
        confirm.caption("Already matched. You can still record a limitation if needed.")
    unsupported = gap.button(
        "Cannot meet this scope",
        key="report_decline_scope",
        disabled=stale,
        width="stretch",
    )
    with st.expander("Add a review note"):
        st.text_area(
            "Review note (required when recording a limitation)",
            key="report_scope_note",
            max_chars=4000,
            placeholder="Explain any limitations or add context…",
        )
    if (supported or unsupported) and eligible and not stale:
        if supported and line.operation_assessment == "gap":
            st.error("This request exceeds the recorded lab limits. Update My Lab first.")
            return
        note = st.session_state["report_scope_note"].strip()
        if unsupported and not note:
            st.error("Add a review note explaining the limitation before recording this gap.")
            return
        candidate = report.workspace.model_copy(deep=True)
        candidate.mappings[line.requirement.id] = MappingChoice(
            canonical_test_id=line.canonical_test_id,
            selected_method_codes=list(line.considered_method_codes),
            capability_id=line.capability.id,
            capability_verdict="supported" if supported else "unsupported",
            lab_fingerprint=lab_fingerprint(report.lab),
            note=note
            or (
                "Lab reviewer confirmed the displayed conditions, acceptance criteria "
                "and applicable scope for this requirement."
            ),
        )
        next_item = None
        if st.session_state.get("report_advance_after_save", True):
            visible = _visible_lines(report, st.session_state.get("report_group", "All active"))
            ordered = [item.requirement.id for item in visible]
            position = ordered.index(line.requirement.id) if line.requirement.id in ordered else -1
            pending = {
                item.requirement.id
                for item in visible
                if item.requirement.id != line.requirement.id and _needs_review(report, item)
            }
            next_item = next(
                (
                    identifier
                    for identifier in ordered[position + 1 :] + ordered[: position + 1]
                    if identifier in pending
                ),
                None,
            )
        _save_workspace(store, candidate, next_item=next_item)


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
            and support_basis(line, lab_fingerprint(report.lab)) is not None
            and cap is not None
            and cap.price is not None
        )
        st.caption(
            "Add a quantity after lab review or an automatic operating match. "
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


def _requirement_details(
    report: ReportSnapshot, line: GapLine, store: FileReportStore, page_count: int
) -> None:
    st.subheader(line.requirement.original_test_name)
    st.caption(_item_status(report, line))
    source_pages = sorted({item.page for item in line.requirement.evidence})
    source_label = "Check extracted text"
    if source_pages:
        source_label += " · p. " + ", ".join(str(page) for page in source_pages)
    if line.extraction_review_state == "needs_attention":
        st.warning(
            "The extracted text needs checking against the PDF in Specifications. "
            "Confirming lab support here will not clear that check."
        )
    st.button(
        source_label,
        key="report_open_source",
        icon=":material/open_in_new:",
        on_click=_open_source,
        args=(report.document_id, report.source_run.run_id, line, page_count),
    )
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown("**Customer asks for**")
            st.write(
                ", ".join(
                    method.code + (f" · revision {method.revision}" if method.revision else "")
                    for method in line.requirement.requested_methods
                )
                or "Not stated"
            )
            st.caption("Requested method")
    with right:
        with st.container(border=True):
            st.markdown("**Your lab lists**")
            st.write(
                ", ".join(line.lab_method_identifiers)
                or (
                    "Matching needs clarification"
                    if line.status == "needs_review"
                    else "No matching method listed"
                )
            )
            st.caption(line.canonical_test_name or "Test identity needs confirmation")
    with st.container(border=True):
        st.markdown("**Requirements to check**")
        for label, values in [
            ("Conditions", line.requirement.conditions),
            ("Acceptance criteria", line.requirement.acceptance_criteria),
        ]:
            st.markdown(f"**{label}**")
            if not values:
                st.caption("Not stated in the extracted content")
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
        if line.capability:
            st.markdown("**Your lab's recorded limits**")
            st.text(line.capability.declared_constraints or "No operating limits declared.")
            if line.capability.notes:
                st.caption(line.capability.notes)
        if line.mapping_choice:
            st.caption("Saved review note: " + line.mapping_choice.note)
    if line.status == "excluded":
        st.info("Excluded in Specifications. This item is not included in the quote.")
        return
    if line.operation_checks:
        with st.expander("Automatic operating comparison"):
            for check in line.operation_checks:
                st.text(check)
            st.caption("This checks declared operating limits, not whether the material will pass.")


def _line_detail(
    report: ReportSnapshot, line: GapLine, store: FileReportStore, page_count: int
) -> None:
    with st.container(height=540, border=False, key="report_requirement_details"):
        _requirement_details(report, line, store, page_count)
    if line.status == "excluded":
        return
    stale = _prepare_editor(report, line)
    _scope_actions(report, line, store, stale)
    _mapping_editor(report, line, store, stale)
    _quote_editor(report, line, store, stale)


def _item_status(report: ReportSnapshot, line: GapLine) -> str:
    if line.status == "excluded":
        return "Excluded from this request"
    if line.extraction_review_state == "needs_attention":
        return "Check extracted text in Specifications"
    return {
        "in_house": (
            "Operating conditions matched automatically"
            if support_basis(line, lab_fingerprint(report.lab)) == "automatic"
            else "Lab support confirmed"
            if support_basis(line, lab_fingerprint(report.lab)) == "human"
            else "Check remaining operating requirements"
        ),
        "needs_review": (
            "No catalog match yet"
            if not line.candidate_test_ids
            else "Choose the correct test / method"
        ),
        "test_gap": "Gap · test not listed by your lab",
        "method_gap": "Gap · requested method not listed",
        "condition_gap": "Gap · outside your lab's scope",
    }[line.status]


def _reset_bulk() -> None:
    st.session_state.pop("_report_bulk_context", None)


def _reset_bulk_acknowledgement() -> None:
    st.session_state["report_bulk_ack"] = False


def _bulk_scope_review(report: ReportSnapshot, store: FileReportStore) -> None:
    """A selected batch is one lab decision, never a source review or quote."""

    context = (report.document_id, report.source_run.run_id)
    if (
        st.session_state.get("_report_bulk_context") != context
        or "report_bulk_note" not in st.session_state
    ):
        st.session_state.update(
            {
                "_report_bulk_context": context,
                "_report_bulk_fingerprint": report.input_fingerprint,
                "_report_bulk_version": report.workspace.version,
                "report_bulk_items": [],
                "report_bulk_ack": False,
                "report_bulk_note": "",
            }
        )
    by_id = {line.requirement.id: line for line in report.lines}
    eligible = {
        identifier: line
        for identifier, line in by_id.items()
        if line.status in {"in_house", "condition_gap"} and line.capability is not None
    }
    options = list(
        dict.fromkeys(
            [
                *eligible,
                *(key for key in st.session_state.get("report_bulk_items", []) if key in by_id),
            ]
        )
    )
    with st.popover("Batch lab review", icon=":material/checklist:", width="stretch"):
        st.caption(
            "Select only tests whose displayed requirements you have checked. "
            "This confirms lab support, not extracted text or a quote."
        )
        if not options:
            st.info("Resolve a test and method match before adding it to a lab-review batch.")
            return
        selected = st.multiselect(
            "Tests to review together",
            options,
            key="report_bulk_items",
            format_func=lambda value: (
                by_id[value].requirement.original_test_name
                + " · "
                + (", ".join(by_id[value].considered_method_codes) or "Method not stated")
            ),
            on_change=_reset_bulk_acknowledgement,
        )
        st.caption(f"{len(selected)} selected · no tests selected automatically")
        for identifier in selected:
            line = by_id[identifier]
            st.write(line.requirement.original_test_name)
            st.caption(
                "Conditions: "
                + (
                    "; ".join(item.raw_value for item in line.requirement.conditions)
                    or "Not stated"
                )
                + " · Criteria: "
                + (
                    "; ".join(item.raw_value for item in line.requirement.acceptance_criteria)
                    or "Not stated"
                )
                + " · Scope: "
                + ("; ".join(line.requirement.applicability) or "Not stated")
            )
            if line.capability:
                st.caption(
                    "Lab limits: " + (line.capability.declared_constraints or "Not declared")
                )
        source_pending = sum(
            by_id[identifier].extraction_review_state == "needs_attention"
            for identifier in selected
        )
        if source_pending:
            st.warning(
                f"{source_pending} selected items still need a source check. "
                "A lab decision will not clear those checks or make them quotable."
            )
        stale = st.session_state["_report_bulk_fingerprint"] != report.input_fingerprint
        if stale:
            st.warning("Source, lab or review data changed. Reload the batch and check it again.")
        st.checkbox(
            "I checked the conditions, criteria and applicable scope for every selected test",
            key="report_bulk_ack",
        )
        st.text_area("Batch review note / limitation", key="report_bulk_note", max_chars=4000)
        disabled = stale or not selected or not st.session_state["report_bulk_ack"]
        operating_gap = any(by_id[value].operation_assessment == "gap" for value in selected)
        if operating_gap:
            st.info(
                "A selected request exceeds declared operating limits; "
                "support confirmation is disabled."
            )
        support, decline = st.columns(2)
        supported = support.button(
            "Confirm selected",
            key="report_bulk_confirm",
            type="primary",
            disabled=disabled or operating_gap,
        )
        unsupported = decline.button(
            "Cannot meet selected", key="report_bulk_decline", disabled=disabled
        )
        st.button("Clear / reload batch", key="report_bulk_reload", on_click=_reset_bulk)
        if (supported or unsupported) and not disabled:
            if any(identifier not in eligible for identifier in selected):
                st.error("A selected test no longer has a resolved method and lab declaration.")
                return
            if supported and any(
                eligible[value].operation_assessment == "gap" for value in selected
            ):
                st.error(
                    "A selected request exceeds declared operating limits. "
                    "Update the lab declaration first."
                )
                return
            note = st.session_state["report_bulk_note"].strip()
            if unsupported and not note:
                st.error("Explain the limitation before marking the selected scopes unsupported.")
                return
            workspace = report.workspace.model_copy(deep=True)
            for identifier in selected:
                line = eligible[identifier]
                workspace.mappings[identifier] = MappingChoice(
                    canonical_test_id=line.canonical_test_id,
                    selected_method_codes=list(line.considered_method_codes),
                    capability_id=line.capability.id,
                    capability_verdict="supported" if supported else "unsupported",
                    lab_fingerprint=lab_fingerprint(report.lab),
                    note=note
                    or "Lab reviewer checked and confirmed this request's conditions, "
                    "acceptance criteria and applicable scope in a selected batch review.",
                )
            store.save_workspace(
                workspace, expected_version=st.session_state["_report_bulk_version"]
            )
            _reset_bulk()
            _reset_editor()
            st.session_state["_report_notice"] = (
                f"Lab review saved for {len(selected)} selected tests."
            )
            st.rerun()


def _visible_lines(report: ReportSnapshot, selected: str) -> list[GapLine]:
    return [
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


def _capability_review(report: ReportSnapshot, store: FileReportStore, page_count: int) -> None:
    counts = Counter(line.status for line in report.lines)
    pending = sum(_needs_review(report, line) for line in report.lines)
    for column, label, value in zip(
        st.columns(3),
        ["Need review", "Operating support resolved", "Catalog / scope gaps"],
        [
            pending,
            sum(
                support_basis(line, lab_fingerprint(report.lab)) is not None
                for line in report.lines
            ),
            counts["test_gap"] + counts["method_gap"] + counts["condition_gap"],
        ],
        strict=True,
    ):
        column.metric(label, value)
    st.caption(
        "Check each request below. Missing catalog entries remain gaps, not proof of impossibility."
    )
    active = [line for line in report.lines if line.status != "excluded"]
    source_checks = sum(line.extraction_review_state == "needs_attention" for line in active)
    scope_checks = sum(_lab_scope_review(report, line) == "Pending lab review" for line in active)
    st.caption(
        f"Source text checks: {source_checks}"
        f" · Catalog matching: {counts['needs_review']}"
        f" · Lab scope checks: {scope_checks}"
        ". These overlap; they are not a model error rate."
    )
    navigation, detail = st.columns([1, 2.4], gap="large")
    groups = ["Needs review", "All active", "Methods listed", "Gaps", "Excluded"]
    with navigation:
        st.subheader("Choose a test")
        selected = st.selectbox("Show", groups, key="report_group")
    lines = _visible_lines(report, selected)
    by_id = {line.requirement.id: line for line in lines}
    next_item = st.session_state.pop("_report_next_item", None)
    if next_item in by_id:
        st.session_state["report_item"] = next_item
    if st.session_state.get("report_item") not in by_id:
        st.session_state["report_item"] = next(
            (line.requirement.id for line in lines if _needs_review(report, line)),
            next(iter(by_id), None),
        )
    with navigation:
        identifier = st.selectbox(
            "Selected test",
            list(by_id),
            key="report_item",
            index=None,
            placeholder="No tests in this group",
            format_func=lambda value: by_id[value].requirement.original_test_name,
        )
        st.caption(f"{len(lines)} items in this group")
        st.checkbox("Next pending item after saving", value=True, key="report_advance_after_save")
        if identifier:
            st.write(_item_status(report, by_id[identifier]))
            other_pending = [
                item.requirement.id
                for item in lines
                if item.requirement.id != identifier and _needs_review(report, item)
            ]
            if other_pending:
                position = list(by_id).index(identifier)
                following = [
                    value for value in other_pending if list(by_id).index(value) > position
                ]
                st.button(
                    "Next item to review",
                    key="report_next_pending",
                    on_click=_select_item,
                    args=((following or other_pending)[0],),
                    icon=":material/arrow_forward:",
                )
        st.divider()
        st.button(
            "Continue to report",
            key="report_continue",
            width="stretch",
            on_click=_show_report,
            icon=":material/description:",
        )
        st.caption("Unresolved items stay visible in the draft report.")
        _bulk_scope_review(report, store)
    with detail:
        if identifier:
            with st.container(border=True):
                _line_detail(report, by_id[identifier], store, page_count)
        else:
            st.info("No items in this group. Choose another group or continue to your report.")


def _select_item(identifier: str) -> None:
    st.session_state["report_item"] = identifier


def _diagnostic_table(report: ReportSnapshot) -> None:
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
            for line in report.lines
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


def _saved_label(report: ReportSnapshot) -> str:
    return f"{report.created_at:%b %d, %H:%M:%S} UTC · {report.lab.name} · Draft"


def _pdf_preview(
    store: FileReportStore, reports: list[ReportSnapshot], current: ReportSnapshot
) -> None:
    if not reports:
        st.info(
            "No report saved for this specification. Create a draft report above to preview it."
        )
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
    st.download_button(
        "Download PDF",
        pdf,
        file_name=f"scione-gap-{identifier[:8]}.pdf",
        mime="application/pdf",
        key="report_download_pdf",
        type="primary",
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


def _report_output(
    current: ReportSnapshot, store: FileReportStore, reports: list[ReportSnapshot]
) -> None:
    st.subheader("Create your draft report")
    pending = sum(_needs_review(current, line) for line in current.lines)
    if pending:
        st.info(
            f"{pending} items still need review. You can create a draft now; "
            "unresolved items will be clearly marked, not treated as confirmed."
        )
    else:
        st.success("No open item reviews. Recorded gaps will remain in the report.")
    st.caption(
        "Includes requested tests, conditions, lab coverage and gaps. "
        "This draft is not a final approved quote."
    )
    if current.totals_by_currency:
        st.markdown("**Partial quote subtotal · manually selected lines only**")
        for currency, value in current.totals_by_currency.items():
            st.write(f"{currency} {value:,f}")
    with st.expander("Pricing included in this draft"):
        quoted = [line for line in current.lines if line.quote]
        if not quoted:
            st.write("No quote quantities have been confirmed. No job total is estimated.")
        for line in quoted:
            quote = line.quote
            st.write(
                f"{line.requirement.original_test_name}: {quote.quantity:f} × "
                f"{quote.currency} {quote.unit_price:f} / {quote.pricing_basis} = "
                f"{quote.currency} {quote.subtotal:f}"
            )
        st.caption(
            "Add quantities within a test's optional pricing section after confirming lab support. "
            "Records may overlap and are not a billable test count. "
            "Unknown rates, partner costs and lead times are not estimated."
        )
    if st.button(
        "Create draft report", type="primary", key="report_generate", icon=":material/description:"
    ):
        snapshot = current.model_copy(
            update={"report_id": str(uuid4()), "created_at": datetime.now(UTC)}
        )
        with st.spinner("Preparing your PDF…"):
            pdf = render_report_pdf(snapshot)
            store.save_report(snapshot, pdf)
        st.session_state["report_snapshot"] = snapshot.report_id
        st.session_state["_report_open_after_generate"] = True
        st.session_state["_report_notice"] = "Draft report saved. Preview or download it below."
        st.rerun()
    st.divider()
    st.subheader("Preview & download")
    _pdf_preview(store, reports, current)


def _developer(current: ReportSnapshot, reports: list[ReportSnapshot]) -> None:
    st.caption(
        "Local deterministic aliases / identifier matching, "
        "not LLM confidence or standard-content comparison. No model call is needed for export."
    )
    st.subheader("Matching & review diagnostics")
    _diagnostic_table(current)
    with st.expander("Input versions & report warnings"):
        st.json(
            {
                "fingerprint": current.input_fingerprint,
                "run": current.source_run.run_id,
                "review_version": current.source_review.version,
                "lab_version": current.lab.version,
                "catalog_version": current.catalog.version,
                "workspace_version": current.workspace.version,
                "review_policy": REVIEW_POLICY_VERSION,
            }
        )
        for warning in current.warnings:
            st.text(warning)
    with st.expander("Matching decisions / source fields"):
        st.json([line.model_dump(mode="json") for line in current.lines], expanded=False)
    if reports:
        st.subheader("Saved report data")
        selected = next(
            (item for item in reports if item.report_id == st.session_state.get("report_snapshot")),
            reports[0],
        )
        st.caption(
            "JSON corresponds to the saved report selected in the Report tab, not unsaved edits."
        )
        st.download_button(
            "Download JSON",
            selected.model_dump_json(indent=2),
            file_name=f"scione-gap-{selected.report_id[:8]}.json",
            mime="application/json",
            key="report_download_json",
        )
        with st.expander("Saved report history"):
            st.dataframe(
                [
                    {
                        "Report": item.report_id[:8],
                        "Created (UTC)": item.created_at.isoformat(),
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
        "Saved PDFs and their JSON are immutable snapshots. Matching edits do not change them."
    )


def render_reports(settings: Settings) -> None:
    st.title("Reports")
    spec_store = FileSpecStore(settings.scione_workspace_dir)
    report_store = FileReportStore(settings.scione_workspace_dir)
    try:
        notice = st.session_state.pop("_report_notice", None)
        if notice:
            st.toast(notice, icon=":material/check_circle:")
        summaries = spec_store.list_documents()
        if not summaries:
            st.info("No specifications saved yet. Add and analyze a PDF in Specifications first.")
            return
        by_id = {item.document_id: item for item in summaries}
        if st.session_state.get("report_document") not in by_id:
            st.session_state["report_document"] = next(iter(by_id))
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
        lab = FileLabStore(settings.scione_workspace_dir, catalog, seed_demo=False).load_or_create()
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
            f"{REVIEW_POLICY_VERSION}/{MATCHING_POLICY_VERSION}",
        )
        st.caption(
            f"{lab.name} · Review only the items that need your judgment, or continue to a draft. "
            "Recorded gaps do not need confirmation."
        )
        if coverage_issues(run.extraction, stored.document):
            st.warning(
                "Some tests may be missing from the extraction. Check the original PDF "
                "for omissions before sharing the report."
            )
        reports = report_store.list_reports(document_id)
        if st.session_state.pop("_report_open_after_generate", False):
            st.session_state["report_view_tabs"] = "Report"
        if st.session_state.get("report_view_tabs") not in _TABS:
            st.session_state["report_view_tabs"] = _TABS[0]
        capability, output, developer = st.tabs(_TABS, key="report_view_tabs", on_change="rerun")
        with capability:
            _capability_review(current, report_store, stored.document.page_count)
        with output:
            _report_output(current, report_store, reports)
        with developer:
            _developer(current, reports)
    except (
        SpecStoreError,
        LabStoreError,
        ReportStoreError,
        ReportPDFError,
        PDFPreviewError,
        ValidationError,
        ValueError,
    ) as exc:
        st.error(
            "This report action could not be completed. Your selections or saved data may "
            "have changed. Reload the item and check your selections before trying again."
        )
        st.caption(
            "Existing source data and saved reports have not been reset. "
            "Check the inputs and retry."
        )
        with st.expander("Developer error details"):
            st.code(str(exc), language=None)

"""Persistent customer documents, source preview, and explicit extraction review."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path

import streamlit as st

from scione.config import ConfigurationError, Settings
from scione.providers.factory import create_configured_provider
from scione.schemas import IngestedDocument, TextExtractionStatus
from scione.specifications import CustomerExtractionError, CustomerRequirementRunner
from scione.specifications.extraction import build_plan, coverage_issues
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.specifications.pdf_preview import (
    PDFPreviewError,
    inspect_pdf_highlights,
    render_pdf_page,
)
from scione.specifications.review import assess_requirements
from scione.specifications.store import (
    ExtractionFailure,
    FileSpecStore,
    RequirementReviewSet,
    SpecStoreError,
    StoredSpecification,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_SPEC_PATH = REPOSITORY_ROOT / (
    "(Liyang's provided SPEC) MS-PNT-410 Material Performance Standard Exterior Topcoat System.pdf"
)


@st.cache_data(show_spinner=False, max_entries=24)
def _preview_image(pdf: bytes, page: int, quotes: tuple[str, ...] = ()) -> bytes:
    return render_pdf_page(pdf, page_number=page, highlight_quotes=quotes)


@st.cache_data(show_spinner=False, max_entries=24)
def _cached_assessments(run_json: str, document_json: str, review_json: str, pdf: bytes) -> dict:
    return assess_requirements(
        CustomerExtractionRun.model_validate_json(run_json),
        IngestedDocument.model_validate_json(document_json),
        RequirementReviewSet.model_validate_json(review_json),
        pdf_bytes=pdf,
    )


def _assess_review(
    store: FileSpecStore,
    stored: StoredSpecification,
    run: CustomerExtractionRun,
    review: RequirementReviewSet,
) -> dict:
    return _cached_assessments(
        run.model_dump_json(),
        stored.document.model_dump_json(exclude_computed_fields=True),
        review.model_dump_json(),
        store.read_pdf(stored.document_id),
    )


def _import_document(store: FileSpecStore, name: str, data: bytes) -> None:
    try:
        stored = store.import_pdf(name, data)
    except SpecStoreError as exc:
        st.error(str(exc))
        return
    st.session_state["_spec_open_after_import"] = stored.document_id
    st.session_state["_spec_notice"] = "PDF saved locally. No model request was made."
    st.rerun()


def _render_intake(store: FileSpecStore, *, compact: bool = False) -> None:
    panel = (
        st.popover("Add PDF", icon=":material/upload_file:", width="stretch")
        if compact
        else st.expander("Add a specification", expanded=True)
    )
    with panel:
        uploaded = st.file_uploader(
            "Customer specification PDF", type=["pdf"], key="spec_upload_pdf"
        )
        if st.button("Save specification", key="spec_save_upload", disabled=uploaded is None):
            _import_document(store, uploaded.name, uploaded.getvalue())
        st.caption("Text PDFs only · up to 25 MB / 200 pages · saved locally")
        if SAMPLE_SPEC_PATH.is_file():
            if st.button("Import supplied demo spec", key="spec_import_sample"):
                try:
                    _import_document(store, SAMPLE_SPEC_PATH.name, SAMPLE_SPEC_PATH.read_bytes())
                except OSError as exc:
                    st.error(f"Unable to read the local sample: {exc}")


def _set_pdf_page(page: int) -> None:
    st.session_state["spec_pdf_page"] = page


def _focus_requirement(stored: StoredSpecification, run: CustomerExtractionRun) -> None:
    """A selection callback runs before either column, keeping the PDF in sync."""

    selected = st.session_state.get("spec_requirement_choice")
    requirement = next((item for item in run.extraction.requirements if item.id == selected), None)
    if requirement is not None:
        first = next(
            (item for item in requirement.evidence if 1 <= item.page <= stored.document.page_count),
            None,
        )
        if first is not None:
            st.session_state["spec_pdf_page"] = first.page
            st.session_state["spec_evidence_page"] = first.page


def _open_requirement(
    requirement_id: str, stored: StoredSpecification, run: CustomerExtractionRun
) -> None:
    st.session_state["spec_requirement_choice"] = requirement_id
    st.session_state.pop("_spec_manual_review", None)
    _focus_requirement(stored, run)


def _prepare_selection(stored: StoredSpecification, run: CustomerExtractionRun | None) -> None:
    if st.session_state.get("_spec_preview_document") != stored.document_id:
        st.session_state["spec_pdf_page"] = 1
        st.session_state["_spec_preview_document"] = stored.document_id
    if run is not None:
        context = (stored.document_id, run.run_id)
        ids = [item.id for item in run.extraction.requirements]
        if ids and (
            st.session_state.get("_spec_requirement_run") != context
            or (
                st.session_state.get("spec_requirement_choice") is not None
                and st.session_state.get("spec_requirement_choice") not in ids
            )
        ):
            st.session_state["_spec_requirement_run"] = context
            st.session_state["spec_requirement_choice"] = None


def _selected_requirement(run: CustomerExtractionRun | None) -> CustomerRequirement | None:
    if run is None:
        return None
    return next(
        (
            item
            for item in run.extraction.requirements
            if item.id == st.session_state.get("spec_requirement_choice")
        ),
        None,
    )


def _render_pdf(
    stored: StoredSpecification, pdf: bytes, requirement: CustomerRequirement | None = None
) -> None:
    document = stored.document
    st.subheader("Your document")
    page = int(st.session_state.get("spec_pdf_page", 1))
    previous, counter, following = st.columns([1, 2, 1], vertical_alignment="bottom")
    with previous:
        st.button(
            "Previous page",
            disabled=page <= 1,
            on_click=_set_pdf_page,
            args=(page - 1,),
            width="content",
            type="tertiary",
            icon=":material/chevron_left:",
        )
    with counter:
        page = st.number_input(
            f"PDF page (of {document.page_count})",
            min_value=1,
            max_value=document.page_count,
            step=1,
            key="spec_pdf_page",
        )
    with following:
        st.button(
            "Next page",
            disabled=page >= document.page_count,
            on_click=_set_pdf_page,
            args=(page + 1,),
            width="content",
            type="tertiary",
            icon=":material/chevron_right:",
        )
    try:
        quotes = (
            tuple(item.quote for item in requirement.evidence if item.page == page)
            if requirement
            else ()
        )
        if quotes:
            result = inspect_pdf_highlights(pdf, page_number=page, quotes=quotes)
            has_full_match = any(item.status == "matched" for item in result.matches)
            if has_full_match:
                st.caption("Selected test highlighted below")
            elif result.rectangles:
                st.caption("Partial source text located; check the quotation against the PDF.")
            else:
                # A locator is not evidence validation. Never relabel it as an exact quote.
                locator = inspect_pdf_highlights(
                    pdf, page_number=page, quotes=(requirement.original_test_name,)
                )
                if locator.rectangles:
                    quotes = (requirement.original_test_name,)
                    st.caption("Test name highlighted only — the model quotation did not match.")
                else:
                    st.warning("Quotation not located on this page. Check the source manually.")
                    quotes = ()
        elif requirement:
            st.caption("No citation on this page. Select a source page on the right to return.")
        st.image(_preview_image(pdf, page, quotes), width="stretch")
    except PDFPreviewError as exc:
        st.error(str(exc))


def _key_is_ready(settings: Settings, provider: str) -> bool:
    try:
        if provider == "groq":
            settings.require_groq_api_key()
        else:
            settings.require_gemini_api_key()
    except ConfigurationError:
        return False
    return True


def _render_extraction_controls(
    settings: Settings, store: FileSpecStore, stored: StoredSpecification, *, has_result: bool
) -> None:
    with st.popover(
        "Analyze again" if has_result else "Analyze PDF",
        icon=":material/auto_awesome:",
        width="content",
    ):
        st.markdown("**Find test requirements**")
        providers = ["gemini", "groq"]
        default_index = (
            providers.index(settings.model_provider) if settings.model_provider in providers else 0
        )
        provider = st.selectbox("AI provider", providers, index=default_index, key="spec_provider")
        configured_model = settings.groq_model if provider == "groq" else settings.gemini_model
        with st.expander("Advanced model settings"):
            model = st.text_input(
                "Model for next extraction", value=configured_model, key=f"spec_model_{provider}"
            ).strip()
            strategy = st.selectbox(
                "Reading mode",
                ["compact_document", "whole_document", "focused_passes"],
                format_func=lambda value: {
                    "compact_document": "Compact extraction · one request",
                    "whole_document": "Detailed extraction · one request",
                    "focused_passes": "Detailed coverage passes · multiple requests",
                }[value],
                key="spec_extraction_strategy",
            )
            configured_limit = (
                settings.groq_max_completion_tokens
                if provider == "groq"
                else settings.scione_customer_gemini_output_tokens
            )
            output_limit = st.number_input(
                "Max output tokens per request",
                min_value=128,
                step=128,
                value=configured_limit,
                key=f"spec_output_tokens_{provider}",
                help="This is an output ceiling, not a spending cap or an account quota.",
            )
        plan_error = None
        try:
            call_count = len(build_plan(stored.document, strategy))
        except CustomerExtractionError as exc:
            plan_error = str(exc)
            call_count = 0
        if strategy == "focused_passes":
            st.caption(
                f"Up to {call_count} requests, with a different 3-page focus each time. "
                "Every request still includes the full PDF text for shared context. "
                "This improves coverage checks, not input-token size; no automatic retries."
            )
        else:
            st.caption(
                "One model request. Your previous result stays saved until analysis succeeds."
            )
        if plan_error:
            st.warning(plan_error)
        if provider == "groq":
            st.caption(
                "Groq: a per-minute output quota can reject the requested ceiling. "
                "Lowering it may instead truncate the JSON; coverage passes do not bypass quotas."
            )
        permission_context = (stored.document_id, provider, model, strategy, output_limit)
        if st.session_state.get("_spec_permission_context") != permission_context:
            st.session_state["_spec_permission_context"] = permission_context
            st.session_state["spec_external_permission"] = False
            st.session_state["spec_free_tier_confirmed"] = False
        allowed = st.checkbox(
            "I have permission to send this document to the selected provider",
            key="spec_external_permission",
        )
        free_confirmed = True
        if provider == "gemini":
            free_confirmed = st.checkbox(
                "I confirmed this Google project is on the Free tier",
                key="spec_free_tier_confirmed",
            )
        ready = stored.document.extraction_status == TextExtractionStatus.READY
        key_ready = _key_is_ready(settings, provider)
        if not key_ready:
            st.info("Configure the selected provider's key in .env before live extraction.")
        if not ready:
            st.warning("Text is not ready for extraction. Review the PDF; OCR is not available.")
        st.caption("Analysis is not automatic. Account billing cannot be verified by this app.")
        if st.button(
            "Extract requirements",
            key="spec_extract_requirements",
            type="primary",
            disabled=not (ready and key_ready and allowed and free_confirmed and model)
            or plan_error is not None,
        ):
            st.session_state.pop("_spec_failed_response", None)
            st.session_state.pop("_spec_extraction_error", None)
            previous = store.load_latest_extraction(stored.document_id)
            try:
                with st.spinner("Extracting customer requirements from the saved PDF…"):
                    overrides = {
                        "groq_max_completion_tokens": output_limit,
                        "groq_temperature": 0.0,
                        "gemini_max_output_tokens": output_limit,
                        "scione_single_attempt_mode": True,
                    }
                    adapter = create_configured_provider(
                        settings.model_copy(update=overrides),
                        provider_name=provider,
                        model_name=model,
                    )
                    run = CustomerRequirementRunner(
                        adapter,
                        max_input_characters=settings.scione_customer_input_character_limit,
                    ).run(stored.document, strategy=strategy)
                    store.save_extraction(stored.document_id, run)
            except CustomerExtractionError as exc:
                st.session_state["_spec_extraction_error"] = (
                    stored.document_id,
                    provider,
                    model,
                    str(exc),
                )
                if exc.raw_response is not None:
                    st.session_state["_spec_failed_response"] = (
                        stored.document_id,
                        exc.raw_response,
                    )
                _record_failure(
                    store, stored, provider, model, strategy, output_limit, str(exc), previous
                )
            except (ConfigurationError, SpecStoreError) as exc:
                st.session_state["_spec_extraction_error"] = (
                    stored.document_id,
                    provider,
                    model,
                    str(exc),
                )
                _record_failure(
                    store, stored, provider, model, strategy, output_limit, str(exc), previous
                )
            else:
                st.session_state.pop("_spec_failed_response", None)
                st.session_state["_spec_notice"] = "New analysis saved. Review flagged items below."
                st.rerun()


def _record_failure(
    store: FileSpecStore,
    stored: StoredSpecification,
    provider: str,
    model: str,
    strategy: str,
    output_limit: int,
    message: str,
    previous: CustomerExtractionRun | None,
) -> None:
    try:
        store.save_failure(
            ExtractionFailure(
                document_id=stored.document_id,
                occurred_at=datetime.now(UTC),
                provider=provider,
                model=model,
                strategy=strategy,
                output_limit=output_limit,
                message=message[:4000],
                previous_run_id=previous.run_id if previous else None,
            )
        )
    except SpecStoreError:
        st.warning(
            "Analysis failed, and its diagnostic could not be saved. Existing data is unchanged."
        )


def _render_failure_notice(
    store: FileSpecStore,
    stored: StoredSpecification,
    run: CustomerExtractionRun | None,
) -> None:
    failure = store.load_failure(stored.document_id)
    if failure is None or failure.previous_run_id != (run.run_id if run else None):
        return
    error = failure.message.casefold()
    if "max_tokens" in error or "max_output" in error or "finish_reason=length" in error:
        reason = "The response reached its output limit."
    elif "429" in error or "rate limit" in error:
        reason = "The provider's quota blocked the request."
    else:
        reason = "The new analysis did not finish successfully."
    retained = (
        f" Still showing the previous {len(run.extraction.requirements)} extracted records "
        f"from {run.started_at.strftime('%b %d, %H:%M UTC')}; they are not a new result."
        if run
        else " No analysis result has been saved."
    )
    st.error(reason + retained + " Technical details are in Developer tools.")


def _render_values(label: str, values: list) -> None:
    st.markdown(f"##### {label}")
    if not values:
        st.caption("Not specified")
        return
    for item in values:
        name = "" if item.name in {"Customer condition", "Customer criterion"} else item.name
        value = item.raw_value
        if item.unit and item.unit not in value:
            value += f" {item.unit}"
        title = f'<span class="spec-value-label">{escape(name)}</span>' if name else ""
        context = (
            f'<div class="spec-value-context">{escape(item.context)}</div>' if item.context else ""
        )
        st.markdown(
            f'<div class="spec-value">{title}<div>{escape(value)}</div>{context}</div>',
            unsafe_allow_html=True,
        )


def _save_review(
    store: FileSpecStore,
    document_id: str,
    run_id: str,
    requirement_id: str,
    status: str,
    expected_version: int,
) -> None:
    """Bind an action to the rendered snapshot, not a newly loaded extraction."""

    try:
        latest = store.load_latest_extraction(document_id)
        if latest is None or latest.run_id != run_id:
            raise SpecStoreError(
                "Extraction changed since you viewed it. Review the new result first."
            )
        updated = store.save_review(
            document_id,
            run_id,
            requirement_id,
            decision=status,
            note=st.session_state.get("spec_review_note", ""),
            expected_version=expected_version,
        )
    except SpecStoreError as exc:
        st.session_state["_spec_action_error"] = str(exc)
        return
    st.session_state.pop("_spec_review_context", None)
    st.session_state.pop("_spec_manual_review", None)
    name = next(
        item.original_test_name
        for item in latest.extraction.requirements
        if item.id == requirement_id
    )
    label = {"confirmed": "Confirmed", "excluded": "Excluded", "needs_review": "Reopened"}[status]
    st.session_state["_spec_notice"] = f"{label}: {name}."
    assessments = _assess_review(store, store.load_document(document_id), latest, updated)
    next_id = next(
        (
            key
            for key, item in assessments.items()
            if key != requirement_id and item.state == "needs_attention"
        ),
        None,
    )
    st.session_state["spec_requirement_choice"] = next_id
    if next_id:
        _focus_requirement(store.load_document(document_id), latest)


def _allow_manual_review(requirement_id: str) -> None:
    st.session_state["_spec_manual_review"] = requirement_id


def _back_to_list() -> None:
    st.session_state["spec_requirement_choice"] = None
    st.session_state.pop("_spec_manual_review", None)


def _requirement_list(
    ids: list[str],
    stored: StoredSpecification,
    run: CustomerExtractionRun,
    assessments: dict,
    *,
    show_reason: bool = False,
) -> None:
    by_id = {item.id: item for item in run.extraction.requirements}
    labels = {
        "needs_attention": "Needs review",
        "ready": "Extracted",
        "confirmed": "Confirmed",
        "excluded": "Excluded",
    }
    prefix = "spec_attention" if show_reason else "spec_all"
    with st.container(height=min(260, len(ids) * 48 + 15), border=False, key=prefix + "_list"):
        for identifier in ids:
            row = by_id[identifier]
            suffix = "" if show_reason else f" · {labels[assessments[identifier].state]}"
            st.button(
                row.original_test_name + suffix,
                key=f"{prefix}_open_{identifier}",
                type="tertiary",
                width="stretch",
                icon=":material/chevron_right:",
                on_click=_open_requirement,
                args=(identifier, stored, run),
            )


def _friendly_reason(reasons: tuple[str, ...]) -> str:
    text = " ".join(reasons).casefold()
    if "quot" in text or "evidence" in text or "page" in text:
        return "Check the source text"
    if "method" in text:
        return "Method not stated"
    if "condition" in text or "criteri" in text:
        return "Check missing requirements"
    return "Check interpretation"


def _render_requirement_review(
    store: FileSpecStore,
    stored: StoredSpecification,
    run: CustomerExtractionRun,
    review: RequirementReviewSet,
) -> None:
    requirement_by_id = {item.id: item for item in run.extraction.requirements}
    if not requirement_by_id:
        st.error("Saved extraction has no requirements. Do not treat it as a completed analysis.")
        return
    requirement_id = st.session_state.get("spec_requirement_choice")
    if requirement_id not in requirement_by_id:
        st.caption("Select a test to see its details and highlighted source.")
        return
    requirement = requirement_by_id[requirement_id]
    decision = review.decisions.get(requirement_id)
    context = (stored.document_id, run.run_id, requirement_id)
    if (
        st.session_state.get("_spec_review_context") != context
        or "spec_review_note" not in st.session_state
    ):
        st.session_state["_spec_review_context"] = context
        st.session_state["_spec_review_version"] = review.version
        st.session_state["spec_review_note"] = decision.note if decision else ""
    assessment = _assess_review(store, stored, run, review)[requirement_id]
    st.markdown('<div class="spec-detail-divider"></div>', unsafe_allow_html=True)
    title, relocate = st.columns([4, 1], vertical_alignment="center")
    with title:
        st.markdown(f"<h4>{escape(requirement.original_test_name)}</h4>", unsafe_allow_html=True)
    with relocate:
        st.button(
            "Relocate",
            type="tertiary",
            key="spec_relocate",
            icon=":material/my_location:",
            on_click=_focus_requirement,
            args=(stored, run),
        )
    if requirement.requested_methods:
        st.caption(
            " · ".join(
                f"{item.code}{' (' + item.revision + ')' if item.revision else ''}"
                f"{' — ' + item.role if item.role else ''}"
                for item in requirement.requested_methods
            )
        )
    if assessment.state == "needs_attention":
        st.caption("Review needed: " + _friendly_reason(assessment.reasons).lower() + ".")
    _render_values("Conditions", requirement.conditions)
    _render_values("Acceptance criteria", requirement.acceptance_criteria)
    for label, items in [
        ("Applicability", requirement.applicability),
        ("Special instructions / alternatives", requirement.special_instructions),
    ]:
        if items:
            st.markdown(f"**{label}**")
            for item in items:
                st.text(item)
    pages = sorted(
        {item.page for item in requirement.evidence if 1 <= item.page <= stored.document.page_count}
    )
    if len(pages) > 1:
        evidence_context = (stored.document_id, run.run_id, requirement_id)
        if st.session_state.get("_spec_evidence_context") != evidence_context:
            st.session_state["_spec_evidence_context"] = evidence_context
            current_page = st.session_state.get("spec_pdf_page")
            st.session_state["spec_evidence_page"] = (
                current_page if current_page in pages else pages[0]
            )
        st.selectbox("Source page", pages, key="spec_evidence_page", on_change=_go_to_evidence_page)
    with st.expander("Source quotation & optional note"):
        for evidence in requirement.evidence:
            st.caption(f"Page {evidence.page} · section {evidence.section or 'not stated'}")
            st.text(evidence.quote)
        st.text_area("Review note / corrections to resolve", key="spec_review_note", max_chars=4000)
        for reason in assessment.reasons:
            st.caption(reason)
    if st.session_state["_spec_review_version"] != review.version:
        st.warning("Review changed since this draft loaded. Reload before saving.")
        if st.button("Reload saved review (discard note draft)"):
            st.session_state.pop("_spec_review_context", None)
            st.rerun()
    manual_review = st.session_state.get("_spec_manual_review") == requirement_id
    if assessment.state != "needs_attention" and not manual_review:
        label = {
            "ready": "Extracted — no automatic checks flagged this item.",
            "confirmed": "Confirmed by you.",
            "excluded": "Excluded from this request.",
        }
        st.caption(label[assessment.state])
        st.button(
            "Review this item",
            key="spec_review_item",
            type="tertiary",
            on_click=_allow_manual_review,
            args=(requirement_id,),
        )
        return
    confirm, flag = st.columns([1, 1])
    with confirm:
        st.button(
            "Confirm",
            key="spec_confirm_requirement",
            type="primary",
            icon=":material/check:",
            width="content",
            on_click=_save_review,
            args=(
                store,
                stored.document_id,
                run.run_id,
                requirement.id,
                "confirmed",
                st.session_state["_spec_review_version"],
            ),
        )
    with flag:
        st.button(
            "Exclude this item",
            key="spec_exclude_requirement",
            width="content",
            on_click=_save_review,
            args=(
                store,
                stored.document_id,
                run.run_id,
                requirement.id,
                "excluded",
                st.session_state["_spec_review_version"],
            ),
        )


def _go_to_evidence_page() -> None:
    st.session_state["spec_pdf_page"] = st.session_state["spec_evidence_page"]


def _render_review_panel(
    store: FileSpecStore, stored: StoredSpecification, run: CustomerExtractionRun | None
) -> None:
    st.subheader("Your test requirements")
    if run is None:
        st.info("Your PDF is saved. Choose Analyze PDF to find its test requirements.")
        return
    review = store.load_review(stored.document_id, run.run_id)
    assessments = _assess_review(store, stored, run, review)
    attention = [key for key, item in assessments.items() if item.state == "needs_attention"]
    st.caption(f"{len(assessments)} extracted · {len(attention)} need your review")
    coverage = coverage_issues(run.extraction, stored.document)
    if coverage:
        st.warning("Draft: some tests may still be missing.")
    selected = st.session_state.get("spec_requirement_choice")
    if selected:
        st.button(
            "Back to list",
            key="spec_back_to_list",
            type="tertiary",
            icon=":material/arrow_back:",
            on_click=_back_to_list,
        )
        _render_requirement_review(store, stored, run, review)
    elif attention:
        st.markdown(f"##### Needs your review · {len(attention)}")
        st.caption("Select an item to check it against the document.")
        _requirement_list(
            attention,
            stored,
            run,
            assessments,
            show_reason=True,
        )
    else:
        st.caption("No open item reviews. You can still inspect or review any item below.")
    with st.expander(
        f"All extracted tests · {len(assessments)}", expanded=not attention and not selected
    ):
        _requirement_list(
            list(assessments),
            stored,
            run,
            assessments,
        )
        st.caption("Extracted ≠ human-confirmed. Only flagged items request your review.")
    if not selected:
        st.caption("Select a test to see its details and highlighted source.")


def render_specification_debug(settings: Settings) -> None:
    """Keep diagnostics and immutable responses out of the lab's review journey."""

    st.subheader("Customer-spec diagnostics")
    store = FileSpecStore(settings.scione_workspace_dir)
    try:
        summaries = store.list_documents()
        if not summaries:
            st.info("No customer documents saved.")
            return
        by_id = {item.document_id: item for item in summaries}
        document_id = st.selectbox(
            "Document",
            list(by_id),
            key="spec_debug_document",
            format_func=lambda value: by_id[value].original_name,
        )
        stored = store.load_document(document_id)
        run = store.load_latest_extraction(document_id)
        failure = store.load_failure(document_id)
        if failure:
            st.markdown("**Last failed attempt (not a saved result)**")
            st.json(failure.model_dump(mode="json"))
        if run:
            st.caption(
                f"Saved {run.started_at.isoformat()} · {run.provider}/{run.model} "
                f"· {run.prompt_version} · {run.strategy}"
            )
            with st.expander("Run metadata"):
                st.json(
                    run.model_dump(mode="json", exclude={"raw_response", "extraction", "calls"})
                )
            issues = list(
                dict.fromkeys(
                    [
                        *coverage_issues(run.extraction, stored.document),
                        *run.extraction.review_issues,
                    ]
                )
            )
            for issue in issues:
                st.text(issue)
            st.markdown("**Validated / expanded extraction**")
            st.json(run.extraction.model_dump(mode="json"), expanded=False)
            st.markdown(f"**Saved response · {run.raw_response_kind}**")
            st.text(run.raw_response)
            for call in run.calls:
                with st.expander(f"Request {call.call_index} · focus pages {call.focus_pages}"):
                    st.text(call.raw_response)
        rejected = st.session_state.get("_spec_failed_response")
        if rejected and rejected[0] == document_id:
            with st.expander("Rejected response — not accepted as an extraction"):
                st.text(rejected[1])
        page = st.number_input(
            "Text page",
            min_value=1,
            max_value=stored.document.page_count,
            key="spec_debug_text_page",
        )
        st.text(stored.document.pages[page - 1].text)
        st.download_button(
            "Download original PDF",
            store.read_pdf(document_id),
            file_name=stored.original_name,
            mime="application/pdf",
        )
    except SpecStoreError as exc:
        st.error(str(exc))


def render_specifications(settings: Settings) -> None:
    st.title("Specifications")
    st.caption("Upload a specification, then review only the items that need your attention.")
    store = FileSpecStore(settings.scione_workspace_dir)
    try:
        notice = st.session_state.pop("_spec_notice", None)
        if notice:
            st.success(notice)
        action_error = st.session_state.pop("_spec_action_error", None)
        if action_error:
            st.error(action_error)
        documents = store.list_documents()
        if not documents:
            _render_intake(store)
            st.info("No specifications saved yet. Upload a PDF or import the supplied demo sample.")
            return
        by_id = {item.document_id: item for item in documents}
        pending = st.session_state.pop("_spec_open_after_import", None)
        if pending in by_id:
            st.session_state["spec_document_choice"] = pending
        file_column, add_column = st.columns([5, 1], vertical_alignment="bottom")
        with file_column:
            document_id = st.selectbox(
                "Saved specification",
                list(by_id),
                key="spec_document_choice",
                format_func=lambda value: by_id[value].original_name,
            )
        with add_column:
            _render_intake(store, compact=True)
        stored = store.load_document(document_id)
        for warning in stored.document.warnings:
            st.warning(warning)
        pdf = store.read_pdf(document_id)
        run = store.load_latest_extraction(document_id)
        _prepare_selection(stored, run)
        _render_extraction_controls(settings, store, stored, has_result=run is not None)
        _render_failure_notice(store, stored, run)
        left, right = st.columns([1.15, 1], gap="medium")
        with left:
            with st.container(height=950, border=True, key="spec_pdf_panel"):
                _render_pdf(stored, pdf, _selected_requirement(run))
        with right:
            with st.container(height=950, border=True, key="spec_review_panel"):
                _render_review_panel(store, stored, run)
    except SpecStoreError as exc:
        st.error(str(exc))
        st.caption(
            "Saved data has not been reset or replaced. Check local storage before retrying."
        )

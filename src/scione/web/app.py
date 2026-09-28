"""Interactive review workbench for local extraction experiments."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from scione.config import ConfigurationError, Settings
from scione.evaluation import EvaluationReport, TestMethodEvaluator
from scione.extraction import ExtractionError
from scione.ingestion import IngestionError
from scione.runs import FileRunStore, RunStoreError, StoredMethodRun
from scione.schemas import TestMethodDefinition, TestMethodExtraction, TextReadingOrder
from scione.web.presentation import (
    comparison_evaluation_rows,
    comparison_summary_rows,
    evaluation_detail_rows,
    evaluation_rows,
    evaluation_summary,
    evidence_rows,
)
from scione.workbench import create_configured_provider, execute_method_workbench

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
SAMPLE_GROUND_TRUTH = (
    REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"
)
PROVIDER_LABELS = {
    "groq": "GroqCloud",
    "gemini": "Google Gemini",
}


def _apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root { color-scheme: light; }
        .stApp {
            color: #102a2a;
            background: linear-gradient(180deg, #f7faf9 0%, #eef4f1 100%);
        }
        .stMain h1, .stMain h2, .stMain h3, .stMain h4,
        .stMain p, .stMain label,
        .stMain [data-testid="stMetricLabel"],
        .stMain [data-testid="stMetricValue"],
        .stMain button[data-baseweb="tab"] {
            color: #102a2a !important;
        }
        [data-testid="stSidebar"] { background: #102a2a; }
        [data-testid="stSidebar"] * { color: #f4fbf7; }
        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea { color: #102a2a !important; }
        [data-testid="stSidebar"] [data-baseweb="select"] * {
            color: #f4fbf7 !important;
        }
        [data-testid="stSidebar"] code {
            color: #102a2a !important;
            background: #eef4f1 !important;
        }
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] *,
        [data-testid="stSidebar"] [data-testid="stFileUploaderFile"] *,
        [data-testid="stSidebar"] [data-testid="stFileUploader"] section *,
        [data-testid="stSidebar"] [data-testid="stFileUploader"] button * {
            color: #102a2a !important;
        }
        .hero {
            padding: 1.6rem 1.8rem;
            border: 1px solid #cfe0d7;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.88);
            box-shadow: 0 12px 35px rgba(16, 42, 42, 0.07);
            margin-bottom: 1rem;
        }
        .eyebrow { color: #147d64; font-size: .78rem; font-weight: 700; letter-spacing: .12em; }
        .hero h1 { color: #102a2a; font-size: 2.1rem; margin: .35rem 0 .45rem; }
        .hero p { color: #526761; margin: 0; max-width: 760px; }
        .run-meta { color: #61736d; font-size: .85rem; }
        div[data-testid="stMetric"] {
            background: rgba(255,255,255,.82);
            border: 1px solid #d8e5df;
            padding: .8rem 1rem;
            border-radius: 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _persist_uploaded_file(directory: Path, uploaded_file: object, fallback: str) -> Path:
    original_name = getattr(uploaded_file, "name", fallback)
    safe_name = Path(original_name).name or fallback
    destination = directory / safe_name
    destination.write_bytes(uploaded_file.getvalue())
    return destination


def _provider_key_is_configured(settings: Settings, provider_name: str) -> bool:
    try:
        if provider_name == "groq":
            settings.require_groq_api_key()
        elif provider_name == "gemini":
            settings.require_gemini_api_key()
        else:
            return False
    except ConfigurationError:
        return False
    return True


def _provider_model(settings: Settings, provider_name: str) -> str:
    if provider_name == "groq":
        return settings.groq_model
    if provider_name == "gemini":
        return settings.gemini_model
    raise ConfigurationError(f"Unsupported provider: {provider_name}")


def _run_controls(settings: Settings, store: FileRunStore) -> str | None:
    st.sidebar.markdown("## New run settings")
    st.sidebar.caption(
        "These settings apply only to the next run. A model call happens only "
        "when you press the button."
    )
    source = st.sidebar.radio(
        "PDF source",
        ["Synthetic sample", "Upload PDF"],
        horizontal=True,
    )
    uploaded_pdf = None
    if source == "Upload PDF":
        uploaded_pdf = st.sidebar.file_uploader("Test document", type=["pdf"])

    reading_order_value = st.sidebar.selectbox(
        "PDF text order",
        [order.value for order in TextReadingOrder],
        index=0,
        help="Content-stream works best for the current two-column synthetic sample.",
    )
    uploaded_truth = None
    if source == "Synthetic sample":
        use_sample_truth = st.sidebar.checkbox(
            "Evaluate with provisional sample truth",
            value=True,
        )
    else:
        use_sample_truth = False
        uploaded_truth = st.sidebar.file_uploader(
            "Optional ground-truth JSON",
            type=["json"],
        )

    provider_names = list(PROVIDER_LABELS)
    default_provider = settings.model_provider.strip().casefold()
    default_provider_index = (
        provider_names.index(default_provider) if default_provider in provider_names else 0
    )
    selected_provider = st.sidebar.selectbox(
        "Provider for next run",
        provider_names,
        index=default_provider_index,
        format_func=lambda name: PROVIDER_LABELS[name],
    )
    selected_model = st.sidebar.selectbox(
        "Model for next run",
        [_provider_model(settings, selected_provider)],
    )
    key_ready = _provider_key_is_configured(settings, selected_provider)
    st.sidebar.markdown(
        f"**Next run provider:** `{selected_provider}`  \n"
        f"**Next run model:** `{selected_model}`  \n"
        f"**API key:** {'configured' if key_ready else 'missing'}"
    )

    free_tier_confirmed = True
    if selected_provider == "gemini":
        st.sidebar.caption(
            "Gemini comparison uses saved runs and makes no extra request. "
            "The app cannot inspect Google billing status."
        )
        st.sidebar.warning(
            "Free-tier Gemini is for synthetic or otherwise non-sensitive documents only."
        )
        free_tier_confirmed = st.sidebar.checkbox(
            "I confirmed this Google project is on the Free tier",
            value=False,
        )
    if settings.scione_single_attempt_mode:
        st.sidebar.success("Single-attempt guard enabled: automatic API retries are off.")
    if not key_ready:
        st.sidebar.warning(f"Configure the {selected_provider} API key before running.")

    if st.sidebar.button(
        "Run extraction",
        type="primary",
        width="stretch",
        disabled=not key_ready or not free_tier_confirmed,
    ):
        if source == "Upload PDF" and uploaded_pdf is None:
            st.sidebar.warning("Choose a PDF before running extraction.")
        else:
            try:
                with st.spinner("Reading the document and extracting structured fields…"):
                    with tempfile.TemporaryDirectory(prefix="scione-workbench-") as temp_name:
                        temp_directory = Path(temp_name)
                        pdf_path = (
                            SAMPLE_PDF
                            if source == "Synthetic sample"
                            else _persist_uploaded_file(
                                temp_directory,
                                uploaded_pdf,
                                "uploaded.pdf",
                            )
                        )
                        ground_truth_path: Path | None = None
                        if use_sample_truth:
                            ground_truth_path = SAMPLE_GROUND_TRUTH
                        elif uploaded_truth is not None:
                            ground_truth_path = _persist_uploaded_file(
                                temp_directory,
                                uploaded_truth,
                                "ground_truth.json",
                            )
                        result = execute_method_workbench(
                            pdf_path,
                            provider=create_configured_provider(
                                settings,
                                provider_name=selected_provider,
                                model_name=selected_model,
                            ),
                            runs_dir=settings.scione_runs_dir,
                            reading_order=TextReadingOrder(reading_order_value),
                            ground_truth_path=ground_truth_path,
                        )
                st.session_state["active_run_id"] = result.run.run_id
                st.rerun()
            except (
                ConfigurationError,
                ExtractionError,
                IngestionError,
                OSError,
                RunStoreError,
                ValidationError,
            ) as exc:
                st.sidebar.error(str(exc))

    st.sidebar.divider()
    st.sidebar.markdown("## Review saved run")
    run_ids = store.list_method_run_ids()
    if not run_ids:
        st.sidebar.info("No saved runs yet.")
        return None

    preferred = st.session_state.get("active_run_id")
    selected_index = run_ids.index(preferred) if preferred in run_ids else 0
    selected = st.sidebar.selectbox(
        "Review run",
        run_ids,
        index=selected_index,
        format_func=lambda run_id: f"{run_id[:8]}…",
    )
    st.session_state["active_run_id"] = selected
    return selected


def _render_method(method: TestMethodDefinition) -> None:
    with st.expander(f"Method {method.method_id} · {method.name}", expanded=True):
        if method.applicability:
            st.caption(method.applicability)

        st.markdown("**Exposures and conditions**")
        exposure_rows = []
        for exposure in method.exposures:
            if exposure.conditions:
                for condition in exposure.conditions:
                    exposure_rows.append(
                        {
                            "exposure": exposure.name,
                            "cycles": exposure.cycle_count,
                            "condition": condition.name,
                            "value": condition.raw_value,
                            "unit": condition.unit,
                        }
                    )
            else:
                exposure_rows.append(
                    {
                        "exposure": exposure.name,
                        "cycles": exposure.cycle_count,
                        "condition": "—",
                        "value": "—",
                        "unit": "—",
                    }
                )
        st.dataframe(exposure_rows, width="stretch", hide_index=True)

        left, right = st.columns(2)
        with left:
            st.markdown("**Parameters**")
            st.dataframe(
                [
                    {
                        "name": parameter.name,
                        "unit": parameter.unit,
                        "description": parameter.description,
                    }
                    for parameter in method.parameters
                ],
                width="stretch",
                hide_index=True,
            )
        with right:
            st.markdown("**Classifications**")
            st.dataframe(
                [
                    {"label": level.label, "description": level.description}
                    for level in method.classifications
                ],
                width="stretch",
                hide_index=True,
            )


def _load_comparable_runs(
    store: FileRunStore,
    selected: StoredMethodRun,
) -> list[StoredMethodRun]:
    comparable: list[StoredMethodRun] = []
    for run_id in store.list_method_run_ids():
        if run_id == selected.run.run_id:
            continue
        candidate = store.load_method_run(run_id)
        if (
            candidate.document.sha256 == selected.document.sha256
            and candidate.document.text_reading_order == selected.document.text_reading_order
            and candidate.run.strategy == selected.run.strategy
            and candidate.run.prompt_version == selected.run.prompt_version
            and candidate.run.schema_version == selected.run.schema_version
        ):
            comparable.append(candidate)
    return comparable


def _ground_truth_for_display(stored: StoredMethodRun) -> TestMethodExtraction | None:
    """Load the persisted truth, with a compatibility fallback for old sample runs."""

    if stored.ground_truth is not None:
        return stored.ground_truth
    if not SAMPLE_PDF.is_file() or not SAMPLE_GROUND_TRUTH.is_file():
        return None
    sample_sha256 = hashlib.sha256(SAMPLE_PDF.read_bytes()).hexdigest()
    if stored.document.sha256 != sample_sha256:
        return None
    return TestMethodExtraction.model_validate_json(
        SAMPLE_GROUND_TRUTH.read_text(encoding="utf-8")
    )


def _evaluation_for_display(
    stored: StoredMethodRun,
) -> tuple[EvaluationReport | None, TestMethodExtraction | None]:
    ground_truth = _ground_truth_for_display(stored)
    if ground_truth is None:
        return stored.evaluation, None
    return (
        TestMethodEvaluator().evaluate(
            stored.run.extraction,
            ground_truth,
            source_document=stored.document,
        ),
        ground_truth,
    )


def _render_comparison(selected: StoredMethodRun, store: FileRunStore) -> None:
    st.subheader("Saved-run comparison")
    st.caption(
        "This view reads existing run artifacts only. It makes no model call. "
        "Candidates are restricted to the same PDF, reading order, prompt, schema, and strategy."
    )
    comparable = _load_comparable_runs(store, selected)
    if not comparable:
        st.info(
            "Run this same document with another provider or model to unlock a fair comparison."
        )
        return

    comparison_by_id = {stored.run.run_id: stored for stored in comparable}
    comparison_id = st.selectbox(
        "Compare selected run with",
        list(comparison_by_id),
        format_func=lambda run_id: (
            f"{comparison_by_id[run_id].run.provider} / "
            f"{comparison_by_id[run_id].run.model} · {run_id[:8]}…"
        ),
    )
    comparison = comparison_by_id[comparison_id]
    selected_evaluation, _ = _evaluation_for_display(selected)
    comparison_evaluation, _ = _evaluation_for_display(comparison)
    reports = {
        run_id: report
        for run_id, report in (
            (selected.run.run_id, selected_evaluation),
            (comparison.run.run_id, comparison_evaluation),
        )
        if report is not None
    }
    st.dataframe(
        comparison_summary_rows([selected, comparison], reports=reports),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Mean field F1 is an unweighted diagnostic summary; use the dimensions below "
        "to identify exactly where each model succeeds or fails."
    )

    if selected_evaluation is None or comparison_evaluation is None:
        st.info("Both runs need the same ground truth before field-level scores can be compared.")
        return

    st.markdown("**Field-level score comparison**")
    st.dataframe(
        comparison_evaluation_rows(selected_evaluation, comparison_evaluation),
        width="stretch",
        hide_index=True,
    )
    left, right = st.columns(2)
    with left:
        st.markdown(
            f"**Selected: {selected.run.provider} / {selected.run.model}**"
        )
        st.caption(f"Run {selected.run.run_id}")
    with right:
        st.markdown(
            f"**Comparison: {comparison.run.provider} / {comparison.run.model}**"
        )
        st.caption(f"Run {comparison.run.run_id}")


def _render_extraction(stored: StoredMethodRun, store: FileRunStore) -> None:
    extraction = stored.run.extraction
    document = stored.document
    run = stored.run
    evaluation, ground_truth = _evaluation_for_display(stored)

    st.markdown(
        """
        <div class="hero">
          <div class="eyebrow">SCIONE AI · EXTRACTION WORKBENCH</div>
          <h1>Test Specification Intelligence</h1>
          <p>Inspect exactly what the model read, what it extracted, where each fact
          came from, and how the result compares with a review fixture.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    summary = evaluation_summary(evaluation) if evaluation is not None else None
    metric_columns = st.columns(6)
    metric_columns[0].metric("PDF pages", document.page_count)
    metric_columns[1].metric("Methods", len(extraction.methods))
    metric_columns[2].metric("Evidence", len(evidence_rows(extraction)))
    metric_columns[3].metric(
        "Diagnostic F1",
        f"{summary['mean_f1']:.3f}" if summary and summary["mean_f1"] is not None else "—",
    )
    metric_columns[4].metric("Latency", f"{run.latency_ms / 1000:.2f}s")
    metric_columns[5].metric("Tokens", run.usage.total_tokens or "—")
    st.markdown(
        f'<div class="run-meta">Reviewing saved run {run.run_id} · '
        f"{run.provider} / {run.model} · "
        f"{run.prompt_version} · {document.text_reading_order.value}</div>",
        unsafe_allow_html=True,
    )

    if extraction.warnings:
        st.warning("\n\n".join(extraction.warnings))

    overview_tab, evidence_tab, evaluation_tab, comparison_tab, json_tab = st.tabs(
        [
            "Model output",
            "Evidence & source",
            "Results vs truth",
            "Compare runs",
            "Raw JSON & metadata",
        ]
    )

    with overview_tab:
        identity, test_items = st.columns([1, 1])
        with identity:
            st.subheader("Document identity")
            st.json(extraction.document.model_dump(mode="json"), expanded=False)
        with test_items:
            st.subheader("Test items")
            if extraction.test_items:
                st.dataframe(
                    [
                        {
                            "source": item.source_name,
                            "normalized": item.normalized_name,
                            "description": item.description,
                        }
                        for item in extraction.test_items
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No test items were extracted.")

        st.subheader("Methods")
        if extraction.methods:
            for method in extraction.methods:
                _render_method(method)
        else:
            st.info("No methods were extracted.")

        lower_left, lower_right = st.columns(2)
        with lower_left:
            st.subheader("Referenced documents")
            st.dataframe(
                [
                    {"code": reference.code, "title": reference.title}
                    for reference in extraction.referenced_documents
                ],
                width="stretch",
                hide_index=True,
            )
        with lower_right:
            st.subheader("Acceptance criteria")
            if extraction.acceptance_criteria:
                st.dataframe(
                    [
                        criterion.model_dump(mode="json")
                        for criterion in extraction.acceptance_criteria
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.success("No unsupported customer pass/fail criteria were inferred.")

    with evidence_tab:
        rows = evidence_rows(extraction)
        st.subheader("Evidence index")
        st.dataframe(rows, width="stretch", hide_index=True)
        page_number = st.selectbox(
            "Source page",
            [page.page_number for page in document.pages],
            format_func=lambda page: f"Physical PDF page {page}",
        )
        selected_page = next(page for page in document.pages if page.page_number == page_number)
        page_evidence = [row for row in rows if row["page"] == page_number]
        st.caption(f"{len(page_evidence)} extracted evidence references point to this page.")
        st.text_area(
            "Extracted PDF text seen by the model",
            selected_page.text,
            height=520,
            disabled=True,
        )

    with evaluation_tab:
        if evaluation is None:
            st.info("This run has no ground truth, so no evaluation was computed.")
        else:
            st.subheader("Quality at a glance")
            st.caption(
                "This is a deterministic diagnostic against the review fixture, not a "
                "claim of production accuracy. Each dimension remains visible so strong "
                "classification extraction cannot hide missing procedure details. "
                f"Evaluator version: {evaluation.evaluator_version}."
            )
            summary = evaluation_summary(evaluation)
            summary_columns = st.columns(4)
            summary_columns[0].metric(
                "Mean field F1",
                f"{summary['mean_f1']:.3f}" if summary["mean_f1"] is not None else "—",
            )
            summary_columns[1].metric(
                "Truth values found",
                f"{summary['matched']}/{summary['expected']}",
            )
            summary_columns[2].metric("Unexpected values", summary["unexpected"])
            summary_columns[3].metric(
                "Evidence grounded",
                (
                    f"{summary['evidence_grounded']}/{summary['evidence_total']}"
                    if summary["evidence_total"] is not None
                    else "—"
                ),
            )

            st.markdown("**Dimension-by-dimension results**")
            st.dataframe(
                evaluation_rows(evaluation),
                width="stretch",
                hide_index=True,
            )

            metric_by_name = {metric.name: metric for metric in evaluation.metrics}
            selected_metric_name = st.selectbox(
                "Inspect one dimension",
                list(metric_by_name),
                format_func=lambda name: name.replace("_", " ").title(),
                key=f"evaluation-dimension-{run.run_id}",
            )
            selected_metric = metric_by_name[selected_metric_name]
            st.caption(
                "Values below are normalized only for formatting. Missing and unexpected "
                "values remain separate instead of being paired by a subjective similarity score."
            )
            st.dataframe(
                evaluation_detail_rows(selected_metric),
                width="stretch",
                hide_index=True,
            )

            grounding = evaluation.evidence_grounding
            if grounding and grounding.ungrounded:
                with st.expander(
                    f"Evidence requiring review · {len(grounding.ungrounded)}"
                ):
                    st.code("\n\n".join(grounding.ungrounded))

            if ground_truth is not None:
                with st.expander("Raw model output and ground truth"):
                    prediction_column, truth_column = st.columns(2)
                    with prediction_column:
                        st.markdown("**Model output**")
                        st.json(extraction.model_dump(mode="json"), expanded=False)
                    with truth_column:
                        st.markdown("**Ground truth**")
                        st.json(ground_truth.model_dump(mode="json"), expanded=False)

    with comparison_tab:
        _render_comparison(stored, store)

    with json_tab:
        st.subheader("Validated prediction")
        prediction_json = extraction.model_dump_json(indent=2)
        st.download_button(
            "Download prediction JSON",
            prediction_json,
            file_name=f"{run.run_id}-prediction.json",
            mime="application/json",
        )
        st.json(extraction.model_dump(mode="json"), expanded=False)
        with st.expander("Run metadata"):
            st.json(run.model_dump(mode="json", exclude={"raw_response", "extraction"}))


def main() -> None:
    st.set_page_config(
        page_title="SciOne Extraction Workbench",
        page_icon="🧪",
        layout="wide",
    )
    _apply_theme()
    settings = Settings()
    store = FileRunStore(settings.scione_runs_dir)
    try:
        selected_run_id = _run_controls(settings, store)
        if selected_run_id is None:
            st.title("Test Specification Intelligence")
            st.info("Run the synthetic sample to create the first reviewable extraction.")
            return
        stored = store.load_method_run(selected_run_id)
    except RunStoreError as exc:
        st.error(str(exc))
        return
    _render_extraction(stored, store)


if __name__ == "__main__":
    main()

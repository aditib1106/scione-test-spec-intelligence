"""Interactive review workbench for local extraction experiments."""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from scione.config import ConfigurationError, Settings
from scione.extraction import ExtractionError
from scione.ingestion import IngestionError
from scione.runs import FileRunStore, RunStoreError, StoredMethodRun
from scione.schemas import TestMethodDefinition, TextReadingOrder
from scione.web.presentation import evaluation_rows, evidence_rows
from scione.workbench import create_configured_provider, execute_method_workbench

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
SAMPLE_GROUND_TRUTH = (
    REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"
)


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


def _run_controls(settings: Settings, store: FileRunStore) -> str | None:
    st.sidebar.markdown("## Run control")
    st.sidebar.caption("A model call happens only when you press the button.")
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

    key_ready = settings.groq_api_key is not None
    st.sidebar.markdown(
        f"**Provider:** `{settings.model_provider}`  \n"
        f"**Model:** `{settings.groq_model}`  \n"
        f"**API key:** {'configured' if key_ready else 'missing'}"
    )

    if st.sidebar.button("Run extraction", type="primary", width="stretch"):
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
                            provider=create_configured_provider(settings),
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
    st.sidebar.markdown("## Saved runs")
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


def _render_extraction(stored: StoredMethodRun) -> None:
    extraction = stored.run.extraction
    document = stored.document
    run = stored.run

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

    metric_columns = st.columns(5)
    metric_columns[0].metric("PDF pages", document.page_count)
    metric_columns[1].metric("Methods", len(extraction.methods))
    metric_columns[2].metric("Evidence", len(evidence_rows(extraction)))
    metric_columns[3].metric("Latency", f"{run.latency_ms / 1000:.2f}s")
    metric_columns[4].metric("Tokens", run.usage.total_tokens or "—")
    st.markdown(
        f'<div class="run-meta">Run {run.run_id} · {run.provider} / {run.model} · '
        f"{run.prompt_version} · {document.text_reading_order.value}</div>",
        unsafe_allow_html=True,
    )

    if extraction.warnings:
        st.warning("\n\n".join(extraction.warnings))

    overview_tab, evidence_tab, evaluation_tab, json_tab = st.tabs(
        ["Extraction review", "Evidence & source", "Evaluation", "JSON & metadata"]
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
        if stored.evaluation is None:
            st.info("This run has no ground truth, so no evaluation was computed.")
        else:
            st.subheader("Field-level metrics")
            st.caption(
                "The fixture is provisional. Metrics remain separate so one strong "
                "field cannot hide another weak field."
            )
            st.dataframe(
                evaluation_rows(stored.evaluation),
                width="stretch",
                hide_index=True,
            )
            for metric in stored.evaluation.metrics:
                if metric.missing or metric.unexpected:
                    with st.expander(
                        f"{metric.name.replace('_', ' ').title()} · review differences"
                    ):
                        left, right = st.columns(2)
                        left.markdown("**Missing**")
                        left.code("\n".join(metric.missing) or "None")
                        right.markdown("**Unexpected**")
                        right.code("\n".join(metric.unexpected) or "None")

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
    _render_extraction(stored)


if __name__ == "__main__":
    main()

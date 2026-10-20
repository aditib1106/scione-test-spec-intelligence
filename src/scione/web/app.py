"""Lab workspace and developer diagnostics for customer specifications."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from scione.catalogs import CatalogError, FileLabStore, LabStoreError, load_reference_catalog
from scione.config import Settings
from scione.web.developer import render_developer_diagnostics
from scione.web.portal import (
    render_my_lab,
    render_test_catalog,
    render_workspace_navigation,
)
from scione.web.reports import render_reports
from scione.web.specifications import render_specifications
from scione.workspace_reset import DemoResetError, reset_demo_workspace


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
        .st-key-workspace_navigation button {
            justify-content: flex-start;
            text-align: left;
            border: 1px solid transparent;
            background: transparent;
            min-height: 2.7rem;
            border-radius: 8px;
        }
        .st-key-workspace_navigation button:hover {
            background: #1b3b35;
            border-color: #31564c;
        }
        .st-key-workspace_navigation button[kind="primary"] {
            background: #245446;
            border-color: #42765f;
        }
        .st-key-workspace_navigation button p { color: #f4fbf7 !important; }
        .st-key-workspace_navigation [data-testid="stExpander"] {
            background: #102a2a !important;
            border-color: #31564c !important;
        }
        .st-key-workspace_navigation [data-testid="stExpander"] summary,
        .st-key-workspace_navigation [data-testid="stExpander"] summary:hover,
        .st-key-workspace_navigation [data-testid="stExpander"] summary:focus,
        .st-key-workspace_navigation [data-testid="stExpander"] summary:focus-visible,
        .st-key-workspace_navigation [data-testid="stExpander"] summary:active,
        .st-key-workspace_navigation [data-testid="stExpander"] details[open] > summary {
            color: #f4fbf7 !important;
            background: #17362f !important;
        }
        .st-key-workspace_navigation [data-testid="stExpander"] summary * {
            color: #f4fbf7 !important;
        }
        .st-key-workspace_navigation [data-testid="stExpanderDetails"] {
            background: #102a2a !important;
        }
        .stMain button[kind="primary"] {
            background: #147d64;
            border-color: #147d64;
        }
        .stMain button[kind="primary"] p,
        .stMain button[kind="primary"] span { color: #ffffff !important; }
        .stMain button:disabled {
            background: #e4ebe7 !important;
            border-color: #d0dbd5 !important;
            color: #677970 !important;
            opacity: 1;
            cursor: not-allowed;
        }
        .stMain button:disabled p,
        .stMain button:disabled span { color: #677970 !important; }
        .st-key-spec_pdf_panel, .st-key-spec_review_panel {
            background: #ffffff;
            border-radius: 12px;
        }
        .spec-value { margin: .3rem 0 .65rem; font-size: .95rem; }
        .spec-value-label { color: #64776f; font-size: .8rem; }
        .spec-value-context { color: #64776f; font-size: .85rem; margin-top: .15rem; }
        .spec-detail-divider { border-top: 1px solid #d8e5df; margin: .25rem 0; }
        .st-key-spec_review_panel [data-testid="stElementToolbar"] { display: none; }
        .st-key-spec_attention_list button, .st-key-spec_all_list button {
            justify-content: flex-start;
            text-align: left;
            border-radius: 0;
            border-bottom: 1px solid #edf1ee;
            padding: .3rem .15rem;
            min-height: 2.25rem;
        }
        .st-key-spec_attention_list button [data-testid="stMarkdownContainer"],
        .st-key-spec_all_list button [data-testid="stMarkdownContainer"] {
            text-align: left;
            flex-grow: 1;
        }
        .st-key-spec_attention_list button > div,
        .st-key-spec_all_list button > div,
        .st-key-spec_attention_list button [data-has-shortcut],
        .st-key-spec_all_list button [data-has-shortcut] {
            width: 100%;
            justify-content: flex-start !important;
        }
        .st-key-spec_pdf_panel [data-testid="stVerticalBlockBorderWrapper"],
        .st-key-spec_review_panel [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: #d8e5df;
        }
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


def _render_developer_tools(settings: Settings) -> None:
    st.title("Developer Tools")
    st.caption(
        "Customer-spec extraction diagnostics. Saved outputs, source checks and failures "
        "are inspected locally; opening this page does not call a model."
    )
    render_developer_diagnostics(settings)
    with st.expander("Reset demo workspace"):
        st.caption(
            "Start a first-use walkthrough. Saved lab entries, uploads, analyses, reviews and "
            "reports are moved into a private recovery backup. API keys and source files stay. "
            "Finish any running analysis before resetting."
        )
        acknowledged = st.checkbox(
            "I want to back up and clear this local demo", key="demo_reset_acknowledge"
        )
        if (
            st.button("Reset to first use", key="demo_reset_confirm", disabled=not acknowledged)
            and acknowledged
        ):
            try:
                result = reset_demo_workspace(settings.scione_workspace_dir)
            except DemoResetError as exc:
                st.error(str(exc))
            else:
                st.cache_data.clear()
                st.cache_resource.clear()
                st.session_state.clear()
                st.session_state["workspace_page"] = "My Lab"
                st.session_state["_workspace_notice"] = (
                    f"Demo reset. Recovery backup: {result.backup_path}"
                    if result.backup_path
                    else "Demo reset. No saved data needed moving."
                )
                st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="SciOne Lab Workspace",
        page_icon="🧪",
        layout="wide",
    )
    _apply_theme()
    settings = Settings()
    st.sidebar.title("SciOne")
    st.sidebar.caption("Lab capability prototype")
    page = render_workspace_navigation()
    st.sidebar.caption("Local demo · no account or database setup required")
    if page == "Developer Tools":
        _render_developer_tools(settings)
        return
    if page == "Specifications":
        render_specifications(settings)
        return
    if page == "Reports":
        render_reports(settings)
        return
    try:
        catalog = load_reference_catalog()
        if page == "My Lab":
            render_my_lab(
                FileLabStore(settings.scione_workspace_dir, catalog, seed_demo=False), catalog
            )
        else:
            render_test_catalog(catalog)
    except (CatalogError, LabStoreError, ValidationError) as exc:
        st.error(f"Unable to open the local workspace: {exc}")
        st.caption("Saved data has not been reset. Check the local workspace before retrying.")


if __name__ == "__main__":
    main()

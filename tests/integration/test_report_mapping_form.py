"""Mapping corrections have useful field validation instead of schema tracebacks."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from streamlit.testing.v1 import AppTest

from scione.catalogs import LabCapability, LabProfile, load_reference_catalog
from scione.catalogs.models import LabOperatingLimit
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.reports.matching import build_report
from scione.reports.models import ReportSnapshot
from scione.reports.store import FileReportStore
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")

    def no_model(*args: object, **kwargs: object) -> None:
        pytest.fail("Mapping corrections must not invoke a model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", no_model)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", no_model)


def snapshot(
    root: Path,
    *,
    multiple_methods: bool = False,
    hard_gap: bool = False,
    wrong_method: bool = False,
    no_lab: bool = False,
) -> ReportSnapshot:
    methods = ["ISO 2409", "ASTM D3359"] if multiple_methods else ["ISO 2409"]
    spacing = "3" if hard_gap else "2"
    quote = f"Cross-cut adhesion: {', '.join(methods)}, {spacing} mm spacing; GT 0 to 1."
    with pymupdf.open() as pdf:
        pdf.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 700), quote, fontsize=10)
        pdf_bytes = pdf.tobytes()
    specs = FileSpecStore(root)
    stored = specs.import_pdf("mapping-customer.pdf", pdf_bytes)
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": stored.document_id,
            "prompt_version": "offline_mapping_form",
            "schema_version": "0.1",
            "provider": "offline",
            "model": "no-network",
            "latency_ms": 1,
            "usage": {},
            "raw_response": "Original response preserved.",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Mapping correction fixture"},
                "requirements": [
                    {
                        "id": "req_adhesion",
                        "original_test_name": "Cross-cut adhesion",
                        "requested_methods": [{"code": code} for code in methods],
                        "conditions": [
                            {"name": "Spacing", "raw_value": f"{spacing} mm spacing", "unit": "mm"}
                        ],
                        "acceptance_criteria": [{"name": "Rating", "raw_value": "GT 0 to 1"}],
                        "evidence": [{"page": 1, "quote": quote}],
                    }
                ],
            },
        }
    )
    specs.save_extraction(stored.document_id, run)
    catalog = load_reference_catalog()
    lab = LabProfile(
        id="mapping_lab",
        name="Offline mapping lab",
        capabilities=[]
        if no_lab
        else [
            LabCapability(
                id="cap_adhesion",
                canonical_test_id="cross_cut_adhesion",
                method_ids=["astm_d3359"] if wrong_method else ["iso_2409", "astm_d3359"],
                structured_limits=[LabOperatingLimit(parameter="spacing", values=["2"], unit="mm")],
            )
        ],
    )
    return build_report(
        stored,
        run,
        specs.load_review(stored.document_id, run.run_id),
        lab,
        catalog,
        FileReportStore(root).load_workspace(stored.document_id, run.run_id),
        pdf_bytes=pdf_bytes,
    )


def mapping_app(snapshot_json: str, workspace: str) -> None:
    from pathlib import Path

    import streamlit as st

    from scione.reports.models import ReportSnapshot
    from scione.reports.store import FileReportStore
    from scione.web.reports import _mapping_editor, _prepare_editor

    report = ReportSnapshot.model_validate_json(snapshot_json)
    line = report.lines[0]
    stale = _prepare_editor(report, line)
    _mapping_editor(report, line, FileReportStore(Path(workspace)), stale)
    if "_report_notice" in st.session_state:
        st.success(st.session_state["_report_notice"])


def open_editor(report: ReportSnapshot, root: Path) -> AppTest:
    return AppTest.from_function(
        mapping_app, args=(report.model_dump_json(), str(root)), default_timeout=15
    ).run()


def saved_choice(report: ReportSnapshot, root: Path):
    return FileReportStore(root).load_workspace(report.document_id, report.source_run.run_id)


def error_text(app: AppTest) -> str:
    assert not app.exception
    result = "\n".join(item.value for item in app.error)
    assert "ValidationError" not in result
    assert "pydantic" not in result.lower()
    return result


@pytest.mark.parametrize("note", ["", "   \n "])
def test_ordinary_full_method_save_needs_no_typed_note(tmp_path: Path, note: str) -> None:
    report = snapshot(tmp_path, multiple_methods=True)
    app = open_editor(report, tmp_path)
    app.text_area(key="report_mapping_note").set_value(note)
    app = app.button(key="report_save_mapping").click().run()

    assert not app.exception
    assert not app.error
    choice = saved_choice(report, tmp_path).mappings["req_adhesion"]
    assert choice.selected_method_codes == ["ISO 2409", "ASTM D3359"]
    assert choice.capability_verdict == "unreviewed"
    assert "Operating scope was not assessed" in choice.note
    assert report.source_run.raw_response == "Original response preserved."


def test_reducing_original_methods_requires_explanation(tmp_path: Path) -> None:
    report = snapshot(tmp_path, multiple_methods=True)
    app = open_editor(report, tmp_path)
    app.multiselect(key="report_mapping_methods").set_value(["ISO 2409"])
    app = app.button(key="report_save_mapping").click().run()
    assert "removed method does not apply" in error_text(app)
    assert saved_choice(report, tmp_path).version == 0

    app.text_area(key="report_mapping_note").set_value("Source note makes ASTM regional only.")
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    choice = saved_choice(report, tmp_path).mappings["req_adhesion"]
    assert choice.selected_method_codes == ["ISO 2409"]
    assert choice.note == "Source note makes ASTM regional only."


def test_empty_method_selection_is_field_error_not_saved_gap(tmp_path: Path) -> None:
    report = snapshot(tmp_path)
    app = open_editor(report, tmp_path)
    app.multiselect(key="report_mapping_methods").set_value([])
    app = app.button(key="report_save_mapping").click().run()
    assert "Keep at least one method" in error_text(app)
    assert saved_choice(report, tmp_path).version == 0


def test_limitation_requires_explanation_instead_of_generic_schema_error(tmp_path: Path) -> None:
    report = snapshot(tmp_path)
    app = open_editor(report, tmp_path)
    app.selectbox(key="report_mapping_verdict").set_value("unsupported")
    app.text_area(key="report_mapping_note").set_value(" \n ")
    app = app.button(key="report_save_mapping").click().run()
    assert "describing the limitation" in error_text(app)
    assert saved_choice(report, tmp_path).version == 0

    app.text_area(key="report_mapping_note").set_value("Fixture is temporarily unavailable.")
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    assert (
        saved_choice(report, tmp_path).mappings["req_adhesion"].capability_verdict == "unsupported"
    )


@pytest.mark.parametrize("verdict", ["supported", "unsupported"])
def test_scope_decision_requires_real_lab_declaration(tmp_path: Path, verdict: str) -> None:
    report = snapshot(tmp_path, no_lab=True)
    app = open_editor(report, tmp_path)
    app.selectbox(key="report_mapping_verdict").set_value(verdict)
    app.text_area(key="report_mapping_note").set_value("No lab declaration is available.")
    app = app.button(key="report_save_mapping").click().run()
    assert "Choose a lab capability" in error_text(app)
    assert saved_choice(report, tmp_path).version == 0


@pytest.mark.parametrize("case", ["wrong_method", "hard_gap", "changed_selection"])
def test_supported_verdict_cannot_bypass_displayed_match(tmp_path: Path, case: str) -> None:
    report = snapshot(
        tmp_path,
        wrong_method=case == "wrong_method",
        hard_gap=case == "hard_gap",
        multiple_methods=case == "changed_selection",
    )
    app = open_editor(report, tmp_path)
    app.selectbox(key="report_mapping_capability").set_value("cap_adhesion")
    app.selectbox(key="report_mapping_verdict").set_value("supported")
    app.text_area(key="report_mapping_note").set_value("Reviewer claims support.")
    if case == "changed_selection":
        app.multiselect(key="report_mapping_methods").set_value(["ISO 2409"])
    app = app.button(key="report_save_mapping").click().run()
    errors = error_text(app)
    assert (
        "exceeds the recorded lab limits" in errors
        if case == "hard_gap"
        else ("conditions not assessed first" in errors)
    )
    assert saved_choice(report, tmp_path).version == 0


def test_resolved_supported_selection_can_save_without_invented_reasoning(tmp_path: Path) -> None:
    report = snapshot(tmp_path)
    app = open_editor(report, tmp_path)
    app.selectbox(key="report_mapping_verdict").set_value("supported")
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    assert not app.error
    choice = saved_choice(report, tmp_path).mappings["req_adhesion"]
    assert choice.capability_verdict == "supported"
    assert "reviewer marked" in choice.note


def test_missing_lab_entry_allows_plain_matching_save_with_no_scope_approval(
    tmp_path: Path,
) -> None:
    report = snapshot(tmp_path, no_lab=True)
    app = open_editor(report, tmp_path)
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    assert not app.error
    choice = saved_choice(report, tmp_path).mappings["req_adhesion"]
    assert choice.capability_id is None
    assert choice.capability_verdict == "unreviewed"

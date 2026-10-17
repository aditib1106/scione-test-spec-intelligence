"""Offline customer-to-lab report journey with isolated PDFs and local JSON stores."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf
import pytest
from pytest import MonkeyPatch
from streamlit.delta_generator import DeltaGenerator
from streamlit.testing.v1 import AppTest

from scione.catalogs import FileLabStore, LabCapability, load_reference_catalog
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.reports.models import MappingChoice
from scione.reports.store import FileReportStore
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore

APP = Path(__file__).parents[2] / "src" / "scione" / "web" / "app.py"
PENCIL_QUOTE = "Pencil hardness: ISO 15184, 750 g load; at least F."
ADHESION_QUOTE = "Cross-cut adhesion: ISO 2409, 2 mm spacing; GT 0 to 1."
PULL_QUOTE = "Pull-off adhesion: ISO 4624, 20 mm dolly; at least 5 MPa."
TABS = ["Capability review", "PDF preview", "Saved reports", "Developer"]


@pytest.fixture(autouse=True)
def offline_reports(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))

    def reject_model_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Report matching, review and export must never call a model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)


def make_pdf(*pages: str) -> bytes:
    with pymupdf.open() as document:
        for text in pages:
            document.new_page().insert_textbox(
                pymupdf.Rect(35, 35, 560, 700),
                "Offline customer coating specification.\n" + text,
                fontsize=10,
            )
        return document.tobytes()


@pytest.fixture
def stores(tmp_path: Path):
    root = tmp_path / "workspace"
    specs = FileSpecStore(root)
    reports = FileReportStore(root)
    labs = FileLabStore(root, load_reference_catalog())
    return specs, reports, labs


def seed(stores, *, name: str = "customer.pdf", count: int = 3, one_page: bool = False):
    specs, reports, labs = stores
    texts = [PENCIL_QUOTE, ADHESION_QUOTE, PULL_QUOTE][:count]
    pages = ["\n".join(texts)] if one_page else texts
    document = specs.import_pdf(name, make_pdf(*pages))
    requirements = [
        {
            "id": "req_pencil",
            "original_test_name": "Pencil hardness",
            "requested_methods": [{"code": "ISO 15184"}],
            "conditions": [{"name": "Load", "raw_value": "750", "unit": "g"}],
            "acceptance_criteria": [{"name": "Hardness", "raw_value": "At least F"}],
            "evidence": [{"page": 1, "quote": PENCIL_QUOTE}],
        },
        {
            "id": "req_adhesion",
            "original_test_name": "Cross-cut adhesion",
            "requested_methods": [{"code": "ISO 2409"}],
            "conditions": [{"name": "Spacing", "raw_value": "2", "unit": "mm"}],
            "acceptance_criteria": [{"name": "Rating", "raw_value": "GT 0 to 1"}],
            "evidence": [{"page": 1 if one_page else 2, "quote": ADHESION_QUOTE}],
        },
        {
            "id": "req_pull",
            "original_test_name": "Pull-off adhesion",
            "requested_methods": [{"code": "ISO 4624"}],
            "conditions": [{"name": "Dolly", "raw_value": "20", "unit": "mm"}],
            "acceptance_criteria": [{"name": "Strength", "raw_value": "At least 5 MPa"}],
            "evidence": [{"page": 1 if one_page else 3, "quote": PULL_QUOTE}],
        },
    ][:count]
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": document.document_id,
            "prompt_version": "offline_customer_fixture",
            "schema_version": "0.1",
            "provider": "offline_test",
            "model": "no_network",
            "latency_ms": 1,
            "usage": {},
            "raw_response": "  ORIGINAL MODEL TEXT\n",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Offline customer spec"},
                "requirements": requirements,
            },
        }
    )
    specs.save_extraction(document.document_id, run)
    lab = labs.load_or_create()
    lab.capabilities = [
        LabCapability(
            id="cap_pencil",
            canonical_test_id="pencil_hardness",
            method_ids=["iso_15184"],
            declared_constraints="750 g test load; coated flat panels.",
            price=Decimal("100.00"),
            currency="USD",
            pricing_basis="per panel",
        ),
        LabCapability(
            id="cap_adhesion",
            canonical_test_id="cross_cut_adhesion",
            method_ids=["astm_d3359"],
            declared_constraints="Tape adhesion only; ISO 2409 is not declared.",
        ),
    ]
    labs.save(lab)
    return document, run


def open_reports() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    return app.sidebar.button(key="nav_reports").click().run()


def all_text(app) -> str:
    values = []
    for kind in ("markdown", "caption", "info", "warning", "error", "text", "json"):
        values.extend(str(element.value) for element in app.get(kind))
    for metric in app.metric:
        values.extend((metric.label, str(metric.value)))
    for table in (*app.dataframe, *app.table):
        values.append(table.value.to_string(index=False))
    return "\n".join(values)


def inspect_pencil(app: AppTest) -> AppTest:
    return app.selectbox(key="report_item").set_value("req_pencil").run()


def save_support(app: AppTest) -> AppTest:
    app.selectbox(key="report_mapping_test").set_value("pencil_hardness")
    app.selectbox(key="report_mapping_capability").set_value("cap_pencil")
    app.selectbox(key="report_mapping_verdict").set_value("supported")
    app.text_area(key="report_mapping_note").set_value("Reviewed 750 g flat-panel scope.")
    return app.button(key="report_save_mapping").click().run()


def quote_submit(app: AppTest):
    return next(button for button in app.button if button.label == "Save quote quantity")


def pending_review_count(app: AppTest) -> int:
    return int(next(metric.value for metric in app.metric if metric.label == "Need review"))


def test_empty_reports_provide_next_step_without_saving_fake_report(stores) -> None:
    specs, reports, _ = stores
    app = open_reports()
    assert not app.exception
    assert app.title[0].value == "Reports"
    assert "No specifications saved" in all_text(app)
    assert not app.tabs
    assert reports.list_reports() == []
    assert specs.list_documents() == []
    assert not reports.root.exists()


def test_uploaded_pdf_without_analysis_offers_source_not_fabricated_gap_report(stores) -> None:
    specs, reports, _ = stores
    document = specs.import_pdf("unanalyzed.pdf", make_pdf(PENCIL_QUOTE))
    app = open_reports()
    assert not app.exception
    assert app.selectbox(key="report_document").value == document.document_id
    assert "Analyze this PDF" in all_text(app)
    assert app.button(key="report_analyze_source")
    assert not any(button.key == "report_generate" for button in app.button)
    assert reports.list_reports() == []


def test_current_comparison_distinguishes_method_match_wrong_method_and_missing_test(
    stores,
) -> None:
    document, run = seed(stores)
    specs, reports, _ = stores
    before = specs.load_review(document.document_id, run.run_id)
    app = open_reports()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == TABS
    table = app.tabs[0].dataframe[0].value.set_index("ID")
    assert table.loc["req_pencil", "Result"] == "Method listed in-house"
    assert table.loc["req_adhesion", "Result"] == "Method gap"
    assert table.loc["req_pull", "Result"] == "Not in lab catalog"
    assert table.loc["req_adhesion", "Requested method"] == "ISO 2409"
    assert "ASTM D3359" in table.loc["req_adhesion", "Lab method"]
    assert table.loc["req_pencil", "Lab scope review"] == "Pending lab review"
    assert table.loc["req_adhesion", "Lab scope review"] == "Resolve test / method first"
    assert table.loc["req_pull", "Lab scope review"] == "Resolve test / method first"
    assert "billable test count" in all_text(app)
    assert specs.load_review(document.document_id, run.run_id) == before
    assert reports.list_reports() == []
    assert not reports.root.exists()


def test_method_match_without_source_flags_still_needs_lab_scope_review(stores) -> None:
    seed(stores, count=1)
    app = open_reports()
    assert not app.exception
    assert pending_review_count(app) == 1
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_pencil"]
    assert row["Result"] == "Method listed in-house"
    assert row["Content review"] == "No row flags"
    assert row["Lab scope review"] == "Pending lab review"
    app = app.selectbox(key="report_group").set_value("Needs review").run()
    assert not app.exception
    assert list(app.tabs[0].dataframe[0].value["ID"]) == ["req_pencil"]
    app = inspect_pencil(app)
    editor = next(
        expander
        for expander in app.expander
        if expander.label == "Adjust test matching / confirm lab scope"
    )
    assert editor.proto.expanded
    assert "Test identity" in all_text(app)
    assert "Lab capability for this request" in all_text(app)


def test_current_lab_scope_confirmation_removes_ready_row_from_pending_review(stores) -> None:
    document, run = seed(stores, count=1)
    specs, _, _ = stores
    source_review = specs.load_review(document.document_id, run.run_id)
    app = save_support(inspect_pencil(open_reports()))
    assert not app.exception
    assert pending_review_count(app) == 0
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_pencil"]
    assert row["Content review"] == "No row flags"
    assert row["Lab scope review"] == "Confirmed by lab reviewer"
    app = app.selectbox(key="report_group").set_value("Needs review").run()
    assert not app.exception
    assert app.tabs[0].dataframe[0].value.empty
    assert specs.load_review(document.document_id, run.run_id) == source_review


def test_lab_scope_confirmation_does_not_clear_unresolved_source_review(stores) -> None:
    document, run = seed(stores, count=1)
    specs, reports, _ = stores
    changed = run.model_copy(deep=True)
    changed.run_id = str(uuid4())
    changed.extraction.review_issues = ["req_pencil: the source requirement needs checking."]
    specs.save_extraction(document.document_id, changed)
    source_review = specs.load_review(document.document_id, changed.run_id)
    app = save_support(inspect_pencil(open_reports()))
    assert not app.exception
    assert pending_review_count(app) == 1
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_pencil"]
    assert row["Content review"] == "Needs review"
    assert row["Lab scope review"] == "Confirmed by lab reviewer"
    assert quote_submit(app).disabled
    app = app.selectbox(key="report_group").set_value("Needs review").run()
    assert list(app.tabs[0].dataframe[0].value["ID"]) == ["req_pencil"]
    assert specs.load_review(document.document_id, changed.run_id) == source_review
    assert specs.load_extraction(document.document_id, changed.run_id) == changed
    assert (
        reports.load_workspace(document.document_id, changed.run_id)
        .mappings["req_pencil"]
        .capability_verdict
        == "supported"
    )


def test_lab_declaration_change_returns_previously_confirmed_scope_to_pending(stores) -> None:
    document, run = seed(stores, count=1)
    specs, reports, labs = stores
    app = save_support(inspect_pencil(open_reports()))
    assert pending_review_count(app) == 0
    workspace_before = reports.load_workspace(document.document_id, run.run_id)
    source_review = specs.load_review(document.document_id, run.run_id)
    lab = labs.load_or_create()
    lab.capabilities[0].declared_constraints = "Changed operating limits need a new scope review."
    labs.save(lab)
    app = app.run()
    assert not app.exception
    assert pending_review_count(app) == 1
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_pencil"]
    assert row["Lab scope review"] == "Pending lab review"
    app = app.selectbox(key="report_group").set_value("Needs review").run()
    assert list(app.tabs[0].dataframe[0].value["ID"]) == ["req_pencil"]
    assert reports.load_workspace(document.document_id, run.run_id) == workspace_before
    assert specs.load_review(document.document_id, run.run_id) == source_review


def test_reviewer_can_mark_method_listed_requirement_outside_lab_scope(stores) -> None:
    seed(stores, count=1)
    app = inspect_pencil(open_reports())
    app.selectbox(key="report_mapping_verdict").set_value("unsupported")
    app.text_area(key="report_mapping_note").set_value("Lab cannot handle this customer scope.")
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_pencil"]
    assert row["Result"] == "Condition / scope gap"
    assert row["Lab scope review"] == "Outside lab scope"
    assert quote_submit(app).disabled


def test_groups_filter_table_and_clear_inspected_item_when_outside_group(stores) -> None:
    seed(stores)
    app = inspect_pencil(open_reports())
    app = app.selectbox(key="report_group").set_value("Gaps").run()
    assert not app.exception
    assert set(app.tabs[0].dataframe[0].value["ID"]) == {"req_adhesion", "req_pull"}
    assert app.selectbox(key="report_item").value is None
    app = app.selectbox(key="report_group").set_value("Excluded").run()
    assert not app.exception
    assert not app.tabs[0].dataframe[0].value.shape[0]


def test_matching_requires_note_and_remains_separate_from_source_review(stores) -> None:
    document, run = seed(stores)
    specs, reports, _ = stores
    review_before = specs.load_review(document.document_id, run.run_id)
    app = inspect_pencil(open_reports())
    assert not app.exception
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    assert app.error
    assert reports.load_workspace(document.document_id, run.run_id).version == 0
    app = save_support(app)
    assert not app.exception
    workspace = reports.load_workspace(document.document_id, run.run_id)
    choice = workspace.mappings["req_pencil"]
    assert workspace.version == 1
    assert choice.canonical_test_id == "pencil_hardness"
    assert choice.capability_id == "cap_pencil"
    assert choice.capability_verdict == "supported"
    assert choice.note == "Reviewed 750 g flat-panel scope."
    assert specs.load_review(document.document_id, run.run_id) == review_before
    assert specs.load_extraction(document.document_id, run.run_id) == run


def test_quote_has_no_default_quantity_and_requires_support_and_explicit_scope(stores) -> None:
    document, run = seed(stores)
    _, reports, _ = stores
    app = inspect_pencil(open_reports())
    assert app.text_input(key="report_quote_quantity").value == ""
    assert not app.checkbox(key="report_quote_confirm").value
    assert quote_submit(app).disabled
    app = save_support(app)
    assert not quote_submit(app).disabled
    app.text_input(key="report_quote_quantity").set_value("3")
    app.text_area(key="report_quote_note").set_value("Three nonoverlapping coated panels.")
    app = quote_submit(app).click().run()
    assert not app.exception
    assert "Confirm scope and pricing basis" in all_text(app)
    assert not reports.load_workspace(document.document_id, run.run_id).quantities
    app.checkbox(key="report_quote_confirm").check()
    app = quote_submit(app).click().run()
    assert not app.exception
    saved = reports.load_workspace(document.document_id, run.run_id)
    assert saved.quantities["req_pencil"].quantity == Decimal("3")
    assert saved.quantities["req_pencil"].scope_and_basis_confirmed is True
    assert "USD 300.00" in all_text(app)
    assert "Partial quote subtotal" in all_text(app)


def test_scope_confirmation_cannot_promote_wrong_method_to_support_or_quote(stores) -> None:
    seed(stores)
    app = open_reports()
    app = app.selectbox(key="report_item").set_value("req_adhesion").run()
    app.selectbox(key="report_mapping_capability").set_value("cap_adhesion")
    app.selectbox(key="report_mapping_verdict").set_value("supported")
    app.text_area(key="report_mapping_note").set_value("User claims support; wrong method remains.")
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_adhesion"]
    assert row["Result"] == "Method gap"
    assert quote_submit(app).disabled


def test_generate_saves_consistent_pdf_json_and_exact_download_preview(
    stores, monkeypatch: MonkeyPatch
) -> None:
    import scione.web.reports as reports_page

    document, run = seed(stores)
    _, reports, _ = stores
    downloads: dict[str, Any] = {}
    previews: list[tuple[bytes, int]] = []
    original_download = DeltaGenerator.download_button
    original_preview = reports_page._preview

    def capture_download(self, label, data, *args, **kwargs):
        downloads[kwargs.get("key", label)] = data
        return original_download(self, label, data, *args, **kwargs)

    def capture_preview(pdf: bytes, page: int) -> bytes:
        previews.append((pdf, page))
        return original_preview(pdf, page)

    monkeypatch.setattr(DeltaGenerator, "download_button", capture_download)
    monkeypatch.setattr(reports_page, "_preview", capture_preview)
    app = open_reports()
    app = app.button(key="report_generate").click().run()
    assert not app.exception
    assert not app.error
    saved = reports.list_reports(document.document_id)
    assert len(saved) == 1
    snapshot = saved[0]
    assert snapshot.source_run == run
    assert len(snapshot.lines) == 3
    assert not snapshot.totals_by_currency
    assert app.session_state["report_view_tabs"] == "PDF preview"
    assert app.selectbox(key="report_snapshot").value == snapshot.report_id
    assert app.number_input(key="report_pdf_page").value == 1
    assert downloads["report_download_pdf"] == reports.read_pdf(snapshot.report_id)
    assert type(snapshot).model_validate_json(downloads["report_download_json"]) == snapshot
    with pymupdf.open(stream=downloads["report_download_pdf"], filetype="pdf") as pdf:
        text = "\n".join(page.get_text() for page in pdf)
        assert "Draft" in text or "DRAFT" in text
        assert "Pencil hardness" in text
        assert "ISO 2409" in text
        assert "ASTM D3359" in text
    assert previews[-1] == (downloads["report_download_pdf"], 1)


def test_saved_snapshot_survives_lab_edits_and_is_marked_historical(stores) -> None:
    document, _ = seed(stores)
    _, reports, labs = stores
    app = open_reports().button(key="report_generate").click().run()
    assert not app.exception
    saved = reports.list_reports(document.document_id)[0]
    pdf_before = reports.read_pdf(saved.report_id)
    lab = labs.load_or_create()
    lab.name = "Changed lab profile"
    lab.capabilities[0].price = Decimal("777")
    labs.save(lab)
    app = app.run()
    assert not app.exception
    assert "Saved snapshot: source, lab or review data has changed" in all_text(app)
    assert reports.load_report(saved.report_id) == saved
    assert reports.read_pdf(saved.report_id) == pdf_before
    assert "Historical snapshot" in all_text(app)


def test_new_document_resets_saved_report_and_page_without_cross_document_download(stores) -> None:
    first, _ = seed(stores, name="three-tests.pdf")
    _, reports, _ = stores
    app = open_reports().button(key="report_generate").click().run()
    first_snapshot = reports.list_reports(first.document_id)[0]
    with pymupdf.open(stream=reports.read_pdf(first_snapshot.report_id), filetype="pdf") as pdf:
        last_page = len(pdf)
    assert last_page > 1
    app = app.number_input(key="report_pdf_page").set_value(last_page).run()
    second, _ = seed(stores, name="one-test.pdf", count=1, one_page=True)
    app = app.run()
    app = app.selectbox(key="report_document").set_value(second.document_id).run()
    assert not app.exception
    assert "No report saved for this specification" in all_text(app)
    assert not any(box.key == "report_snapshot" for box in app.selectbox)
    app = app.button(key="report_generate").click().run()
    assert not app.exception
    second_snapshot = reports.list_reports(second.document_id)[0]
    assert app.selectbox(key="report_snapshot").value == second_snapshot.report_id
    assert app.number_input(key="report_pdf_page").value == 1
    assert second_snapshot.document_id != first_snapshot.document_id
    assert reports.list_reports(first.document_id) == [first_snapshot]


def test_stale_matching_editor_cannot_overwrite_another_saved_choice(stores) -> None:
    document, run = seed(stores)
    _, reports, _ = stores
    app = inspect_pencil(open_reports())
    app.text_area(key="report_mapping_note").set_value("Stale unsaved edit")
    workspace = reports.load_workspace(document.document_id, run.run_id)
    workspace.mappings["req_pencil"] = MappingChoice(
        canonical_test_id="pencil_hardness", note="Another editor saved this choice"
    )
    saved = reports.save_workspace(workspace, expected_version=0)
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    assert reports.load_workspace(document.document_id, run.run_id) == saved
    assert "changed" in all_text(app).lower() or "Reload" in all_text(app)
    app = app.run()
    assert app.button(key="report_save_mapping").disabled
    app = app.button(key="report_reload_editor").click().run()
    assert not app.exception
    assert app.text_area(key="report_mapping_note").value == "Another editor saved this choice"
    assert not app.button(key="report_save_mapping").disabled


def test_report_source_link_opens_corresponding_requirement_in_specifications(stores) -> None:
    document, run = seed(stores)
    app = inspect_pencil(open_reports())
    app = app.button(key="report_open_source").click().run()
    assert not app.exception
    assert app.session_state["workspace_page"] == "Specifications"
    assert app.session_state["spec_requirement_choice"] == "req_pencil"
    assert app.session_state["_spec_requirement_run"] == (document.document_id, run.run_id)


def test_invalid_model_citation_does_not_break_source_navigation(stores) -> None:
    document, run = seed(stores, count=1)
    specs, _, _ = stores
    changed = run.model_copy(deep=True)
    changed.run_id = str(uuid4())
    changed.extraction.requirements[0].evidence[0].page = 999
    specs.save_extraction(document.document_id, changed)
    app = inspect_pencil(open_reports())
    app = app.button(key="report_open_source").click().run()
    assert not app.exception
    assert app.session_state["workspace_page"] == "Specifications"
    assert app.session_state["spec_pdf_page"] == 1


def test_explicit_quote_enters_saved_snapshot_but_excluded_source_is_never_billed(stores) -> None:
    document, run = seed(stores, count=1)
    specs, reports, _ = stores
    app = save_support(inspect_pencil(open_reports()))
    app.text_input(key="report_quote_quantity").set_value("2")
    app.text_area(key="report_quote_note").set_value("Two coated panels, no overlap.")
    app.checkbox(key="report_quote_confirm").check()
    app = quote_submit(app).click().run()
    app = app.button(key="report_generate").click().run()
    assert not app.exception
    before = reports.list_reports(document.document_id)[0]
    assert before.totals_by_currency == {"USD": Decimal("200.00")}
    assert before.lines[0].quote.quantity == Decimal("2")
    assert before.lines[0].quote.unit_price == Decimal("100.00")
    assert before.lines[0].quote.subtotal == Decimal("200.00")
    specs.save_review(
        document.document_id,
        run.run_id,
        "req_pencil",
        "excluded",
        "Customer removed this scope.",
        expected_version=0,
    )
    app = app.run()
    assert not app.exception
    assert not app.tabs[0].dataframe[0].value.shape[0]
    app = app.button(key="report_generate").click().run()
    assert not app.exception
    after = reports.list_reports(document.document_id)[0]
    assert after.report_id != before.report_id
    assert len(after.lines) == 1
    assert after.lines[0].status == "excluded"
    assert after.lines[0].quote is None
    assert not after.totals_by_currency
    assert reports.load_report(before.report_id) == before


def test_returning_from_source_review_reinitializes_same_item_editor_widgets(stores) -> None:
    document, run = seed(stores)
    _, reports, _ = stores
    app = save_support(inspect_pencil(open_reports()))
    saved = reports.load_workspace(document.document_id, run.run_id)
    app = app.button(key="report_open_source").click().run()
    assert not app.exception
    assert app.session_state["workspace_page"] == "Specifications"
    app = app.sidebar.button(key="nav_reports").click().run()
    assert not app.exception
    if app.selectbox(key="report_item").value != "req_pencil":
        app = inspect_pencil(app)
    assert not app.exception
    assert app.text_area(key="report_mapping_note").value == saved.mappings["req_pencil"].note
    assert app.selectbox(key="report_mapping_verdict").value == "supported"
    assert app.selectbox(key="report_mapping_capability").value == "cap_pencil"
    assert not app.checkbox(key="report_quote_confirm").value
    assert not app.button(key="report_save_mapping").disabled
    assert reports.load_workspace(document.document_id, run.run_id) == saved


def test_lab_pricing_change_requires_scope_then_quantity_reconfirmation(stores) -> None:
    document, run = seed(stores, count=1)
    _, reports, labs = stores
    app = save_support(inspect_pencil(open_reports()))
    app.text_input(key="report_quote_quantity").set_value("3")
    app.text_area(key="report_quote_note").set_value("Three panels, reviewed as one scope.")
    app.checkbox(key="report_quote_confirm").check()
    app = quote_submit(app).click().run()
    assert not app.exception
    before = reports.load_workspace(document.document_id, run.run_id)
    original_quantity_hash = before.quantities["req_pencil"].lab_fingerprint
    assert original_quantity_hash
    lab = labs.load_or_create()
    lab.capabilities[0].price = Decimal("7.125")
    lab.capabilities[0].pricing_basis = "per sample set"
    labs.save(lab)
    app = app.run()
    assert not app.exception
    assert app.button(key="report_save_mapping").disabled
    assert quote_submit(app).disabled
    app = app.selectbox(key="report_group").set_value("Needs review").run()
    assert not app.exception
    row = app.tabs[0].dataframe[0].value.set_index("ID").loc["req_pencil"]
    assert row["Result"] == "Method listed in-house"
    assert "saved quantity was not priced" in all_text(app)
    app = app.button(key="report_reload_editor").click().run()
    app.text_area(key="report_mapping_note").set_value(
        "Rechecked scope against the new lab rate and basis."
    )
    app = app.button(key="report_save_mapping").click().run()
    assert not app.exception
    after_scope = reports.load_workspace(document.document_id, run.run_id)
    assert after_scope.mappings["req_pencil"].lab_fingerprint != original_quantity_hash
    assert after_scope.quantities["req_pencil"].lab_fingerprint == original_quantity_hash
    assert "saved quantity was not priced" in all_text(app)
    app = app.selectbox(key="report_group").set_value("All active").run()
    app = inspect_pencil(app)
    assert not app.exception
    assert not quote_submit(app).disabled
    assert not app.checkbox(key="report_quote_confirm").value
    app.text_area(key="report_quote_note").set_value("Three sample sets using the updated basis.")
    app.checkbox(key="report_quote_confirm").check()
    app = quote_submit(app).click().run()
    assert not app.exception
    after_quantity = reports.load_workspace(document.document_id, run.run_id)
    assert (
        after_quantity.quantities["req_pencil"].lab_fingerprint
        == after_scope.mappings["req_pencil"].lab_fingerprint
    )
    assert "USD 21.375" in all_text(app)


def test_generating_after_lab_edit_does_not_reprice_old_human_quantity(stores) -> None:
    document, run = seed(stores, count=1)
    _, reports, labs = stores
    app = save_support(inspect_pencil(open_reports()))
    app.text_input(key="report_quote_quantity").set_value("2")
    app.text_area(key="report_quote_note").set_value(
        "Two panels, reviewed using the original lab catalog."
    )
    app.checkbox(key="report_quote_confirm").check()
    app = quote_submit(app).click().run()
    app = app.button(key="report_generate").click().run()
    assert not app.exception
    original = reports.list_reports(document.document_id)[0]
    original_pdf = reports.read_pdf(original.report_id)
    assert original.totals_by_currency == {"USD": Decimal("200.00")}
    lab = labs.load_or_create()
    lab.capabilities[0].price = Decimal("999")
    lab.capabilities[0].pricing_basis = "per batch"
    labs.save(lab)
    app = app.run()
    app = app.button(key="report_generate").click().run()
    assert not app.exception
    current = reports.list_reports(document.document_id)[0]
    assert current.report_id != original.report_id
    assert current.lab.capabilities[0].price == Decimal("999")
    assert current.workspace.quantities["req_pencil"].quantity == Decimal("2")
    assert current.lines[0].quote is None
    assert not current.totals_by_currency
    assert reports.load_report(original.report_id) == original
    assert reports.read_pdf(original.report_id) == original_pdf
    assert (
        reports.load_workspace(document.document_id, run.run_id).quantities["req_pencil"]
        == original.workspace.quantities["req_pencil"]
    )

"""Smoke-test the Streamlit interface without starting a browser or model call."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest
from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from scione.catalogs import FileLabStore, LabProfile
from scione.config import Settings
from scione.providers import TokenUsage
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.specifications.models import CustomerExtraction, CustomerExtractionRun
from scione.specifications.store import FileSpecStore

REPOSITORY_ROOT = Path(__file__).parents[2]
APP = REPOSITORY_ROOT / "src" / "scione" / "web" / "app.py"
WORKSPACE_PAGES = ["My Lab", "Test Catalog", "Specifications", "Reports", "Developer Tools"]
WORKSPACE_NAVIGATION_KEYS = {
    "My Lab": "nav_my_lab",
    "Test Catalog": "nav_test_catalog",
    "Specifications": "nav_specifications",
    "Reports": "nav_reports",
    "Developer Tools": "nav_developer_tools",
}


@pytest.fixture(autouse=True)
def offline_workspace(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Keep UI tests independent of local credentials, saved data, and paid APIs."""

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))

    def reject_model_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Rendering or navigating the workspace must not call a model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)


def open_workspace_page(app: AppTest, page: str) -> AppTest:
    return app.sidebar.button(key=WORKSPACE_NAVIGATION_KEYS[page]).click().run()


def click_button(app: AppTest, label: str) -> AppTest:
    return next(button for button in app.button if button.label == label).click().run()


def open_capability_editor(app: AppTest, capability_id: str | None = None) -> AppTest:
    if capability_id is None:
        return app.button(key="add_capability").click().run()
    app = app.selectbox(key="capability_to_edit").set_value(capability_id).run()
    return app.button(key="edit_capability").click().run()


def test_web_app_starts_with_my_lab_without_credentials() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "My Lab"
    assert [button.label for button in app.sidebar.button] == WORKSPACE_PAGES
    assert [expander.label for expander in app.sidebar.expander] == ["Developer tools"]
    assert not app.sidebar.radio
    assert app.session_state["workspace_page"] == "My Lab"
    assert not any(button.label == "Run extraction" for button in app.button)
    assert not any(field.key == "capability_test_id" for field in app.selectbox)
    assert not any(button.label == "Save test" for button in app.button)
    assert app.dataframe[0].value.shape[0] == 5


def test_capability_editor_only_opens_on_request_and_cancel_does_not_save(
    tmp_path: Path,
) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    store = FileLabStore(tmp_path / "workspace")
    before = store.load_or_create()
    app = open_capability_editor(app)
    app.text_area(key="capability_notes").set_value("An unsaved draft.")
    app = app.button(key="cancel_capability").click().run()

    assert not app.exception
    assert not any(field.key == "capability_test_id" for field in app.selectbox)
    assert store.load_or_create() == before

    app = open_capability_editor(app)
    assert app.text_area(key="capability_notes").value == ""


def test_workspace_navigation_does_not_create_model_runs(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path / "workspace")
    app = AppTest.from_file(str(APP), default_timeout=10).run()

    assert store.list_documents() == []
    for page in WORKSPACE_PAGES:
        app = open_workspace_page(app, page)
        assert not app.exception
        assert app.title[0].value == page

    assert store.list_documents() == []
    assert not list(store.root.rglob("run.json"))


def test_reports_are_marked_as_unimplemented() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_workspace_page(app, "Reports")

    assert not app.exception
    assert any("Not implemented yet" in info.value for info in app.info)
    assert not app.get("download_button")


@pytest.mark.parametrize("query", ["铅笔硬度", "ISO 15184"])
def test_catalog_search_finds_test_by_alias_or_method(query: str) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_workspace_page(app, "Test Catalog")
    app = app.text_input(key="catalog_search").set_value(query).run()

    assert not app.exception
    assert app.dataframe[0].value["Test ID"].tolist() == ["pencil_hardness"]


def test_lab_details_persist_between_app_sessions() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app.text_input(key="lab_name").set_value("Prototype Materials Lab")
    app.text_area(key="lab_description").set_value("Synthetic lab for an offline walkthrough.")
    app = click_button(app, "Save lab details")

    assert not app.exception
    assert app.success

    reopened = AppTest.from_file(str(APP), default_timeout=10).run()

    assert not reopened.exception
    assert reopened.text_input(key="lab_name").value == "Prototype Materials Lab"
    assert (
        reopened.text_area(key="lab_description").value
        == "Synthetic lab for an offline walkthrough."
    )


def test_capability_and_price_persist_and_can_be_edited(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_capability_editor(app)
    app = app.selectbox(key="capability_test_id").set_value("pencil_hardness").run()
    app = app.checkbox(key="capability_has_price").check().run()
    app.text_area(key="capability_constraints").set_value("750 g; flat coated panels only.")
    app.text_area(key="capability_notes").set_value("Offline integration test capability.")
    app.text_input(key="capability_price").set_value("125.50")
    app = click_button(app, "Save test")

    assert not app.exception
    assert app.success
    saved_lab_path = tmp_path / "workspace" / "lab.json"
    saved = LabProfile.model_validate_json(saved_lab_path.read_text(encoding="utf-8"))
    capability = next(
        item for item in saved.capabilities if item.notes == "Offline integration test capability."
    )
    assert capability.canonical_test_id == "pencil_hardness"
    assert capability.method_ids == ["iso_15184"]
    assert str(capability.price) == "125.50"
    assert capability.currency
    assert capability.pricing_basis
    assert not any(field.key == "capability_test_id" for field in app.selectbox)
    assert any("Test saved" in message.value for message in app.success)

    # Reopening a saved test must update, not append a duplicate.
    app = open_capability_editor(app, capability.id)
    app = click_button(app, "Save test")
    assert not app.exception
    resaved = LabProfile.model_validate_json(saved_lab_path.read_text(encoding="utf-8"))
    assert len(resaved.capabilities) == len(saved.capabilities)

    reopened = AppTest.from_file(str(APP), default_timeout=10).run()
    reopened = open_capability_editor(reopened, capability.id)

    assert not reopened.exception
    assert reopened.selectbox(key="capability_test_id").value == "pencil_hardness"
    assert reopened.multiselect(key="capability_method_ids").value == ["iso_15184"]
    assert (
        reopened.text_area(key="capability_constraints").value
        == "750 g; flat coated panels only."
    )
    assert reopened.checkbox(key="capability_has_price").value is True
    assert reopened.text_input(key="capability_price").value == "125.50"
    reopened.text_area(key="capability_notes").set_value("Updated after reopening the app.")
    reopened = click_button(reopened, "Save test")

    assert not reopened.exception
    updated = LabProfile.model_validate_json(saved_lab_path.read_text(encoding="utf-8"))
    assert len(updated.capabilities) == len(saved.capabilities)
    assert next(item for item in updated.capabilities if item.id == capability.id).notes == (
        "Updated after reopening the app."
    )


def test_invalid_price_does_not_overwrite_saved_lab(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = click_button(app, "Save lab details")
    saved_lab_path = tmp_path / "workspace" / "lab.json"
    original = saved_lab_path.read_bytes()

    app = open_capability_editor(app)
    app = app.checkbox(key="capability_has_price").check().run()
    app.text_input(key="capability_price").set_value("not-a-price")
    app = click_button(app, "Save test")

    assert not app.exception
    assert app.error
    assert saved_lab_path.read_bytes() == original


def test_lab_can_declare_test_without_claiming_a_method(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_capability_editor(app)
    app = app.selectbox(key="capability_test_id").set_value("pencil_hardness").run()
    app.multiselect(key="capability_method_ids").set_value([])
    app.text_area(key="capability_notes").set_value("Method needs confirmation.")
    app = click_button(app, "Save test")

    assert not app.exception
    saved = LabProfile.model_validate_json(
        (tmp_path / "workspace" / "lab.json").read_text(encoding="utf-8")
    )
    capability = next(
        item for item in saved.capabilities if item.notes == "Method needs confirmation."
    )
    assert capability.method_ids == []
    assert capability.price is None


@pytest.mark.parametrize(
    ("currency", "pricing_basis"), [("USD", "per specimen"), ("GBP", "per batch")]
)
def test_optional_pricing_draft_survives_toggle(currency: str, pricing_basis: str) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_capability_editor(app)
    app = app.checkbox(key="capability_has_price").check().run()
    assert app.text_input(key="capability_currency").value == "USD"
    assert app.text_input(key="capability_pricing_basis").value == "per specimen"
    app.text_input(key="capability_price").set_value("89.75")
    app.text_input(key="capability_currency").set_value(currency)
    app = app.text_input(key="capability_pricing_basis").set_value(pricing_basis).run()
    app = app.checkbox(key="capability_has_price").uncheck().run()

    assert not app.exception
    assert not any(field.key == "capability_price" for field in app.text_input)

    app = app.checkbox(key="capability_has_price").check().run()

    assert not app.exception
    assert app.text_input(key="capability_price").value == "89.75"
    assert app.text_input(key="capability_currency").value == currency
    assert app.text_input(key="capability_pricing_basis").value == pricing_basis


def test_stale_capability_editor_preserves_external_changes(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_capability_editor(app, "cap_pencil")
    store = FileLabStore(tmp_path / "workspace")
    lab = store.load_or_create()
    capabilities = [
        item.model_copy(update={"notes": "Changed by an external editor."})
        if item.id == "cap_pencil"
        else item
        for item in lab.capabilities
    ]
    store.save(lab.model_copy(update={"capabilities": capabilities}))
    external_saved = store.path.read_bytes()

    app.text_area(key="capability_notes").set_value("Stale browser draft.")
    app = click_button(app, "Save test")

    assert not app.exception
    assert any("lab has changed" in error.value.lower() for error in app.error)
    assert store.path.read_bytes() == external_saved
    assert next(
        item for item in store.load_or_create().capabilities if item.id == "cap_pencil"
    ).notes == "Changed by an external editor."


def test_saved_capability_can_be_reopened_after_workspace_navigation() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_capability_editor(app, "cap_cross_cut")
    app.text_area(key="capability_notes").set_value("Persisted before changing workspace page.")
    app = click_button(app, "Save test")
    app = open_workspace_page(app, "Test Catalog")
    app = open_workspace_page(app, "My Lab")
    app = open_capability_editor(app, "cap_cross_cut")

    assert not app.exception
    assert app.selectbox(key="capability_test_id").value == "cross_cut_adhesion"
    assert app.multiselect(key="capability_method_ids").value == ["iso_2409"]
    assert app.text_area(key="capability_notes").value == (
        "Persisted before changing workspace page."
    )
    assert app.checkbox(key="capability_has_price").value is True
    assert app.text_input(key="capability_price").value == "35.00"
    assert not any("Reload before saving" in warning.value for warning in app.warning)


def test_successful_saves_do_not_show_stale_editor_warning() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()

    for label in ("Save lab details", "Save test", "Save test", "Save lab details"):
        if label == "Save test":
            app = open_capability_editor(app, "cap_cross_cut")
        app = click_button(app, label)
        assert not app.exception
        assert not app.error
        assert app.success
        assert not any("Reload before saving" in warning.value for warning in app.warning)


def test_developer_tools_renders_empty_customer_diagnostics(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_workspace_page(app, "Developer Tools")

    assert not app.exception
    assert app.title[0].value == "Developer Tools"
    assert app.subheader[0].value == "Customer-spec diagnostics"
    assert any("No customer documents saved" in info.value for info in app.info)
    assert FileSpecStore(tmp_path / "workspace").list_documents() == []
    assert not app.json
    assert not app.get("download_button")


def test_developer_tools_renders_saved_customer_extraction_without_model_calls(
    tmp_path: Path,
) -> None:
    source = "Fictional customer request: pencil hardness using ISO 15184, at least F."
    with pymupdf.open() as document:
        document.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 400), source)
        pdf_bytes = document.tobytes()
    store = FileSpecStore(tmp_path / "workspace")
    stored = store.import_pdf("fictional-customer.pdf", pdf_bytes)
    extraction = CustomerExtraction.model_validate(
        {
            "schema_version": "0.1",
            "document": {"title": "Fictional customer request"},
            "requirements": [
                {
                    "id": "req_pencil",
                    "original_test_name": "Pencil hardness",
                    "requested_methods": [{"code": "ISO 15184"}],
                    "acceptance_criteria": [
                        {"name": "Pencil hardness", "raw_value": "at least F"}
                    ],
                    "evidence": [{"page": 1, "quote": source}],
                }
            ],
        }
    )
    run = CustomerExtractionRun(
        run_id="00000000-0000-4000-8000-000000000001",
        started_at=datetime(2026, 10, 17, tzinfo=UTC),
        document_id=stored.document_id,
        prompt_version="customer_requirements_test_fixture",
        schema_version="0.1",
        provider="offline-fixture",
        model="not-a-live-model",
        latency_ms=0,
        usage=TokenUsage(),
        raw_response=extraction.model_dump_json(),
        extraction=extraction,
    )
    store.save_extraction(stored.document_id, run)

    app = AppTest.from_file(str(APP), default_timeout=15).run()
    app = open_workspace_page(app, "Developer Tools")

    assert not app.exception
    assert app.title[0].value == "Developer Tools"
    assert app.selectbox(key="spec_debug_document").value == stored.document_id
    payloads = [json.loads(element.value) for element in app.json]
    assert extraction.model_dump(mode="json") in payloads
    metadata = next(payload for payload in payloads if payload.get("run_id") == run.run_id)
    assert metadata["provider"] == "offline-fixture"
    assert "raw_response" not in metadata
    assert "extraction" not in metadata
    assert any(text.value == run.raw_response for text in app.text)
    assert any(source in text.value for text in app.text)
    assert any("offline-fixture/not-a-live-model" in item.value for item in app.caption)
    assert app.number_input(key="spec_debug_text_page").value == 1
    assert [button.label for button in app.get("download_button")] == ["Download original PDF"]
    assert not app.tabs
    assert not app.metric
    assert store.load_latest_extraction(stored.document_id) == run
    assert store.list_documents()[0].requirement_count == 1

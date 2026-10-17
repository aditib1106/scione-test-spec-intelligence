"""Smoke-test the Streamlit interface without starting a browser or model call."""

from pathlib import Path

import pytest
from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from scione.catalogs import FileLabStore, LabProfile
from scione.config import Settings
from scione.providers import StaticModelProvider
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.workbench import execute_method_workbench

REPOSITORY_ROOT = Path(__file__).parents[2]
APP = REPOSITORY_ROOT / "src" / "scione" / "web" / "app.py"
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"
WORKSPACE_PAGES = ["My Lab", "Test Catalog", "Specifications", "Reports", "Developer Tools"]


@pytest.fixture(autouse=True)
def offline_workspace(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Keep UI tests independent of local credentials, saved data, and paid APIs."""

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("SCIONE_RUNS_DIR", str(tmp_path / "runs"))

    def reject_model_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Rendering or navigating the workspace must not call a model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)


def open_workspace_page(app: AppTest, page: str) -> AppTest:
    return app.sidebar.radio(key="workspace_page").set_value(page).run()


def click_button(app: AppTest, label: str) -> AppTest:
    return next(button for button in app.button if button.label == label).click().run()


def test_web_app_starts_with_my_lab_without_credentials() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "My Lab"
    assert app.sidebar.radio(key="workspace_page").options == WORKSPACE_PAGES
    assert app.sidebar.radio(key="workspace_page").value == "My Lab"
    assert not any(button.label == "Run extraction" for button in app.button)


def test_workspace_navigation_does_not_create_model_runs(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()

    for page in WORKSPACE_PAGES:
        app = open_workspace_page(app, page)
        assert not app.exception
        if page != "Developer Tools":
            assert app.title[0].value == page

    assert not list((tmp_path / "runs").rglob("run.json"))


@pytest.mark.parametrize("page", ["Specifications", "Reports"])
def test_future_journey_steps_are_marked_as_unimplemented(page: str) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_workspace_page(app, page)

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
    app = app.selectbox(key="capability_test_id").set_value("pencil_hardness").run()
    app = app.checkbox(key="capability_has_price").check().run()
    app.text_area(key="capability_constraints").set_value("750 g; flat coated panels only.")
    app.text_area(key="capability_notes").set_value("Offline integration test capability.")
    app.text_input(key="capability_price").set_value("125.50")
    app = click_button(app, "Save capability")

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
    assert app.selectbox(key="capability_editor_choice").value == capability.id

    # Repeatedly saving the displayed editor must update, not append a duplicate.
    app = click_button(app, "Save capability")
    assert not app.exception
    resaved = LabProfile.model_validate_json(saved_lab_path.read_text(encoding="utf-8"))
    assert len(resaved.capabilities) == len(saved.capabilities)

    reopened = AppTest.from_file(str(APP), default_timeout=10).run()
    reopened = (
        reopened.selectbox(key="capability_editor_choice").set_value(capability.id).run()
    )

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
    reopened = click_button(reopened, "Save capability")

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

    app = app.checkbox(key="capability_has_price").check().run()
    app.text_input(key="capability_price").set_value("not-a-price")
    app = click_button(app, "Save capability")

    assert not app.exception
    assert app.error
    assert saved_lab_path.read_bytes() == original


def test_lab_can_declare_test_without_claiming_a_method(tmp_path: Path) -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = app.selectbox(key="capability_test_id").set_value("pencil_hardness").run()
    app.multiselect(key="capability_method_ids").set_value([])
    app.text_area(key="capability_notes").set_value("Method needs confirmation.")
    app = click_button(app, "Save capability")

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
    app = app.selectbox(key="capability_editor_choice").set_value("cap_pencil").run()
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
    app = click_button(app, "Save capability")

    assert not app.exception
    assert any("lab has changed" in error.value.lower() for error in app.error)
    assert store.path.read_bytes() == external_saved
    assert next(
        item for item in store.load_or_create().capabilities if item.id == "cap_pencil"
    ).notes == "Changed by an external editor."


def test_saved_capability_can_be_reopened_after_workspace_navigation() -> None:
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = app.selectbox(key="capability_editor_choice").set_value("cap_cross_cut").run()
    app.text_area(key="capability_notes").set_value("Persisted before changing workspace page.")
    app = click_button(app, "Save capability")
    app = open_workspace_page(app, "Test Catalog")
    app = open_workspace_page(app, "My Lab")
    app = app.selectbox(key="capability_editor_choice").set_value("cap_cross_cut").run()

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

    for label in ("Save lab details", "Save capability", "Save capability", "Save lab details"):
        app = click_button(app, label)
        assert not app.exception
        assert not app.error
        assert app.success
        assert not any("Reload before saving" in warning.value for warning in app.warning)


def test_web_app_renders_empty_state(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCIONE_RUNS_DIR", str(tmp_path))

    app = AppTest.from_file(str(APP), default_timeout=10).run()
    app = open_workspace_page(app, "Developer Tools")

    assert not app.exception
    assert app.title[0].value == "Test Specification Intelligence"
    assert any("first reviewable extraction" in info.value for info in app.info)


def test_web_app_renders_saved_run(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    execute_method_workbench(
        SAMPLE_PDF,
        provider=StaticModelProvider.from_json_file(GROUND_TRUTH),
        runs_dir=tmp_path,
        ground_truth_path=GROUND_TRUTH,
    )
    monkeypatch.setenv("SCIONE_RUNS_DIR", str(tmp_path))

    app = AppTest.from_file(str(APP), default_timeout=15).run()
    app = open_workspace_page(app, "Developer Tools")

    assert not app.exception
    assert [tab.label for tab in app.tabs] == [
        "Model output",
        "Evidence & source",
        "Results vs truth",
        "Compare runs",
        "Raw JSON & metadata",
    ]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["PDF pages"] == "4"
    assert metrics["Methods"] == "2"
    assert metrics["Diagnostic F1"] == "1.000"

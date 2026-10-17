"""Customer-document journey with local fixtures and no external model requests."""

from pathlib import Path

import pymupdf
import pytest
from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from scione.config import Settings
from scione.providers import StaticModelProvider
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.specifications import CustomerRequirementRunner
from scione.specifications.store import FileSpecStore

REPOSITORY_ROOT = Path(__file__).parents[2]
APP = REPOSITORY_ROOT / "src" / "scione" / "web" / "app.py"
FIRST_PAGE = (
    "Fictional customer specification. Pencil hardness shall be tested using ISO 15184. "
    "Apply a 750 g load. The required pencil hardness is at least F. "
    "This synthetic document is only an offline test fixture."
)
SECOND_PAGE = (
    "Scope and applicability. The pencil hardness request applies to flat coated panels. "
    "No material approval, lab capability, or method equivalence is asserted by this fixture."
)


@pytest.fixture(autouse=True)
def offline_specification_workspace(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Never read developer credentials, saved runs, or the real client document."""

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))

    def reject_model_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Specification UI tests must never call an external model provider.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)


@pytest.fixture
def synthetic_pdf() -> bytes:
    return make_pdf(FIRST_PAGE, SECOND_PAGE)


def make_pdf(*pages: str) -> bytes:
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 400), text, fontsize=11)
        return document.tobytes()


@pytest.fixture
def seeded_store(tmp_path: Path, synthetic_pdf: bytes) -> FileSpecStore:
    store = FileSpecStore(tmp_path / "workspace")
    store.import_pdf("fictional-customer.pdf", synthetic_pdf)
    return store


def open_specifications() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    return app.sidebar.button(key="nav_specifications").click().run()


def click_button(app: AppTest, label: str) -> AppTest:
    return next(button for button in app.button if button.label == label).click().run()


def customer_payload(*, needs_attention: bool = False, second_item: bool = False) -> dict:
    first = {
        "id": "req_pencil",
        "original_test_name": "Pencil hardness",
        "requested_methods": [{"code": "ISO 15184"}],
        "conditions": [{"name": "Load", "raw_value": "750", "unit": "g"}],
        "acceptance_criteria": [{"name": "Pencil hardness", "raw_value": "at least F"}],
        "applicability": ["flat coated panels"],
        "evidence": [
            {"page": 1, "quote": "Pencil hardness shall be tested using ISO 15184."},
            {"page": 2, "quote": "The pencil hardness request applies to flat coated panels."},
        ],
    }
    rows = [first]
    issues = ["req_pencil: applicability needs checking."] if needs_attention else []
    if second_item:
        rows.append(
            {
                "id": "req_panels",
                "original_test_name": "Coated panel applicability",
                "applicability": ["flat coated panels"],
                "evidence": [
                    {
                        "page": 2,
                        "quote": "The pencil hardness request applies to flat coated panels.",
                    }
                ],
            }
        )
    return {
        "schema_version": "0.1",
        "document": {"title": "Fictional customer specification"},
        "requirements": rows,
        "review_issues": issues,
    }


def save_static_extraction(
    store: FileSpecStore, document_id: str, *, requirement_id="req_pencil", payload=None
):
    payload = payload or customer_payload()
    payload["requirements"][0]["id"] = requirement_id
    document = store.load_document(document_id).document
    run = CustomerRequirementRunner(StaticModelProvider(payload)).run(document)
    store.save_extraction(document_id, run)
    return run


def select_requirement(
    app: AppTest, _store: FileSpecStore, requirement_id: str, *, attention: bool = False
) -> AppTest:
    """Click a real list entry, including entries inside the All tests expander."""

    prefix = "spec_attention" if attention else "spec_all"
    return app.button(key=f"{prefix}_open_{requirement_id}").click().run()


def open_for_review(app: AppTest, store: FileSpecStore, requirement_id="req_pencil") -> AppTest:
    app = select_requirement(app, store, requirement_id)
    optional_action = next(
        (button for button in app.button if button.key == "spec_review_item"), None
    )
    return optional_action.click().run() if optional_action else app


def use_whole_document(app: AppTest) -> AppTest:
    # These static fixtures return the detailed schema, not the compact API schema.
    return app.selectbox(key="spec_extraction_strategy").set_value("whole_document").run()


def test_empty_state_renders_without_credentials_and_never_extracts(tmp_path: Path) -> None:
    app = open_specifications()
    assert not app.exception
    assert app.title[0].value == "Specifications"
    assert app.get("file_uploader")
    assert not FileSpecStore(tmp_path / "workspace").list_documents()
    assert not any(button.key == "spec_extract_requirements" for button in app.button)


def test_demo_import_is_explicit_and_persists_across_sessions(
    monkeypatch: MonkeyPatch, tmp_path: Path, synthetic_pdf: bytes
) -> None:
    from scione.web import specifications

    sample = tmp_path / "offline-demo.pdf"
    sample.write_bytes(synthetic_pdf)
    monkeypatch.setattr(specifications, "SAMPLE_SPEC_PATH", sample)
    app = open_specifications()
    store = FileSpecStore(tmp_path / "workspace")
    assert not store.list_documents()
    app = app.button(key="spec_import_sample").click().run()
    assert not app.exception
    saved = store.list_documents()
    assert len(saved) == 1
    assert store.read_pdf(saved[0].document_id) == synthetic_pdf
    assert saved[0].original_name == "offline-demo.pdf"
    assert saved[0].latest_run_id is None
    reopened = open_specifications()
    assert not reopened.exception
    assert reopened.selectbox(key="spec_document_choice").value == saved[0].document_id
    assert len(store.list_documents()) == 1


def test_saved_pdf_can_be_paged_without_model_inference(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    app = open_specifications()
    assert not app.exception
    assert app.selectbox(key="spec_document_choice").value == document_id
    assert app.number_input(key="spec_pdf_page").value == 1
    assert app.button(key="spec_extract_requirements").disabled
    app = click_button(app, "Next page")
    assert not app.exception
    assert app.number_input(key="spec_pdf_page").value == 2
    app = click_button(app, "Previous page")
    assert not app.exception
    assert app.number_input(key="spec_pdf_page").value == 1
    assert seeded_store.load_latest_extraction(document_id) is None


def test_ready_rows_are_listed_without_proactive_confirmation(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(seeded_store, document_id)
    app = open_specifications()
    assert not app.exception
    assert app.session_state["spec_requirement_choice"] is None
    assert not any(button.key == "spec_confirm_requirement" for button in app.button)
    assert app.button(key="spec_all_open_req_pencil")
    app = select_requirement(app, seeded_store, "req_pencil")
    assert not app.exception
    assert app.button(key="spec_review_item")
    assert not any(button.key == "spec_confirm_requirement" for button in app.button)
    assert seeded_store.load_review(document_id, run.run_id).version == 0


def test_continue_to_lab_review_preserves_source_checks_and_document(
    seeded_store: FileSpecStore,
) -> None:
    from scione.reports.store import FileReportStore

    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True)
    )
    before = seeded_store.load_review(document_id, run.run_id)
    app = open_specifications()
    app = app.button(key="spec_continue_reports").click().run()
    assert not app.exception
    assert app.title[0].value == "Reports"
    assert app.selectbox(key="report_document").value == document_id
    assert app.session_state["report_view_tabs"] == "Review & confirm"
    assert seeded_store.load_review(document_id, run.run_id) == before
    assert seeded_store.load_extraction(document_id, run.run_id) == run
    reports = FileReportStore(seeded_store.root.parent)
    assert reports.list_reports() == []


def test_list_selection_opens_first_evidence_page_and_relocate_returns_to_source(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    payload = customer_payload()
    payload["requirements"][0]["evidence"].reverse()
    run = save_static_extraction(seeded_store, document_id, payload=payload)
    app = open_specifications()
    assert app.number_input(key="spec_pdf_page").value == 1
    app = select_requirement(app, seeded_store, "req_pencil")
    assert not app.exception
    assert app.number_input(key="spec_pdf_page").value == 2
    app = app.number_input(key="spec_pdf_page").set_value(1).run()
    app = app.button(key="spec_relocate").click().run()
    assert not app.exception
    assert app.number_input(key="spec_pdf_page").value == 2
    assert seeded_store.load_review(document_id, run.run_id).version == 0


def test_clicking_nested_all_tests_entries_updates_page_and_highlight_without_inference(
    monkeypatch: MonkeyPatch, seeded_store: FileSpecStore
) -> None:
    from scione.web import specifications

    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(second_item=True)
    )
    previews = []
    original_preview = specifications._preview_image

    def capture_preview(pdf: bytes, page: int, quotes: tuple[str, ...] = ()) -> bytes:
        previews.append((page, quotes))
        return original_preview(pdf, page, quotes)

    monkeypatch.setattr(specifications, "_preview_image", capture_preview)
    app = open_specifications()
    assert previews[-1] == (1, ())
    app = select_requirement(app, seeded_store, "req_pencil")
    assert not app.exception
    assert previews[-1] == (1, ("Pencil hardness shall be tested using ISO 15184.",))
    app = select_requirement(app, seeded_store, "req_panels")
    assert not app.exception
    assert app.number_input(key="spec_pdf_page").value == 2
    assert previews[-1] == (2, ("The pencil hardness request applies to flat coated panels.",))
    app = app.number_input(key="spec_pdf_page").set_value(1).run()
    assert previews[-1] == (1, ())
    app = select_requirement(app, seeded_store, "req_pencil")
    assert previews[-1] == (1, ("Pencil hardness shall be tested using ISO 15184.",))
    assert seeded_store.load_latest_extraction(document_id).run_id == run.run_id
    assert seeded_store.load_review(document_id, run.run_id).version == 0


def test_attention_list_entry_click_opens_details_and_its_highlighted_page(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(second_item=True)
    )
    app = open_specifications()
    assert app.session_state["spec_requirement_choice"] is None
    assert app.number_input(key="spec_pdf_page").value == 1

    app = app.button(key="spec_attention_open_req_panels").click().run()

    assert not app.exception
    assert app.session_state["spec_requirement_choice"] == "req_panels"
    assert app.number_input(key="spec_pdf_page").value == 2
    assert app.button(key="spec_confirm_requirement")
    assert any("highlighted" in caption.value for caption in app.caption)
    assert seeded_store.load_review(document_id, run.run_id).version == 0


def test_back_to_list_clears_focused_details_without_confirming_or_changing_pdf_page(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    app = open_specifications()
    app = app.button(key="spec_attention_open_req_panels").click().run()
    assert not app.exception
    assert app.button(key="spec_back_to_list")
    assert app.button(key="spec_confirm_requirement")
    assert not any(
        button.key and button.key.startswith("spec_attention_open_") for button in app.button
    )
    assert app.button(key="spec_all_open_req_pencil")

    app = app.button(key="spec_back_to_list").click().run()

    assert not app.exception
    assert app.session_state["spec_requirement_choice"] is None
    assert app.number_input(key="spec_pdf_page").value == 2
    assert app.button(key="spec_attention_open_req_pencil")
    assert app.button(key="spec_attention_open_req_panels")
    assert not any(button.key == "spec_confirm_requirement" for button in app.button)
    assert app.button(key="spec_back_to_list").disabled
    assert seeded_store.load_review(document_id, run.run_id).version == 0

    app = app.button(key="spec_attention_open_req_pencil").click().run()
    assert not app.exception
    assert app.session_state["spec_requirement_choice"] == "req_pencil"
    assert app.number_input(key="spec_pdf_page").value == 1
    assert app.button(key="spec_confirm_requirement")


def test_changing_documents_clears_selection_and_keeps_separate_review_notes(
    seeded_store: FileSpecStore,
) -> None:
    first_id = seeded_store.list_documents()[0].document_id
    first_run = save_static_extraction(seeded_store, first_id)
    first_review = seeded_store.save_review(
        first_id,
        first_run.run_id,
        "req_pencil",
        decision="confirmed",
        note="Checked the first document only.",
        expected_version=0,
    )
    second = seeded_store.import_pdf(
        "second-customer.pdf", make_pdf(FIRST_PAGE, SECOND_PAGE + " Second specimen series.")
    )
    second_run = save_static_extraction(
        seeded_store, second.document_id, requirement_id="req_second"
    )
    second_review = seeded_store.save_review(
        second.document_id,
        second_run.run_id,
        "req_second",
        decision="needs_review",
        note="Separate second-document scope check.",
        expected_version=0,
    )
    app = open_specifications()
    app = app.selectbox(key="spec_document_choice").set_value(first_id).run()
    app = open_for_review(app, seeded_store)
    assert app.text_area(key="spec_review_note").value == "Checked the first document only."
    app = app.number_input(key="spec_pdf_page").set_value(2).run()
    app = app.selectbox(key="spec_document_choice").set_value(second.document_id).run()
    assert not app.exception
    assert app.session_state["spec_requirement_choice"] is None
    assert app.number_input(key="spec_pdf_page").value == 1
    app = select_requirement(app, seeded_store, "req_second")
    assert app.text_area(key="spec_review_note").value == "Separate second-document scope check."
    assert seeded_store.load_review(first_id, first_run.run_id) == first_review
    assert seeded_store.load_review(second.document_id, second_run.run_id) == second_review


def test_confirm_advances_to_next_attention_item_and_does_not_look_stuck(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    original_json = run.extraction.model_dump_json()
    app = select_requirement(open_specifications(), seeded_store, "req_pencil", attention=True)
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert app.get("toast")
    assert not app.success  # Feedback must not insert a new block above the workspace.
    assert app.session_state["spec_requirement_choice"] == "req_panels"
    assert app.number_input(key="spec_pdf_page").value == 2
    assert (
        seeded_store.load_review(document_id, run.run_id).decisions["req_pencil"].status
        == "confirmed"
    )
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert app.session_state["spec_requirement_choice"] == "req_panels"
    assert not any(button.key == "spec_confirm_requirement" for button in app.button)
    assert (
        seeded_store.load_extraction(document_id, run.run_id).extraction.model_dump_json()
        == original_json
    )


def test_exclusion_persists_without_deleting_extracted_row(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True)
    )
    app = select_requirement(open_specifications(), seeded_store, "req_pencil")
    app.text_area(key="spec_review_note").set_value("Not an applicable customer request.")
    app = app.button(key="spec_exclude_requirement").click().run()
    assert not app.exception
    assert app.session_state["spec_requirement_choice"] == "req_pencil"
    assert not any(button.key == "spec_confirm_requirement" for button in app.button)
    review = seeded_store.load_review(document_id, run.run_id)
    assert review.decisions["req_pencil"].status == "excluded"
    assert review.decisions["req_pencil"].note == "Not an applicable customer request."
    reopened = open_specifications()
    assert not reopened.exception
    assert "Excluded" in reopened.button(key="spec_all_open_req_pencil").label
    assert (
        seeded_store.load_latest_extraction(document_id).extraction.requirements[0].id
        == "req_pencil"
    )


def test_continuous_review_advances_after_current_record_before_wrapping(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    payload = customer_payload(needs_attention=True, second_item=True)
    third = dict(payload["requirements"][1])
    third.update(id="req_third", original_test_name="Third applicability check")
    payload["requirements"].append(third)
    run = save_static_extraction(seeded_store, document_id, payload=payload)
    app = select_requirement(open_specifications(), seeded_store, "req_panels", attention=True)
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert app.session_state["spec_requirement_choice"] == "req_third"
    assert app.button(key="spec_confirm_requirement").label == "Confirm & next"
    assert not app.success
    app = app.button(key="spec_confirm_requirement").click().run()
    assert app.session_state["spec_requirement_choice"] == "req_pencil"
    assert seeded_store.load_review(document_id, run.run_id).version == 2


def test_bulk_source_confirmation_requires_ack_and_is_one_atomic_action(
    seeded_store: FileSpecStore,
) -> None:
    from scione.reports.store import FileReportStore

    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    original = run.model_dump_json()
    app = open_specifications()
    assert app.button(key="spec_bulk_confirm").disabled
    app = app.multiselect(key="spec_bulk_selection").set_value(["req_pencil", "req_panels"]).run()
    assert app.button(key="spec_bulk_confirm").disabled
    app = app.checkbox(key="spec_bulk_acknowledge").check().run()
    app = app.button(key="spec_bulk_confirm").click().run()
    assert not app.exception
    assert app.get("toast")
    assert not app.success
    assert app.multiselect(key="spec_bulk_selection").value == []
    review = seeded_store.load_review(document_id, run.run_id)
    assert review.version == 1
    assert {item.status for item in review.decisions.values()} == {"confirmed"}
    assert seeded_store.load_extraction(document_id, run.run_id).model_dump_json() == original
    assert (
        FileReportStore(seeded_store.root.parent).load_workspace(document_id, run.run_id).version
        == 0
    )


def test_bulk_exclusion_requires_reason_and_preserves_original_records(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    app = open_specifications()
    app = app.multiselect(key="spec_bulk_selection").set_value(["req_pencil", "req_panels"]).run()
    app = app.checkbox(key="spec_bulk_acknowledge").check().run()
    assert app.button(key="spec_bulk_exclude").disabled
    app = (
        app.text_area(key="spec_bulk_note").set_value("Outside the present customer request.").run()
    )
    app = app.button(key="spec_bulk_exclude").click().run()
    assert not app.exception
    review = seeded_store.load_review(document_id, run.run_id)
    assert review.version == 1
    assert len(review.decisions) == 2
    assert {item.status for item in review.decisions.values()} == {"excluded"}
    assert {item.note for item in review.decisions.values()} == {
        "Outside the present customer request."
    }
    assert len(seeded_store.load_extraction(document_id, run.run_id).extraction.requirements) == 2


def test_bulk_selection_change_resets_acknowledgement(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    app = open_specifications()
    app = app.multiselect(key="spec_bulk_selection").set_value(["req_pencil"]).run()
    app = app.checkbox(key="spec_bulk_acknowledge").check().run()
    assert not app.button(key="spec_bulk_confirm").disabled
    app = app.multiselect(key="spec_bulk_selection").set_value(["req_pencil", "req_panels"]).run()
    assert not app.checkbox(key="spec_bulk_acknowledge").value
    assert app.button(key="spec_bulk_confirm").disabled


def test_stale_bulk_cannot_overwrite_another_review(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    app = open_specifications()
    app = app.multiselect(key="spec_bulk_selection").set_value(["req_pencil", "req_panels"]).run()
    app = app.checkbox(key="spec_bulk_acknowledge").check().run()
    other = seeded_store.save_review(
        document_id, run.run_id, "req_panels", "needs_review", "Another reviewer."
    )
    app = app.button(key="spec_bulk_confirm").click().run()
    assert not app.exception
    assert any("review changed" in error.value.lower() for error in app.error)
    assert app.button(key="spec_bulk_confirm").disabled
    assert seeded_store.load_review(document_id, run.run_id) == other
    app = app.button(key="spec_bulk_reload").click().run()
    assert app.multiselect(key="spec_bulk_selection").value == []
    assert not app.checkbox(key="spec_bulk_acknowledge").value


def test_new_extraction_invalidates_entire_bulk_selection(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    old = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True, second_item=True)
    )
    app = open_specifications()
    app = app.multiselect(key="spec_bulk_selection").set_value(["req_pencil", "req_panels"]).run()
    app = app.checkbox(key="spec_bulk_acknowledge").check().run()
    payload = customer_payload(needs_attention=True, second_item=True)
    payload["requirements"][0]["conditions"][0]["raw_value"] = "1000"
    new = save_static_extraction(seeded_store, document_id, payload=payload)
    app = app.button(key="spec_bulk_confirm").click().run()
    assert not app.exception
    assert any("extraction changed" in error.value.lower() for error in app.error)
    assert seeded_store.load_review(document_id, old.run_id).version == 0
    assert seeded_store.load_review(document_id, new.run_id).version == 0
    assert app.multiselect(key="spec_bulk_selection").value == []


def test_gemini_requires_permission_and_separate_free_tier_confirmation(
    monkeypatch: MonkeyPatch, seeded_store: FileSpecStore
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test-not-a-real-key")
    app = open_specifications()
    app = app.selectbox(key="spec_provider").set_value("gemini").run()
    assert not app.exception
    assert app.button(key="spec_extract_requirements").disabled
    app = app.checkbox(key="spec_external_permission").check().run()
    assert app.button(key="spec_extract_requirements").disabled
    app = app.checkbox(key="spec_free_tier_confirmed").check().run()
    assert not app.button(key="spec_extract_requirements").disabled
    assert seeded_store.list_documents()[0].latest_run_id is None


@pytest.mark.parametrize(
    "changed_context", ["document", "provider", "model", "strategy", "output_limit"]
)
def test_external_consent_does_not_carry_over_to_a_different_request(
    monkeypatch: MonkeyPatch, seeded_store: FileSpecStore, changed_context: str
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-not-a-real-key")
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test-not-a-real-key")
    first_id = seeded_store.list_documents()[0].document_id
    second = seeded_store.import_pdf(
        "another-request.pdf", make_pdf(FIRST_PAGE, SECOND_PAGE + " Another request.")
    )
    app = open_specifications()
    app = app.selectbox(key="spec_document_choice").set_value(first_id).run()
    app = app.selectbox(key="spec_provider").set_value("gemini").run()
    app = app.checkbox(key="spec_external_permission").check().run()
    app = app.checkbox(key="spec_free_tier_confirmed").check().run()
    assert not app.button(key="spec_extract_requirements").disabled
    if changed_context == "document":
        app = app.selectbox(key="spec_document_choice").set_value(second.document_id).run()
    elif changed_context == "provider":
        app = app.selectbox(key="spec_provider").set_value("groq").run()
    elif changed_context == "model":
        app = app.text_input(key="spec_model_gemini").set_value("offline-changed-model").run()
    elif changed_context == "strategy":
        app = app.selectbox(key="spec_extraction_strategy").set_value("focused_passes").run()
    else:
        token_limit = app.number_input(key="spec_output_tokens_gemini").value
        app = app.number_input(key="spec_output_tokens_gemini").set_value(token_limit + 128).run()
    assert not app.exception
    assert not app.checkbox(key="spec_external_permission").value
    assert app.button(key="spec_extract_requirements").disabled
    if changed_context != "provider":
        assert not app.checkbox(key="spec_free_tier_confirmed").value
    assert seeded_store.load_latest_extraction(first_id) is None
    assert seeded_store.load_latest_extraction(second.document_id) is None


def test_invalid_model_result_is_not_accepted_as_completed_analysis(
    monkeypatch: MonkeyPatch, seeded_store: FileSpecStore
) -> None:
    from scione.web import specifications

    document_id = seeded_store.list_documents()[0].document_id
    source = seeded_store.read_pdf(document_id)
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-not-a-real-key")
    monkeypatch.setattr(
        specifications,
        "create_configured_provider",
        lambda *args, **kwargs: StaticModelProvider({"schema_version": "0.1"}),
    )
    app = use_whole_document(open_specifications())
    app = app.checkbox(key="spec_external_permission").check().run()
    app = app.button(key="spec_extract_requirements").click().run()
    assert not app.exception
    assert app.error
    assert seeded_store.load_latest_extraction(document_id) is None
    assert seeded_store.read_pdf(document_id) == source


def test_failed_reanalysis_shows_retained_old_result_and_persists_failure_notice(
    monkeypatch: MonkeyPatch, seeded_store: FileSpecStore
) -> None:
    from scione.web import specifications

    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(seeded_store, document_id)
    original_json = run.model_dump_json()
    review = seeded_store.save_review(
        document_id,
        run.run_id,
        "req_pencil",
        decision="confirmed",
        note="Previously confirmed.",
        expected_version=0,
    )
    monkeypatch.setenv("GROQ_API_KEY", "offline-test-not-a-real-key")
    monkeypatch.setattr(
        specifications,
        "create_configured_provider",
        lambda *args, **kwargs: StaticModelProvider({"schema_version": "0.1"}),
    )
    app = use_whole_document(open_specifications())
    app = app.checkbox(key="spec_external_permission").check().run()
    app = app.button(key="spec_extract_requirements").click().run()
    assert not app.exception
    assert any(
        "previous" in error.value.lower() and "not a new result" in error.value
        for error in app.error
    )
    assert seeded_store.load_latest_extraction(document_id).model_dump_json() == original_json
    assert seeded_store.load_review(document_id, run.run_id) == review
    failure = seeded_store.load_failure(document_id)
    assert failure is not None
    assert failure.previous_run_id == run.run_id
    reopened = open_specifications()
    assert not reopened.exception
    assert any("not a new result" in error.value for error in reopened.error)


def test_stale_review_cannot_overwrite_another_reviewer(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    run = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True)
    )
    app = select_requirement(open_specifications(), seeded_store, "req_pencil")
    external = seeded_store.save_review(
        document_id,
        run.run_id,
        "req_pencil",
        decision="needs_review",
        note="Another reviewer's scope issue.",
        expected_version=0,
    )
    app.text_area(key="spec_review_note").set_value("Stale browser note.")
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert any("review changed" in error.value.lower() for error in app.error)
    assert seeded_store.load_review(document_id, run.run_id) == external
    assert app.text_area(key="spec_review_note").value == "Stale browser note."
    app = click_button(app, "Reload saved review (discard note draft)")
    assert not app.exception
    assert app.text_area(key="spec_review_note").value == "Another reviewer's scope issue."


def test_stale_confirm_cannot_approve_unseen_new_run_with_same_id(
    seeded_store: FileSpecStore,
) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    old = save_static_extraction(
        seeded_store, document_id, payload=customer_payload(needs_attention=True)
    )
    app = select_requirement(open_specifications(), seeded_store, "req_pencil")
    app.text_area(key="spec_review_note").set_value("Reviewing the original result.")
    changed = customer_payload(needs_attention=True)
    changed["requirements"][0]["conditions"][0]["raw_value"] = "1000"
    new = save_static_extraction(seeded_store, document_id, payload=changed)
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert any("extraction changed" in error.value.lower() for error in app.error)
    assert seeded_store.load_review(document_id, old.run_id).version == 0
    assert seeded_store.load_review(document_id, new.run_id).version == 0
    app = select_requirement(app, seeded_store, "req_pencil")
    app.text_area(key="spec_review_note").set_value("Checked the new result.")
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    confirmed = seeded_store.load_review(document_id, new.run_id)
    assert confirmed.version == 1
    assert confirmed.decisions["req_pencil"].note == "Checked the new result."
    assert seeded_store.load_review(document_id, old.run_id).version == 0


def test_explicit_extraction_and_review_never_rewrite_source_or_model_result(
    monkeypatch: MonkeyPatch, seeded_store: FileSpecStore
) -> None:
    from scione.web import specifications

    document_id = seeded_store.list_documents()[0].document_id
    original_pdf = seeded_store.read_pdf(document_id)
    calls = []

    class CountingProvider(StaticModelProvider):
        def generate_structured(self, request):
            calls.append(request)
            return super().generate_structured(request)

    monkeypatch.setenv("GROQ_API_KEY", "offline-test-not-a-real-key")
    monkeypatch.setattr(
        specifications,
        "create_configured_provider",
        lambda *args, **kwargs: CountingProvider(customer_payload()),
    )
    app = use_whole_document(open_specifications())
    assert not calls
    app = app.checkbox(key="spec_external_permission").check().run()
    assert not calls
    app = app.button(key="spec_extract_requirements").click().run()
    assert not app.exception
    assert len(calls) == 1
    run = seeded_store.load_latest_extraction(document_id)
    assert run is not None
    original_json = run.model_dump_json()
    app = open_for_review(app, seeded_store)
    app.text_area(key="spec_review_note").set_value("Checked both source pages.")
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert len(calls) == 1
    assert (
        seeded_store.load_review(document_id, run.run_id).decisions["req_pencil"].status
        == "confirmed"
    )
    assert seeded_store.read_pdf(document_id) == original_pdf
    reopened = open_specifications()
    assert not reopened.exception
    assert len(calls) == 1
    assert seeded_store.load_latest_extraction(document_id).model_dump_json() == original_json


def test_focused_passes_only_make_declared_calls_after_explicit_consent(
    monkeypatch: MonkeyPatch,
    seeded_store: FileSpecStore,
) -> None:
    from scione.web import specifications

    stored = seeded_store.import_pdf(
        "seven-page-fictional-spec.pdf",
        make_pdf(
            FIRST_PAGE,
            SECOND_PAGE,
            *[f"Synthetic supporting context on page {page}." for page in range(3, 8)],
        ),
    )
    calls = []

    class CountingProvider(StaticModelProvider):
        def generate_structured(self, request):
            calls.append(request)
            return super().generate_structured(request)

    monkeypatch.setenv("GROQ_API_KEY", "offline-test-not-a-real-key")
    monkeypatch.setattr(
        specifications,
        "create_configured_provider",
        lambda *args, **kwargs: CountingProvider(customer_payload(needs_attention=True)),
    )
    app = open_specifications()
    app = app.selectbox(key="spec_document_choice").set_value(stored.document_id).run()
    app = app.selectbox(key="spec_extraction_strategy").set_value("focused_passes").run()
    assert not app.exception
    assert not calls
    assert any("Up to 3 requests" in caption.value for caption in app.caption)
    app = app.checkbox(key="spec_external_permission").check().run()
    app = app.button(key="spec_extract_requirements").click().run()
    assert not app.exception
    assert len(calls) == 3
    run = seeded_store.load_latest_extraction(stored.document_id)
    assert run.strategy == "focused_passes"
    assert [call.focus_pages for call in run.calls] == [[1, 2, 3], [4, 5, 6], [7]]
    assert all(FIRST_PAGE in " ".join(request.user_prompt.split()) for request in calls)
    assert all(
        "Synthetic supporting context on page 7." in request.user_prompt for request in calls
    )
    app = select_requirement(app, seeded_store, run.extraction.requirements[0].id)
    app = app.button(key="spec_confirm_requirement").click().run()
    assert not app.exception
    assert len(calls) == 3


def test_old_result_keeps_document_coverage_warning_independent_of_row_readiness(
    seeded_store: FileSpecStore,
) -> None:
    stored = seeded_store.import_pdf(
        "fictional-spec-additional-method.pdf",
        make_pdf(FIRST_PAGE, SECOND_PAGE + " Cross-cut adhesion also uses ISO 2409."),
    )
    save_static_extraction(seeded_store, stored.document_id)
    app = open_specifications()
    app = app.selectbox(key="spec_document_choice").set_value(stored.document_id).run()
    assert not app.exception
    assert any(
        warning.value == "Draft: some tests may still be missing." for warning in app.warning
    )
    assert not any(button.key == "spec_confirm_requirement" for button in app.button)


def test_raw_model_output_is_separate_from_customer_review(seeded_store: FileSpecStore) -> None:
    document_id = seeded_store.list_documents()[0].document_id
    save_static_extraction(seeded_store, document_id)
    app = open_specifications()
    assert not app.exception
    assert not any("raw" in element.label.lower() for element in app.get("download_button"))
    assert not app.json
    app = app.sidebar.button(key="nav_developer_tools").click().run()
    assert not app.exception
    assert any("Customer-spec diagnostics" in heading.value for heading in app.subheader)
    assert app.json

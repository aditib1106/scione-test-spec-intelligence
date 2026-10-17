"""Saved customer-extraction diagnostics, isolated from real documents and APIs."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pymupdf
import pytest
import streamlit as st
from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import ExtractionFailure, FileSpecStore

APP = Path(__file__).parents[2] / "src" / "scione" / "web" / "app.py"
FIRST_PAGE = "Fictional test request. Pencil hardness: ISO 15184, 750 g load; at least F."
SECOND_PAGE = "Synthetic second request. Cross-cut adhesion: ISO 2409, 2 mm spacing; GT 0 to 1."
TABS = ["Overview", "Requirements", "Evidence & checks", "Compare runs", "Raw & metadata"]


@pytest.fixture(autouse=True)
def offline_dashboard(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Never read local credentials or send a request while browsing diagnostics."""

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))

    def reject_model_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Developer-dashboard inspection must never call an external model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)


def make_pdf(*pages: str) -> bytes:
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_textbox(pymupdf.Rect(40, 40, 550, 400), text, fontsize=11)
        return document.tobytes()


@pytest.fixture
def store(tmp_path: Path) -> FileSpecStore:
    return FileSpecStore(tmp_path / "workspace")


def make_run(
    document_id: str, *, old: bool = False, focused: bool = False
) -> CustomerExtractionRun:
    """Intentionally preserve noncanonical spacing in the provider's actual text."""

    provider = "groq" if old else "gemini"
    model = "offline-older-model" if old else "offline-newer-model"
    raw = ' \n{ "source" : "old fixture" }\n  ' if old else '\n { "source" : "new fixture" } \n'
    data: dict[str, Any] = {
        "run_id": "older-fixture-run" if old else "newer-fixture-run",
        "started_at": datetime(2026, 10, 17, 12, tzinfo=UTC) + timedelta(hours=not old),
        "document_id": document_id,
        "strategy": "whole_document",
        "prompt_version": "customer_requirements_offline_fixture",
        "schema_version": "0.1",
        "provider": provider,
        "model": model,
        "latency_ms": 1250,
        "usage": {} if not old else {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160},
        "raw_response": raw,
        "extraction": {
            "schema_version": "0.1",
            "document": {"title": "Offline synthetic request", "identifier": "FIXTURE-01"},
            "requirements": [
                {
                    "id": "req_pencil",
                    "original_test_name": "Pencil hardness",
                    "requested_methods": [{"code": "ISO 15184"}],
                    "conditions": [{"name": "Load", "raw_value": "750", "unit": "g"}],
                    "acceptance_criteria": [{"name": "Pencil hardness", "raw_value": "at least F"}],
                    "evidence": [
                        {"page": 1, "quote": "Pencil hardness: ISO 15184, 750 g load; at least F."}
                    ],
                },
                {
                    "id": "req_adhesion",
                    "original_test_name": "Cross-cut adhesion",
                    "requested_methods": [{"code": "ISO 2409"}],
                    "conditions": [{"name": "Spacing", "raw_value": "2", "unit": "mm"}],
                    "acceptance_criteria": [{"name": "Classification", "raw_value": "GT 0 to 1"}],
                    "evidence": [
                        {
                            "page": 2,
                            "quote": "Cross-cut adhesion: ISO 2409, 2 mm spacing; GT 0 to 1.",
                        }
                    ],
                },
            ],
            "review_issues": [],
        },
    }
    if old:
        data["extraction"]["requirements"] = data["extraction"]["requirements"][:1]
    if focused:
        data.update(
            strategy="focused_passes",
            raw_response_kind="focused_pass_manifest",
            raw_response=(
                ' \n{"kind":"focused_pass_manifest","calls":['
                '{"call_index":1,"focus_pages":[1]},'
                '{"call_index":2,"focus_pages":[2]}]}\n '
            ),
            calls=[
                {
                    "call_index": index,
                    "focus_pages": [index],
                    "provider": provider,
                    "model": model,
                    "latency_ms": 625,
                    "usage": {},
                    "raw_response": f' \n{{"fixture_call": {index}}}\n  ',
                }
                for index in (1, 2)
            ],
        )
    return CustomerExtractionRun.model_validate(data)


def seed_history(store: FileSpecStore, *, focused: bool = False):
    document = store.import_pdf("fictional-two-page.pdf", make_pdf(FIRST_PAGE, SECOND_PAGE))
    older = make_run(document.document_id, old=True)
    newer = make_run(document.document_id, focused=focused)
    store.save_extraction(document.document_id, older)
    store.save_review(
        document.document_id, older.run_id, "req_pencil", "confirmed", "Older review."
    )
    store.save_extraction(document.document_id, newer)
    store.save_review(
        document.document_id, newer.run_id, "req_pencil", "excluded", "Latest review."
    )
    return document, older, newer


def open_dashboard() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    return app.sidebar.button(key="nav_developer_tools").click().run()


def all_text(app: AppTest) -> str:
    """Collect human-facing diagnostics, including expanded tab contents."""

    values = []
    for kind in ("markdown", "caption", "info", "warning", "error", "text", "code", "json"):
        values.extend(str(element.value) for element in app.get(kind))
    for metric in app.metric:
        values.extend((metric.label, str(metric.value)))
    for table in (*app.dataframe, *app.table):
        values.append(table.value.to_string(index=False))
    return "\n".join(values)


def test_empty_dashboard_is_friendly_and_never_starts_a_model(store: FileSpecStore) -> None:
    app = open_dashboard()
    assert not app.exception
    assert app.title[0].value == "Developer Tools"
    assert "No customer documents" in all_text(app)
    assert not app.json
    assert not store.list_documents()
    assert not any(button.key == "spec_extract_requirements" for button in app.button)


def test_document_without_run_offers_source_without_fake_results(store: FileSpecStore) -> None:
    document = store.import_pdf("not-analyzed.pdf", make_pdf(FIRST_PAGE))
    app = open_dashboard()
    assert not app.exception
    assert app.selectbox(key="spec_debug_document").value == document.document_id
    assert not any(select.key == "spec_debug_run" for select in app.selectbox)
    assert FIRST_PAGE in all_text(app)
    assert not store.load_latest_extraction(document.document_id)


def test_saved_runs_default_latest_and_can_inspect_history_without_mutating_reviews(
    store: FileSpecStore,
) -> None:
    document, older, newer = seed_history(store)
    old_review = store.load_review(document.document_id, older.run_id)
    new_review = store.load_review(document.document_id, newer.run_id)
    app = open_dashboard()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == TABS
    assert app.selectbox(key="spec_debug_run").value == newer.run_id
    overview = next(tab for tab in app.tabs if tab.label == "Overview")
    assert f"{newer.provider} / {newer.model}" in all_text(overview)
    assert older.model not in all_text(overview)
    app = app.selectbox(key="spec_debug_run").set_value(older.run_id).run()
    assert not app.exception
    overview = next(tab for tab in app.tabs if tab.label == "Overview")
    assert f"{older.provider} / {older.model}" in all_text(overview)
    assert newer.model not in all_text(overview)
    assert app.selectbox(key="spec_debug_run").value == older.run_id
    assert store.load_latest_extraction(document.document_id).run_id == newer.run_id
    assert store.load_review(document.document_id, older.run_id) == old_review
    assert store.load_review(document.document_id, newer.run_id) == new_review


def test_unknown_token_usage_is_not_reported_as_zero(store: FileSpecStore) -> None:
    seed_history(store)
    app = open_dashboard()
    assert not app.exception
    usage_metrics = [metric for metric in app.metric if "token" in metric.label.lower()]
    assert usage_metrics
    assert any(metric.value == "—" for metric in usage_metrics)
    assert all(metric.value != "0" for metric in usage_metrics)


def test_legacy_focused_run_without_call_artifacts_does_not_invent_one_request(
    store: FileSpecStore,
) -> None:
    document = store.import_pdf("legacy-focused.pdf", make_pdf(FIRST_PAGE, SECOND_PAGE))
    run = make_run(document.document_id, focused=True).model_copy(update={"calls": []})
    store.save_extraction(document.document_id, run)
    app = open_dashboard()
    assert not app.exception
    overview = next(tab for tab in app.tabs if tab.label == "Overview")
    captions = "\n".join(caption.value for caption in overview.caption)
    assert "1 model requests" not in captions
    assert "— model requests" in captions or "model requests —" in captions


def test_requirement_selection_locates_evidence_without_confirming_it(
    monkeypatch: MonkeyPatch, store: FileSpecStore
) -> None:
    from scione.web import developer

    document, _, newer = seed_history(store)
    before = store.load_review(document.document_id, newer.run_id)
    previews = []
    original_preview = developer._page_image

    def capture_preview(pdf: bytes, page: int, quotes: tuple[str, ...]) -> bytes:
        previews.append((pdf, page, quotes))
        return original_preview(pdf, page, quotes)

    monkeypatch.setattr(developer, "_page_image", capture_preview)
    app = open_dashboard()
    app = app.selectbox(key="spec_debug_requirement").set_value("req_adhesion").run()
    assert not app.exception
    assert app.number_input(key="spec_debug_text_page").value == 2
    assert previews[-1] == (
        store.read_pdf(document.document_id),
        2,
        (newer.extraction.requirements[1].evidence[0].quote,),
    )
    text = all_text(app)
    assert "GT 0 to 1" in text
    assert "ISO 2409" in text
    assert store.load_review(document.document_id, newer.run_id) == before


def test_comparison_is_descriptive_not_a_fake_accuracy_score(store: FileSpecStore) -> None:
    seed_history(store)
    app = open_dashboard()
    assert not app.exception
    comparison = next(tab for tab in app.tabs if tab.label == "Compare runs")
    text = all_text(comparison).lower()
    assert "accuracy" in text
    assert "ground truth" in text or "ground-truth" in text
    assert "offline-older-model" in text
    assert "offline-newer-model" in text
    assert "% accuracy" not in text


def test_raw_downloads_preserve_exact_provider_text_and_distinguish_local_manifest(
    monkeypatch: MonkeyPatch, store: FileSpecStore
) -> None:
    _, _, newer = seed_history(store, focused=True)
    downloads: list[tuple[str, object]] = []
    original_download = st.download_button

    def capture_download(label: str, data: Any, *args: Any, **kwargs: Any):
        downloads.append((label, data))
        return original_download(label, data, *args, **kwargs)

    monkeypatch.setattr(st, "download_button", capture_download)
    app = open_dashboard()
    assert not app.exception
    data = [value for _, value in downloads]
    for expected in [newer.raw_response, *(call.raw_response for call in newer.calls)]:
        assert expected in data or expected.encode("utf-8") in data
    assert "manifest" in all_text(app).lower()
    assert "not" in all_text(app).lower()


def test_last_failure_is_separate_from_inspected_successful_run(store: FileSpecStore) -> None:
    document, _, newer = seed_history(store)
    store.save_failure(
        ExtractionFailure(
            document_id=document.document_id,
            occurred_at=datetime(2026, 10, 17, 14, tzinfo=UTC),
            provider="groq",
            model="offline-rejected-model",
            strategy="compact_document",
            output_limit=6000,
            message="Offline fixture: output-token quota was exceeded.",
            previous_run_id=newer.run_id,
        )
    )
    app = open_dashboard()
    assert not app.exception
    text = all_text(app)
    assert "offline-rejected-model" in text
    assert "output-token quota" in text
    assert newer.model in text
    assert app.selectbox(key="spec_debug_run").value == newer.run_id
    assert store.load_latest_extraction(document.document_id).run_id == newer.run_id


def test_switching_from_multi_page_to_single_page_resets_page_before_widget_creation(
    store: FileSpecStore,
) -> None:
    document, _, _ = seed_history(store)
    app = open_dashboard()
    app = app.number_input(key="spec_debug_text_page").set_value(2).run()
    assert not app.exception
    single = store.import_pdf("single-page.pdf", make_pdf("Synthetic single-page request."))
    app = app.run()
    assert app.selectbox(key="spec_debug_document").value == document.document_id
    app = app.selectbox(key="spec_debug_document").set_value(single.document_id).run()
    assert not app.exception
    assert app.number_input(key="spec_debug_text_page").value == 1
    assert "Synthetic single-page request." in all_text(app)

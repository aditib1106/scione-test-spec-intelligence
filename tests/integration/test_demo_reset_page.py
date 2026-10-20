"""Developer-only reset starts a fresh journey without losing recovery artifacts."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.errors import AppTestError

from scione.catalogs import FileLabStore, load_reference_catalog
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.reports.matching import build_report
from scione.reports.pdf import render_report_pdf
from scione.reports.store import FileReportStore
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore

APP = Path(__file__).parents[2] / "src" / "scione" / "web" / "app.py"
SOURCE = "Pencil hardness: ISO 15184, 750 g load; at least F."
RAW_RESPONSE = "  ORIGINAL MODEL RESPONSE\nPreserve exact whitespace.\n"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path / "workspace"))

    def reject_model_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Demo reset and navigation must not call a model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)


def file_bytes(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def saved_demo(tmp_path: Path):
    root = tmp_path / "workspace"
    catalog = load_reference_catalog()
    labs = FileLabStore(root, catalog)
    lab = labs.load_or_create()
    lab.name = "Existing user-edited laboratory"
    lab.capabilities[0].price = Decimal("123.456789")
    labs.save(lab)
    lab = labs.load_or_create()
    specs = FileSpecStore(root)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 700), SOURCE, fontsize=10)
        source_pdf = pdf.tobytes()
    source_path = tmp_path / "client-supplied.pdf"
    source_path.write_bytes(source_pdf)
    stored = specs.import_pdf("client-supplied.pdf", source_pdf)
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": stored.document_id,
            "prompt_version": "offline_reset_fixture",
            "schema_version": "0.1",
            "provider": "offline_test",
            "model": "no_network",
            "latency_ms": 1,
            "usage": {},
            "raw_response": RAW_RESPONSE,
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Offline reset fixture"},
                "requirements": [
                    {
                        "id": "req_pencil",
                        "original_test_name": "Pencil hardness",
                        "requested_methods": [{"code": "ISO 15184"}],
                        "conditions": [{"name": "Load", "raw_value": "750", "unit": "g"}],
                        "acceptance_criteria": [{"name": "Hardness", "raw_value": "At least F"}],
                        "evidence": [{"page": 1, "quote": SOURCE}],
                    }
                ],
            },
        }
    )
    specs.save_extraction(stored.document_id, run)
    review = specs.save_review(stored.document_id, run.run_id, "req_pencil", "confirmed")
    reports = FileReportStore(root)
    workspace = reports.load_workspace(stored.document_id, run.run_id)
    workspace = reports.save_workspace(workspace, expected_version=0)
    snapshot = build_report(
        stored,
        run,
        review,
        lab,
        catalog,
        workspace,
        pdf_bytes=source_pdf,
    )
    reports.save_report(snapshot, render_report_pdf(snapshot))
    (root / ".env").write_bytes(b"private credentials in workspace remain untouched")
    (tmp_path / ".env").write_bytes(b"private credentials outside workspace remain untouched")
    (root / "unrelated.txt").write_bytes(b"unknown files are not reset")
    return root, stored.document_id, run.run_id, snapshot.report_id, source_path


def open_developer(app: AppTest | None = None) -> AppTest:
    if app is None:
        app = AppTest.from_file(str(APP), default_timeout=20).run()
    return app.sidebar.button(key="nav_developer_tools").click().run()


def perform_reset(app: AppTest) -> AppTest:
    app = app.checkbox(key="demo_reset_acknowledge").check().run()
    assert not app.button(key="demo_reset_confirm").disabled
    return app.button(key="demo_reset_confirm").click().run()


def test_opening_reset_panel_and_acknowledging_does_not_reset(saved_demo) -> None:
    root, *_ = saved_demo
    before = file_bytes(root)
    app = open_developer()

    assert not app.exception
    assert app.button(key="demo_reset_confirm").disabled
    assert not app.checkbox(key="demo_reset_acknowledge").value
    assert file_bytes(root) == before
    assert not (root.parent / ".demo-backups").exists()

    app = app.checkbox(key="demo_reset_acknowledge").check().run()

    assert not app.exception
    assert not app.button(key="demo_reset_confirm").disabled
    assert file_bytes(root) == before
    assert not (root.parent / ".demo-backups").exists()


def test_reset_button_event_without_acknowledgement_cannot_clear_state(saved_demo) -> None:
    root, *_ = saved_demo
    before = file_bytes(root)
    app = open_developer()
    with pytest.raises(AppTestError, match="disabled"):
        app.button(key="demo_reset_confirm").click()
    app = app.run()

    assert not app.exception
    assert app.title[0].value == "Developer Tools"
    assert file_bytes(root) == before
    assert not (root.parent / ".demo-backups").exists()


def test_explicit_reset_preserves_exact_backup_and_restarts_empty_journey(saved_demo) -> None:
    root, document_id, run_id, report_id, source_path = saved_demo
    before = file_bytes(root)
    app = open_developer()
    app.session_state["_test_unsaved_editor"] = "must not survive reset"
    app = perform_reset(app)

    assert not app.exception
    assert app.title[0].value == "My Lab"
    assert app.session_state["workspace_page"] == "My Lab"
    assert "_test_unsaved_editor" not in app.session_state
    assert not app.dataframe
    assert app.button(key="load_example_lab")
    assert any("Recovery backup:" in item.value for item in app.success)
    empty_lab = FileLabStore(root, seed_demo=False).load_or_create()
    assert empty_lab.name == "My Lab"
    assert empty_lab.version == 1
    assert empty_lab.capabilities == []
    assert not (root / "specifications").exists()
    assert not (root / "reports").exists()
    assert (root / ".env").read_bytes() == before[Path(".env")]
    assert (root / "unrelated.txt").read_bytes() == before[Path("unrelated.txt")]
    assert source_path.read_bytes() == before[Path("specifications") / document_id / "original.pdf"]
    assert (root.parent / ".env").read_bytes() == (
        b"private credentials outside workspace remain untouched"
    )

    backups = list((root.parent / ".demo-backups").iterdir())
    assert len(backups) == 1
    backup = backups[0]
    for relative, content in before.items():
        if relative.parts[0] in {"lab.json", "specifications", "reports"}:
            assert (backup / relative).read_bytes() == content
    archived_specs = FileSpecStore(backup)
    assert archived_specs.load_extraction(document_id, run_id).raw_response == RAW_RESPONSE
    assert (
        archived_specs.load_review(document_id, run_id).decisions["req_pencil"].status
        == "confirmed"
    )
    archived_reports = FileReportStore(backup)
    assert archived_reports.load_report(report_id).report_id == report_id
    assert (
        archived_reports.read_pdf(report_id)
        == before[Path("reports") / "snapshots" / report_id / "report.pdf"]
    )

    app = app.sidebar.button(key="nav_specifications").click().run()
    assert not app.exception
    assert any("No specifications saved yet" in item.value for item in app.info)
    app = app.sidebar.button(key="nav_reports").click().run()
    assert not app.exception
    assert any("No specifications saved yet" in item.value for item in app.info)
    assert not app.tabs


def test_repeated_reset_is_explicit_and_keeps_the_first_recovery_backup(saved_demo) -> None:
    root, *_ = saved_demo
    app = perform_reset(open_developer())
    initial_backups = list((root.parent / ".demo-backups").iterdir())
    original_bytes = file_bytes(initial_backups[0])
    app = open_developer(app)

    assert not app.checkbox(key="demo_reset_acknowledge").value
    assert app.button(key="demo_reset_confirm").disabled
    app = perform_reset(app)

    assert not app.exception
    assert app.title[0].value == "My Lab"
    assert FileLabStore(root, seed_demo=False).load_or_create().capabilities == []
    assert len(list((root.parent / ".demo-backups").iterdir())) == 2
    assert file_bytes(initial_backups[0]) == original_bytes


def test_reset_failure_does_not_clear_saved_or_in_memory_state(saved_demo) -> None:
    root, *_ = saved_demo
    app = open_developer()
    app.session_state["_test_unsaved_editor"] = "keep this draft"
    (root / ".lab.lock").write_bytes(b"another writer")
    before = file_bytes(root)
    app = perform_reset(app)

    assert not app.exception
    assert app.title[0].value == "Developer Tools"
    assert app.session_state["_test_unsaved_editor"] == "keep this draft"
    assert any("writer may be active" in item.value for item in app.error)
    assert file_bytes(root) == before
    assert not (root.parent / ".demo-backups").exists()

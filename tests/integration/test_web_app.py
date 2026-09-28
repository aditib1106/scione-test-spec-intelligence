"""Smoke-test the Streamlit interface without starting a browser or model call."""

from pathlib import Path

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from scione.providers import StaticModelProvider
from scione.workbench import execute_method_workbench

REPOSITORY_ROOT = Path(__file__).parents[2]
APP = REPOSITORY_ROOT / "src" / "scione" / "web" / "app.py"
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


def test_web_app_renders_empty_state(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCIONE_RUNS_DIR", str(tmp_path))

    app = AppTest.from_file(str(APP), default_timeout=10).run()

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

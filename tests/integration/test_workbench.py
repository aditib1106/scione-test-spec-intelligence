"""Integration coverage for the interface-neutral application workflow."""

from pathlib import Path

from scione.providers import StaticModelProvider
from scione.workbench import execute_method_workbench

REPOSITORY_ROOT = Path(__file__).parents[2]
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


def test_workbench_executes_evaluates_and_persists(tmp_path: Path) -> None:
    result = execute_method_workbench(
        SAMPLE_PDF,
        provider=StaticModelProvider.from_json_file(GROUND_TRUTH),
        runs_dir=tmp_path,
        ground_truth_path=GROUND_TRUTH,
    )

    assert result.run.provider == "static"
    assert result.evaluation is not None
    assert all(metric.f1 == 1.0 for metric in result.evaluation.metrics)
    assert result.evaluation.evaluator_version == "0.3"
    assert result.evaluation.evidence_grounding is not None
    assert result.evaluation.evidence_grounding.rate == 1.0
    assert result.ground_truth is not None
    assert (result.run_directory / "ground_truth.json").is_file()
    assert result.run_directory.is_dir()

"""Tests for immutable local run artifacts."""

from pathlib import Path

from scione.evaluation import TestMethodEvaluator
from scione.extraction import MethodExtractionRunner
from scione.ingestion import PyMuPDFTextIngestor
from scione.providers import StaticModelProvider
from scione.runs import FileRunStore
from scione.schemas import TestMethodExtraction as MethodExtractionSchema

REPOSITORY_ROOT = Path(__file__).parents[2]
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


def test_file_store_persists_reproducible_run_artifacts(tmp_path: Path) -> None:
    document = PyMuPDFTextIngestor().ingest(SAMPLE_PDF)
    provider = StaticModelProvider.from_json_file(GROUND_TRUTH)
    run = MethodExtractionRunner(provider).run(document)
    ground_truth = MethodExtractionSchema.model_validate_json(
        GROUND_TRUTH.read_text(encoding="utf-8")
    )
    evaluation = TestMethodEvaluator().evaluate(run.extraction, ground_truth)

    run_directory = FileRunStore(tmp_path).save_method_run(
        document=document,
        run=run,
        evaluation=evaluation,
    )

    assert run_directory == tmp_path / run.run_id
    assert {path.name for path in run_directory.iterdir()} == {
        "document.json",
        "evaluation.json",
        "prediction.json",
        "raw_response.txt",
        "run.json",
    }

    loaded = FileRunStore(tmp_path).load_method_run(run.run_id)
    assert loaded.run == run
    assert loaded.document == document
    assert loaded.evaluation == evaluation
    assert FileRunStore(tmp_path).list_method_run_ids() == [run.run_id]

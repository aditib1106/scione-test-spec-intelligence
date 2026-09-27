"""Shared application workflow used by command-line and web interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scione.config import ConfigurationError, Settings
from scione.evaluation import EvaluationReport, TestMethodEvaluator
from scione.extraction import MethodExtractionRun, MethodExtractionRunner
from scione.ingestion import PyMuPDFTextIngestor
from scione.providers import GroqModelProvider, ModelProvider
from scione.runs import FileRunStore
from scione.schemas import IngestedDocument, TestMethodExtraction, TextReadingOrder


@dataclass(frozen=True)
class WorkbenchRun:
    """One completed, persisted extraction workflow."""

    document: IngestedDocument
    run: MethodExtractionRun
    evaluation: EvaluationReport | None
    run_directory: Path


def create_configured_provider(settings: Settings) -> ModelProvider:
    """Build the selected adapter without coupling the extraction runner to a vendor."""

    provider_name = settings.model_provider.strip().casefold()
    if provider_name == "groq":
        return GroqModelProvider(
            api_key=settings.require_groq_api_key(),
            model_name=settings.groq_model,
            timeout_seconds=settings.groq_timeout_seconds,
            max_retries=settings.groq_max_retries,
            max_completion_tokens=settings.groq_max_completion_tokens,
            reasoning_effort=settings.groq_reasoning_effort,
            temperature=settings.groq_temperature,
            strict_structured_output=settings.groq_strict_structured_output,
        )
    raise ConfigurationError(
        f"Unsupported MODEL_PROVIDER={settings.model_provider!r}. "
        "Install an adapter and register it in create_configured_provider()."
    )


def execute_method_workbench(
    pdf_path: str | Path,
    *,
    provider: ModelProvider,
    runs_dir: str | Path,
    reading_order: TextReadingOrder = TextReadingOrder.CONTENT_STREAM,
    ground_truth_path: str | Path | None = None,
) -> WorkbenchRun:
    """Execute and persist one provider-neutral method extraction."""

    document = PyMuPDFTextIngestor(reading_order=reading_order).ingest(pdf_path)
    run = MethodExtractionRunner(provider).run(document)
    evaluation = None
    if ground_truth_path is not None:
        ground_truth = TestMethodExtraction.model_validate_json(
            Path(ground_truth_path).read_text(encoding="utf-8")
        )
        evaluation = TestMethodEvaluator().evaluate(run.extraction, ground_truth)
    run_directory = FileRunStore(runs_dir).save_method_run(
        document=document,
        run=run,
        evaluation=evaluation,
    )
    return WorkbenchRun(
        document=document,
        run=run,
        evaluation=evaluation,
        run_directory=run_directory,
    )

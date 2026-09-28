"""Shared application workflow used by command-line and web interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scione.config import ConfigurationError, Settings
from scione.evaluation import EvaluationReport, TestMethodEvaluator
from scione.extraction import MethodExtractionRun, MethodExtractionRunner
from scione.ingestion import PyMuPDFTextIngestor
from scione.providers import GeminiModelProvider, GroqModelProvider, ModelProvider
from scione.runs import FileRunStore
from scione.schemas import IngestedDocument, TestMethodExtraction, TextReadingOrder


@dataclass(frozen=True)
class WorkbenchRun:
    """One completed, persisted extraction workflow."""

    document: IngestedDocument
    run: MethodExtractionRun
    evaluation: EvaluationReport | None
    run_directory: Path
    ground_truth: TestMethodExtraction | None = None


def create_configured_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    """Build the selected adapter without coupling the extraction runner to a vendor."""

    selected_provider = (provider_name or settings.model_provider).strip().casefold()
    max_retries = 0 if settings.scione_single_attempt_mode else None
    if selected_provider == "groq":
        return GroqModelProvider(
            api_key=settings.require_groq_api_key(),
            model_name=model_name or settings.groq_model,
            timeout_seconds=settings.groq_timeout_seconds,
            max_retries=(settings.groq_max_retries if max_retries is None else max_retries),
            max_completion_tokens=settings.groq_max_completion_tokens,
            reasoning_effort=settings.groq_reasoning_effort,
            temperature=settings.groq_temperature,
            strict_structured_output=settings.groq_strict_structured_output,
        )
    if selected_provider == "gemini":
        return GeminiModelProvider(
            api_key=settings.require_gemini_api_key(),
            model_name=model_name or settings.gemini_model,
            timeout_seconds=settings.gemini_timeout_seconds,
            max_retries=(settings.gemini_max_retries if max_retries is None else max_retries),
            max_output_tokens=settings.gemini_max_output_tokens,
            temperature=settings.gemini_temperature,
            thinking_level=settings.gemini_thinking_level,
        )
    raise ConfigurationError(
        f"Unsupported provider={selected_provider!r}. "
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
    ground_truth = None
    if ground_truth_path is not None:
        ground_truth = TestMethodExtraction.model_validate_json(
            Path(ground_truth_path).read_text(encoding="utf-8")
        )
        evaluation = TestMethodEvaluator().evaluate(
            run.extraction,
            ground_truth,
            source_document=document,
        )
    run_directory = FileRunStore(runs_dir).save_method_run(
        document=document,
        run=run,
        evaluation=evaluation,
        ground_truth=ground_truth,
    )
    return WorkbenchRun(
        document=document,
        run=run,
        evaluation=evaluation,
        run_directory=run_directory,
        ground_truth=ground_truth,
    )

"""Unit tests for provider-neutral method extraction orchestration."""

from pathlib import Path

import pymupdf
import pytest

from scione.extraction import ExtractionError, MethodExtractionRunner
from scione.ingestion import PyMuPDFTextIngestor
from scione.prompts import (
    METHOD_EXTRACTION_PROMPT_VERSION,
    build_method_extraction_user_prompt,
    load_method_extraction_system_prompt,
)
from scione.providers import StaticModelProvider
from scione.schemas import TextReadingOrder

REPOSITORY_ROOT = Path(__file__).parents[2]
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)
GROUND_TRUTH = REPOSITORY_ROOT / "benchmark" / "cases" / "moon_glass_standard" / "ground_truth.json"


def test_prompt_preserves_page_boundaries_and_acceptance_rule() -> None:
    document = PyMuPDFTextIngestor().ingest(SAMPLE_PDF)

    system_prompt = load_method_extraction_system_prompt()
    user_prompt = build_method_extraction_user_prompt(document)

    assert METHOD_EXTRACTION_PROMPT_VERSION == "method_extraction_v0.1"
    assert "classification definition is not a customer acceptance criterion" in system_prompt
    assert "=== PAGE 1 ===" in user_prompt
    assert document.document_id in user_prompt


def test_static_provider_runs_complete_validated_pipeline() -> None:
    document = PyMuPDFTextIngestor().ingest(SAMPLE_PDF)
    provider = StaticModelProvider.from_json_file(GROUND_TRUTH)

    run = MethodExtractionRunner(provider).run(document)

    assert run.strategy == "whole_document"
    assert run.prompt_version == METHOD_EXTRACTION_PROMPT_VERSION
    assert run.provider == "static"
    assert run.extraction.document.designation == "FTM-MG-017"
    assert run.extraction.acceptance_criteria == []


def test_runner_rejects_schema_invalid_provider_payload() -> None:
    document = PyMuPDFTextIngestor().ingest(SAMPLE_PDF)
    invalid_payload = {"schema_version": "0.1"}

    with pytest.raises(ExtractionError, match="did not conform"):
        MethodExtractionRunner(StaticModelProvider(invalid_payload)).run(document)


def test_runner_rejects_document_that_needs_ocr(tmp_path: Path) -> None:
    blank_pdf = tmp_path / "blank.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(blank_pdf)

    document = PyMuPDFTextIngestor().ingest(blank_pdf)
    payload = {"schema_version": "0.1"}

    with pytest.raises(ExtractionError, match="requires ready text"):
        MethodExtractionRunner(StaticModelProvider(payload)).run(document)


def test_prompt_records_selected_reading_order() -> None:
    document = PyMuPDFTextIngestor(reading_order=TextReadingOrder.GEOMETRIC).ingest(SAMPLE_PDF)

    assert "text_reading_order: geometric" in build_method_extraction_user_prompt(document)

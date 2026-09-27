"""Integration coverage for the tracked synthetic sample document."""

from pathlib import Path

from scione.ingestion import PyMuPDFTextIngestor
from scione.schemas import TextExtractionStatus

REPOSITORY_ROOT = Path(__file__).parents[2]
SAMPLE_PDF = (
    REPOSITORY_ROOT / "docs" / "samples" / "Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
)


def test_ingests_moon_glass_sample() -> None:
    document = PyMuPDFTextIngestor().ingest(SAMPLE_PDF)

    assert document.page_count == 4
    assert document.extraction_status == TextExtractionStatus.READY
    assert "FTM-MG-017" in document.pages[0].text
    assert "TEST METHOD A" in document.as_prompt_text()
    assert "TEST METHOD B" in document.as_prompt_text()
    assert document.pages[0].text.index("1. Scope") < document.pages[0].text.index(
        "4. Summary of Test Methods"
    )

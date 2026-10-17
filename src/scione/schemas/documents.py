"""Provider-neutral document representations produced by ingestion adapters."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class TextExtractionStatus(StrEnum):
    """High-level assessment of the text extracted from a document."""

    READY = "ready"
    POSSIBLE_SCANNED_PDF = "possible_scanned_pdf"
    EMPTY = "empty"


class TextReadingOrder(StrEnum):
    """Ordering applied while converting positioned PDF text to plain text."""

    CONTENT_STREAM = "content_stream"
    GEOMETRIC = "geometric"


class DocumentPage(BaseModel):
    """Text and basic measurements for one physical PDF page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    text: str
    character_count: int = Field(ge=0)
    non_whitespace_character_count: int = Field(ge=0)


class IngestedDocument(BaseModel):
    """Canonical ingestion output consumed by later inference strategies."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    media_type: str = "application/pdf"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=0)
    pages: list[DocumentPage]
    metadata: dict[str, str] = Field(default_factory=dict)
    text_reading_order: TextReadingOrder
    extraction_status: TextExtractionStatus
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def total_character_count(self) -> int:
        return sum(page.character_count for page in self.pages)

    @computed_field
    @property
    def total_non_whitespace_character_count(self) -> int:
        return sum(page.non_whitespace_character_count for page in self.pages)

    def as_prompt_text(self) -> str:
        """Render text with explicit boundaries so page provenance is not lost."""

        return "\n\n".join(
            f"=== PAGE {page.page_number} ===\n{page.text.rstrip()}" for page in self.pages
        )

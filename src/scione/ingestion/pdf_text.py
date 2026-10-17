"""Text-based PDF ingestion implemented with PyMuPDF."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf

from scione.ingestion.base import DocumentIngestor, IngestionError
from scione.schemas import (
    DocumentPage,
    IngestedDocument,
    TextExtractionStatus,
    TextReadingOrder,
)


class PyMuPDFTextIngestor(DocumentIngestor):
    """Extract ordered, page-aware text from a digitally generated PDF."""

    def __init__(
        self,
        *,
        minimum_document_characters: int = 100,
        minimum_page_characters: int = 30,
        minimum_text_page_ratio: float = 0.5,
        reading_order: TextReadingOrder = TextReadingOrder.CONTENT_STREAM,
    ) -> None:
        self.minimum_document_characters = minimum_document_characters
        self.minimum_page_characters = minimum_page_characters
        self.minimum_text_page_ratio = minimum_text_page_ratio
        self.reading_order = reading_order

    def ingest(self, path: str | Path) -> IngestedDocument:
        pdf_path = Path(path)
        self._validate_path(pdf_path)

        file_bytes = pdf_path.read_bytes()
        digest = hashlib.sha256(file_bytes).hexdigest()

        try:
            with pymupdf.open(stream=file_bytes, filetype="pdf") as pdf:
                if pdf.needs_pass:
                    raise IngestionError(f"PDF is password protected: {pdf_path.name}")

                pages = [self._extract_page(page, index + 1) for index, page in enumerate(pdf)]
                metadata = self._clean_metadata(pdf.metadata or {})
        except IngestionError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise IngestionError(f"Unable to read PDF {pdf_path.name}: {exc}") from exc

        status, warnings = self._assess_text_quality(pages)

        return IngestedDocument(
            document_id=digest[:16],
            file_name=pdf_path.name,
            sha256=digest,
            page_count=len(pages),
            pages=pages,
            metadata=metadata,
            text_reading_order=self.reading_order,
            extraction_status=status,
            warnings=warnings,
        )

    @staticmethod
    def _validate_path(path: Path) -> None:
        if not path.exists():
            raise IngestionError(f"File does not exist: {path}")
        if not path.is_file():
            raise IngestionError(f"Path is not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise IngestionError(f"Expected a .pdf file, received: {path.name}")

    def _extract_page(self, page: pymupdf.Page, page_number: int) -> DocumentPage:
        text = page.get_text(
            "text",
            sort=self.reading_order == TextReadingOrder.GEOMETRIC,
        )
        return DocumentPage(
            page_number=page_number,
            text=text,
            character_count=len(text),
            non_whitespace_character_count=sum(not character.isspace() for character in text),
        )

    @staticmethod
    def _clean_metadata(metadata: dict[str, str]) -> dict[str, str]:
        return {
            key: value.strip()
            for key, value in metadata.items()
            if isinstance(value, str) and value.strip()
        }

    def _assess_text_quality(
        self, pages: list[DocumentPage]
    ) -> tuple[TextExtractionStatus, list[str]]:
        total_characters = sum(page.non_whitespace_character_count for page in pages)
        if total_characters == 0:
            return (
                TextExtractionStatus.EMPTY,
                ["No extractable text was found; the PDF may be empty or image-only."],
            )

        pages_with_text = sum(
            page.non_whitespace_character_count >= self.minimum_page_characters for page in pages
        )
        text_page_ratio = pages_with_text / len(pages) if pages else 0.0

        warnings: list[str] = []
        blank_pages = [page.page_number for page in pages if not page.text.strip()]
        if blank_pages:
            warnings.append(f"No text was extracted from pages: {blank_pages}.")

        if (
            total_characters < self.minimum_document_characters
            or text_page_ratio < self.minimum_text_page_ratio
        ):
            warnings.append(
                "The extracted text is sparse; this document may require OCR or "
                "multimodal ingestion."
            )
            return TextExtractionStatus.POSSIBLE_SCANNED_PDF, warnings

        return TextExtractionStatus.READY, warnings

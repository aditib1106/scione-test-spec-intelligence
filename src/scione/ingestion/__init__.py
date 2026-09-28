"""Document ingestion adapters."""

from scione.ingestion.base import DocumentIngestor, IngestionError
from scione.ingestion.pdf_text import PyMuPDFTextIngestor

__all__ = ["DocumentIngestor", "IngestionError", "PyMuPDFTextIngestor"]

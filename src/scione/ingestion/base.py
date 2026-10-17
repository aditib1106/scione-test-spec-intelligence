"""Interfaces and errors for document ingestion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from scione.schemas import IngestedDocument


class IngestionError(RuntimeError):
    """Raised when a document cannot be safely converted into canonical input."""


class DocumentIngestor(ABC):
    """Convert a source document into the provider-neutral document schema."""

    @abstractmethod
    def ingest(self, path: str | Path) -> IngestedDocument:
        """Read a document without mutating the source file."""

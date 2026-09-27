"""Prompt loading and document rendering for method extraction v0.1."""

from __future__ import annotations

from importlib.resources import files

from scione.schemas import IngestedDocument

METHOD_EXTRACTION_PROMPT_VERSION = "method_extraction_v0.1"
_PROMPT_FILE = "method_extraction_v0_1.txt"


def load_method_extraction_system_prompt() -> str:
    return files("scione.prompts").joinpath(_PROMPT_FILE).read_text(encoding="utf-8").strip()


def build_method_extraction_user_prompt(document: IngestedDocument) -> str:
    """Keep document identity and page boundaries explicit for evidence extraction."""

    return "\n".join(
        [
            "<SOURCE_DOCUMENT>",
            f"document_id: {document.document_id}",
            f"file_name: {document.file_name}",
            f"page_count: {document.page_count}",
            f"text_reading_order: {document.text_reading_order.value}",
            "<DOCUMENT_TEXT>",
            document.as_prompt_text(),
            "</DOCUMENT_TEXT>",
            "</SOURCE_DOCUMENT>",
        ]
    )

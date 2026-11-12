"""Prove complete source cells without changing any extracted scientific fact.

The comparison view may cite a wrapped table cell whose text was interleaved
with another column in the PDF drawing order. Literal row ownership and whole
cell equality are required; this is layout evidence, not semantic correction.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import pymupdf

from scione.schemas import IngestedDocument
from scione.specifications.models import CustomerRequirement, RequirementEvidence


def _literal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class _Cell:
    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class _Row:
    bbox: tuple[float, float, float, float]
    cells: tuple[_Cell, ...]


def _page_rows(page: pymupdf.Page) -> dict[str, list[_Row]]:
    result: dict[str, list[_Row]] = {}
    try:
        detected = page.find_tables()
        for table in detected.tables:
            if table.col_count < 2:
                continue
            for row in table.rows:
                if not row.cells or any(cell is None for cell in row.cells):
                    continue
                boxes = [pymupdf.Rect(cell) for cell in row.cells]
                if any(
                    abs(box.y0 - boxes[0].y0) > 0.01
                    or abs(box.y1 - boxes[0].y1) > 0.01
                    for box in boxes[1:]
                ):
                    continue  # A vertically merged cell cannot establish one row.
                cells = tuple(
                    _Cell(page.get_text("text", clip=box, sort=True).strip(), tuple(box))
                    for box in boxes
                )
                label = _literal(cells[0].text)
                if not label:
                    continue
                bounds = pymupdf.Rect(boxes[0])
                for box in boxes[1:]:
                    bounds |= box
                result.setdefault(label, []).append(_Row(tuple(bounds), cells))
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return {}  # Failure to inspect layout never proves a citation.
    return result


def ground_table_cell_citations(
    requirements: Sequence[CustomerRequirement],
    document: IngestedDocument,
    pdf_bytes: bytes,
) -> tuple[list[CustomerRequirement], list[dict]]:
    """Return detached requirements with only physically proved extra citations.

    Labels and complete cells match using NFKC and whitespace normalization,
    preserving case, punctuation, numbers, units, and all original atoms.
    Duplicate rows/cells or ambiguous page ownership remain unproved.
    """
    if not isinstance(pdf_bytes, bytes) or hashlib.sha256(pdf_bytes).hexdigest() != document.sha256:
        raise ValueError("PDF bytes do not match the source document.")
    detached = [requirement.model_copy(deep=True) for requirement in requirements]
    actions: dict[tuple, dict] = {}
    page_cache: dict[int, dict[str, list[_Row]]] = {}
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        if len(pdf) != document.page_count:
            raise ValueError("PDF page count does not match the source document.")
        for requirement in detached:
            label = _literal(requirement.original_test_name)
            for claim in requirement.atomic_claims:
                candidates = []
                for page_number in sorted(set(claim.source_pages)):
                    if not 1 <= page_number <= len(pdf):
                        continue
                    if page_number not in page_cache:
                        page_cache[page_number] = _page_rows(pdf[page_number - 1])
                    rows = page_cache[page_number].get(label, [])
                    if len(rows) != 1:
                        continue
                    row = rows[0]
                    matches = [
                        (index, cell) for index, cell in enumerate(row.cells[1:], start=1)
                        if cell.text and _literal(cell.text) == _literal(claim.raw_text)
                    ]
                    if len(matches) == 1:
                        candidates.append((page_number, row, *matches[0]))
                if len(candidates) != 1:
                    continue
                page_number, row, index, cell = candidates[0]
                if len(cell.text) > 8000:
                    continue
                existing = any(
                    evidence.page == page_number
                    and _literal(evidence.quote) == _literal(cell.text)
                    for evidence in requirement.evidence
                )
                if not existing:
                    requirement.evidence.append(
                        RequirementEvidence(page=page_number, quote=cell.text)
                    )
                key = (requirement.id, page_number, row.bbox, index)
                if key not in actions:
                    actions[key] = {
                        "rule": "physical_table_cell_citation",
                        "requirement_id": requirement.id,
                        "claim_ids": [],
                        "source_page": page_number,
                        "source_document_sha256": document.sha256,
                        "source_row_bbox": list(row.bbox),
                        "source_cell_bbox": list(cell.bbox),
                        "source_cell_index": index + 1,
                        "source_test_label": row.cells[0].text,
                        "source_cell_text": cell.text,
                        "source_quote_sha256": hashlib.sha256(cell.text.encode()).hexdigest(),
                        "evidence_added": not existing,
                        "original_atoms_preserved": True,
                        "establishes_lab_support": False,
                    }
                if claim.id not in actions[key]["claim_ids"]:
                    actions[key]["claim_ids"].append(claim.id)
    return detached, list(actions.values())

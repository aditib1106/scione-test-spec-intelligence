"""Explicitly rebuild packaged synthetic identity metadata from teammate files.

Run manually after reviewing a source update. Startup never scans customer PDFs,
imports a lab workbook, calls a model or derives aliases from expected outputs.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from scione.catalogs.bank import MethodBankError, MethodBankIndex

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def audited_identifiers(path: Path) -> set[str]:
    """Read explicit method-ID cells; other audit labels never become metadata."""

    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(item.itertext()) for item in root.findall(f"{_MAIN}si")]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        identifiers = set()
        header_column = None
        for row in sheet.findall(f"{_MAIN}sheetData/{_MAIN}row"):
            values = {}
            for cell in row.findall(f"{_MAIN}c"):
                column = re.sub(r"\d", "", cell.attrib["r"])
                value = cell.find(f"{_MAIN}v")
                text = value.text if value is not None else ""
                if cell.attrib.get("t") == "s":
                    text = strings[int(text)]
                elif cell.attrib.get("t") == "inlineStr":
                    text = "".join(cell.find(f"{_MAIN}is").itertext())
                values[column] = text
            if "Test document ID" in values.values():
                header_column = next(
                    k for k, value in values.items() if value == "Test document ID"
                )
            elif header_column and values.get(header_column):
                identifiers.add(values[header_column])
        if not header_column:
            raise MethodBankError("The audit workbook has no explicit Test document ID column.")
        return identifiers


def build_bank(
    source: Path, audit: Path, destination: Path, *, lab_csv: Path | None = None
) -> dict:
    content = source.read_bytes()
    bank = MethodBankIndex.from_bytes(content)
    identifiers = audited_identifiers(audit)
    lab_identifiers = set()
    if lab_csv is not None:
        with lab_csv.open(encoding="utf-8-sig", newline="") as stream:
            lab_identifiers = {row["test_method"] for row in csv.DictReader(stream)}
        identifiers.update(lab_identifiers)
    selected = []
    for identifier in sorted(identifiers):
        row = bank.lookup_identifier(identifier)
        # The workbook also labels customer-defined procedures. They are not
        # invented bank entries and are deliberately excluded from this import.
        if row is not None:
            selected.append(row.as_dict())
    if not selected:
        raise MethodBankError("The audit selected no existing synthetic bank identifiers.")
    families = {}
    representatives = {}
    page = bank.search(limit=500)
    for offset in range(0, page.total_count, 500):
        for row in bank.search(offset=offset, limit=500).records:
            representatives.setdefault(row.family_id, row)
            families.setdefault(
                row.family_id,
                {
                    "test_item_id": row.family_id,
                    "test_item_name": row.family_name,
                    "domain": row.metadata["domain"],
                    "test_category": row.metadata["test_category"],
                },
            )
    selected_families = {row["test_item_id"] for row in selected}
    representative_ids = []
    for family_id in sorted(families):
        if family_id not in selected_families:
            row = representatives[family_id]
            selected.append(row.as_dict())
            representative_ids.append(row.identifier)
    excerpt = {
        "schema_version": "1.0",
        "source_csv_sha256": bank.source_sha256,
        "families": [families[key] for key in sorted(families)],
        "methods": selected,
    }
    raw_excerpt = (json.dumps(excerpt, ensure_ascii=False, indent=2) + "\n").encode()
    manifest = {
        "schema_version": "1.0",
        "dataset_id": "teammate_synthetic_method_bank_v1",
        "source_csv_name": source.name,
        "source_csv_sha256": bank.source_sha256,
        "source_audit_name": audit.name,
        "source_audit_sha256": hashlib.sha256(audit.read_bytes()).hexdigest(),
        "source_lab_csv_name": lab_csv.name if lab_csv else None,
        "source_lab_csv_sha256": (
            hashlib.sha256(lab_csv.read_bytes()).hexdigest() if lab_csv else None
        ),
        "source_lab_method_count": len(lab_identifiers),
        "representative_method_identifiers": representative_ids,
        "bank_record_count": len(bank),
        "bank_family_count": len(families),
        "runtime_bank_method_count": len(selected),
        "runtime_excerpt_sha256": hashlib.sha256(raw_excerpt).hexdigest(),
        "build_policy": "synthetic_bank_exact_identifiers_v2",
        "runtime_selection": (
            "All source test_item families; exact methods referenced by the reviewed audit "
            "and supplied lab CSV, plus one original bank method per otherwise unlinked family. "
            "Representative identities do not imply customer requiredness or lab support. "
            "Non-method observations, custom procedures and expected outcomes are not imported."
        ),
        "equivalence_policy": (
            "No official equivalence. Concept and cross-standard links are unvalidated "
            "candidate metadata, never aliases or support declarations."
        ),
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "synthetic_method_bank_v1.csv.gz").write_bytes(gzip.compress(content, mtime=0))
    (destination / "runtime_bank_excerpt_v1.json").write_bytes(raw_excerpt)
    (destination / "bank_manifest_v1.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--lab-csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "data")
    args = parser.parse_args()
    manifest = build_bank(args.source, args.audit, args.output, lab_csv=args.lab_csv)
    print(
        f"Packaged {manifest['bank_record_count']} synthetic metadata rows; "
        f"runtime excerpt: {manifest['bank_family_count']} families and "
        f"{manifest['runtime_bank_method_count']} explicit method IDs."
    )


if __name__ == "__main__":
    main()

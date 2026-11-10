"""Synthetic metadata lookup retains identity and never establishes lab support."""

import csv
import io
from pathlib import Path

import pytest

from scione.catalogs.bank import (
    MethodBankError,
    MethodBankIndex,
    load_bank_manifest,
    load_synthetic_method_bank,
)
from scione.catalogs.generated import canonical_test_id_for_family, method_id_for_record
from scione.catalogs.seed import default_csp301_lab, load_reference_catalog
from scione.reports.matching import _method_resolution
from scione.specifications.models import RequestedMethod


def _csv(rows: list[dict[str, str]]) -> bytes:
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode()


def test_packaged_bank_retains_all_source_rows_but_runtime_catalog_is_bounded() -> None:
    bank = load_synthetic_method_bank()
    manifest = load_bank_manifest()
    catalog = load_reference_catalog()
    assert len(bank) == manifest["bank_record_count"] == 32_000
    assert len(bank.family_names) == 71
    assert len(catalog.methods) == 113  # Original 40 plus 77 selected rows, four shared IDs.
    assert len(catalog.tests) == 99  # Original 39 plus 71 bank families, eleven shared IDs.
    assert bank.source_sha256 == manifest["source_csv_sha256"]
    assert "method equivalence" in catalog.source_note
    assert all(
        row.canonical_test_id in {test.id for test in catalog.tests}
        for row in default_csp301_lab().capabilities
    )
    assert len(default_csp301_lab().capabilities) == 4


def test_exact_identifier_lookup_preserves_editions_parts_and_adoption_prefixes() -> None:
    bank = load_synthetic_method_bank()
    method = bank.lookup_identifier(" iso 60038 : 2021 ")
    assert method is not None
    assert method.record_id == "SC-ISO-00038"
    assert method.identifier == "ISO 60038:2021"
    assert bank.lookup_identifier("ISO 60038") is None
    assert bank.lookup_identifier("ISO 60038:2020") is None
    assert bank.lookup_identifier("ISO 60038-1:2021") is None
    assert bank.lookup_identifier("DIN EN ISO 60038:2021") is None
    adopted = bank.lookup_identifier("DIN EN ISO 60001:2017-02")
    assert adopted is not None
    assert adopted.record_id != bank.lookup_identifier("ISO 60001:2017").record_id


def test_full_bank_query_retains_unvalidated_relationship_and_material_metadata() -> None:
    bank = load_synthetic_method_bank()
    page = bank.search(family_id="COR-02", limit=2)
    assert page.total_count == 638
    assert len(page.records) == 2
    assert page.has_more
    more = bank.search(family_id="COR-02", offset=2, limit=2)
    assert not {row.record_id for row in page.records} & {row.record_id for row in more.records}
    record = page.records[0]
    assert record.metadata["official_equivalence"] == "FALSE"
    assert record.metadata["reference_relation"] in {
        "SYNTHETIC_CANDIDATE_ONLY",
        "NO_CROSS_STANDARD_CANDIDATE",
    }
    material_page = bank.search(
        family_id="COR-02",
        domain=record.metadata["domain"],
        material=record.metadata["test_material"],
        limit=500,
    )
    assert record.record_id in {row.record_id for row in material_page.records}
    assert not bank.search(family_id="COR-02", domain="Textiles").records
    assert bank.search(family_id="not-recorded").total_count == 0


def test_source_rows_and_cached_manifest_cannot_be_modified_by_callers() -> None:
    bank = load_synthetic_method_bank()
    record = bank.lookup_record("SC-ISO-00038")
    assert record is not None
    with pytest.raises(TypeError):
        record.metadata["test_item_name"] = "Invented method"
    copy = record.as_dict()
    copy["test_item_name"] = "Invented method"
    assert record.family_name == "Cross-cut adhesion"
    manifest = load_bank_manifest()
    manifest["bank_record_count"] = 1
    assert load_bank_manifest()["bank_record_count"] == 32_000


@pytest.mark.parametrize(
    "update",
    [
        {"official_equivalence": "TRUE"},
        {"is_synthetic": "FALSE"},
        {"standard_id": ""},
    ],
)
def test_import_rejects_unreviewed_identity_or_equivalence_metadata(update: dict) -> None:
    row = load_synthetic_method_bank().lookup_record("SC-ISO-00038").as_dict()
    row.update(update)
    with pytest.raises(MethodBankError):
        MethodBankIndex.from_bytes(_csv([row]))


def test_import_rejects_duplicate_and_inconsistent_source_identities(tmp_path: Path) -> None:
    original = load_synthetic_method_bank().lookup_record("SC-ISO-00038").as_dict()
    duplicate = original | {"record_id": "OTHER", "standard_id": "ISO60038:2021"}
    with pytest.raises(MethodBankError, match="Duplicate"):
        MethodBankIndex.from_bytes(_csv([original, duplicate]))
    conflict = original | {
        "record_id": "OTHER",
        "standard_id": "ISO 99999:2021",
        "test_item_name": "Wrong family",
    }
    with pytest.raises(MethodBankError, match="inconsistent"):
        MethodBankIndex.from_bytes(_csv([original, conflict]))
    path = tmp_path / "bank.csv"
    path.write_bytes(_csv([original]))
    assert MethodBankIndex.from_csv(path).lookup_identifier(original["standard_id"]) is not None


def test_existing_identifiers_are_preserved_and_related_methods_are_not_aliases() -> None:
    catalog = load_reference_catalog()
    bank = load_synthetic_method_bank()
    row = bank.lookup_identifier("ISO 60038:2021")
    assert canonical_test_id_for_family(row.family_id, catalog) == "cross_cut_adhesion"
    assert method_id_for_record(row, catalog) == "synthetic_iso_60038_2021"
    astm = bank.lookup_identifier("ASTM D1037-20")
    astm_id = method_id_for_record(astm, catalog)
    assert astm_id != "synthetic_iso_60038_2021"
    assert catalog.method_by_id(astm_id).identifier == "ASTM D1037-20"
    assert "ISO 60038:2021" not in {alias.text for alias in catalog.method_by_id(astm_id).aliases}
    assert canonical_test_id_for_family("ENV-02", catalog) == "bank_env_02"
    assert canonical_test_id_for_family("missing", catalog) is None


def test_audited_astm_identifier_keeps_its_source_stated_edition_suffix() -> None:
    catalog = load_reference_catalog()
    method_id, issue = _method_resolution(RequestedMethod(code="ASTM D1025-19"), catalog)
    assert issue is None
    assert catalog.method_by_id(method_id).edition == "19"


@pytest.mark.parametrize("values", [{"limit": 0}, {"limit": 501}, {"offset": -1}])
def test_query_bounds_are_explicit(values: dict) -> None:
    with pytest.raises(ValueError):
        load_synthetic_method_bank().search(**values)

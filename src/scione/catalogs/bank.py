"""Offline lookup of the team's synthetic method metadata.

The complete bank is separate from the bounded runtime identity catalog. A bank
row is reference metadata, never a lab capability or a claim of equivalence.
Lookups retain editions, method parts, standard bodies and adoption qualifiers.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType


class MethodBankError(ValueError):
    """The supplied metadata cannot be used as a synthetic identity bank."""


def identifier_key(value: str) -> str:
    """Fold formatting only; never drop a year, part, prefix or qualifier."""

    text = unicodedata.normalize("NFKC", value)
    for character in "‐‑‒–—−":
        text = text.replace(character, "-")
    return re.sub(r"\s+", "", text).upper()


def _text_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class BankMethod:
    """An immutable source row with all original metadata columns retained."""

    metadata: Mapping[str, str]

    @property
    def record_id(self) -> str:
        return self.metadata["record_id"]

    @property
    def identifier(self) -> str:
        return self.metadata["standard_id"]

    @property
    def family_id(self) -> str:
        return self.metadata["test_item_id"]

    @property
    def family_name(self) -> str:
        return self.metadata["test_item_name"]

    def as_dict(self) -> dict[str, str]:
        return dict(self.metadata)


@dataclass(frozen=True)
class BankSearchPage:
    records: tuple[BankMethod, ...]
    total_count: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.records) < self.total_count


class MethodBankIndex:
    """Exact identifier and metadata indexes, loaded only when requested.

    Search pages are explicitly bounded and report the full result count. Search
    does not rank scientific similarity or convert neighboring rows to aliases.
    """

    def __init__(self, rows: list[dict[str, str]], *, source_sha256: str):
        required = {
            "record_id",
            "standard_id",
            "test_item_id",
            "test_item_name",
            "test_name",
            "domain",
            "test_material",
            "is_synthetic",
            "official_equivalence",
        }
        records: list[BankMethod] = []
        identifiers: dict[str, int] = {}
        record_ids: dict[str, int] = {}
        fields: dict[str, dict[str, set[int]]] = {
            "family_id": {},
            "domain": {},
            "material": {},
        }
        family_names: dict[str, str] = {}
        for row in rows:
            if required - row.keys() or any(not row[key].strip() for key in required):
                raise MethodBankError("A method row is missing required identity metadata.")
            if (
                row["is_synthetic"].upper() != "TRUE"
                or row["official_equivalence"].upper() != "FALSE"
            ):
                raise MethodBankError(
                    "This importer accepts synthetic, non-equivalence metadata only."
                )
            method = BankMethod(MappingProxyType(dict(row)))
            key = identifier_key(method.identifier)
            if key in identifiers or method.record_id in record_ids:
                raise MethodBankError("Duplicate method identifiers or record IDs require review.")
            previous = family_names.setdefault(method.family_id, method.family_name)
            if previous != method.family_name:
                raise MethodBankError("One bank family ID has inconsistent names.")
            index = len(records)
            identifiers[key] = index
            record_ids[method.record_id] = index
            records.append(method)
            for field, value in [
                ("family_id", method.family_id),
                ("domain", row["domain"]),
                ("material", row["test_material"]),
            ]:
                fields[field].setdefault(_text_key(value), set()).add(index)
        self._records = tuple(records)
        self._identifiers = MappingProxyType(identifiers)
        self._record_ids = MappingProxyType(record_ids)
        self._fields = {
            field: MappingProxyType({key: frozenset(value) for key, value in values.items()})
            for field, values in fields.items()
        }
        self.family_names = MappingProxyType(family_names)
        self.source_sha256 = source_sha256

    def __len__(self) -> int:
        return len(self._records)

    @classmethod
    def from_csv(cls, path: Path) -> MethodBankIndex:
        return cls.from_bytes(path.read_bytes())

    @classmethod
    def from_bytes(cls, content: bytes) -> MethodBankIndex:
        try:
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        except (UnicodeError, csv.Error) as exc:
            raise MethodBankError("The method bank is not a readable UTF-8 CSV.") from exc
        if not rows:
            raise MethodBankError("A method bank must contain at least one source row.")
        if any(
            any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in row.items()
            )
            for row in rows
        ):
            raise MethodBankError("CSV rows must have consistent column counts.")
        return cls(rows, source_sha256=hashlib.sha256(content).hexdigest())

    def lookup_identifier(self, identifier: str) -> BankMethod | None:
        index = self._identifiers.get(identifier_key(identifier))
        return self._records[index] if index is not None else None

    def lookup_record(self, record_id: str) -> BankMethod | None:
        index = self._record_ids.get(record_id)
        return self._records[index] if index is not None else None

    def search(
        self,
        *,
        identifier: str | None = None,
        family_id: str | None = None,
        domain: str | None = None,
        material: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> BankSearchPage:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Use a nonnegative offset and an explicit page size of 1–500.")
        indexes: set[int] | None = None
        if identifier is not None:
            index = self._identifiers.get(identifier_key(identifier))
            indexes = {index} if index is not None else set()
        for field, value in [("family_id", family_id), ("domain", domain), ("material", material)]:
            if value is None:
                continue
            found = set(self._fields[field].get(_text_key(value), ()))
            indexes = found if indexes is None else indexes & found
        ordered = range(len(self._records)) if indexes is None else sorted(indexes)
        total = len(ordered)
        selected = ordered[offset : offset + limit]
        return BankSearchPage(
            tuple(self._records[index] for index in selected), total, offset, limit
        )


@lru_cache(maxsize=1)
def _manifest_text() -> str:
    return files("scione").joinpath("catalogs/data/bank_manifest_v1.json").read_text()


def load_bank_manifest() -> dict:
    """Return independent source metadata without opening the complete bank."""

    return json.loads(_manifest_text())


@lru_cache(maxsize=1)
def load_synthetic_method_bank() -> MethodBankIndex:
    content = gzip.decompress(
        files("scione").joinpath("catalogs/data/synthetic_method_bank_v1.csv.gz").read_bytes()
    )
    expected = load_bank_manifest()["source_csv_sha256"]
    if hashlib.sha256(content).hexdigest() != expected:
        raise MethodBankError("Packaged method bank differs from its source provenance manifest.")
    return MethodBankIndex.from_bytes(content)

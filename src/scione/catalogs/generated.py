"""Bounded runtime identities derived from the team's synthetic metadata bank."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files

from scione.catalogs.bank import BankMethod, MethodBankError, identifier_key, load_bank_manifest
from scione.catalogs.models import Alias, CanonicalTest, ReferenceCatalog, TestMethod
from scione.catalogs.normalization import normalized_name


def canonical_test_id_for_family(family_id: str, catalog: ReferenceCatalog) -> str | None:
    """Resolve a bank family through its explicit packaged family mapping."""

    data = _runtime_rows()
    row = next((item for item in data["families"] if item["test_item_id"] == family_id), None)
    if row is None:
        return None
    names = {normalized_name(row["test_item_name"])}
    matches = [
        test.id
        for test in catalog.tests
        if names & {normalized_name(test.name), *(normalized_name(x.text) for x in test.aliases)}
    ]
    return matches[0] if len(matches) == 1 else None


def method_id_for_record(record: BankMethod, catalog: ReferenceCatalog) -> str | None:
    """Find an existing runtime method by its complete identifier, not its family."""

    key = identifier_key(record.identifier)
    matches = [method.id for method in catalog.methods if identifier_key(method.identifier) == key]
    return matches[0] if len(matches) == 1 else None


def _runtime_rows() -> dict:
    raw = files("scione").joinpath("catalogs/data/runtime_bank_excerpt_v1.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != load_bank_manifest()["runtime_excerpt_sha256"]:
        raise MethodBankError("Runtime method excerpt differs from its provenance manifest.")
    return json.loads(raw)


def enrich_reference_catalog(pilot: ReferenceCatalog) -> ReferenceCatalog:
    """Extend stable pilot IDs with curated bank identities, without lab claims.

    Only exact family names/previously curated aliases establish a shared family.
    Source-document phrasing, observations and expected results are never copied
    into aliases. Shared concept IDs never imply inter-method equivalence.
    """

    data = _runtime_rows()
    tests = [item.model_copy(deep=True) for item in pilot.tests]
    methods = [item.model_copy(deep=True) for item in pilot.methods]
    test_by_id = {item.id: item for item in tests}
    method_keys = {identifier_key(item.identifier): item for item in methods}
    families = {}
    for row in data["families"]:
        name = normalized_name(row["test_item_name"])
        matches = [
            test
            for test in tests
            if name
            in {normalized_name(test.name), *(normalized_name(x.text) for x in test.aliases)}
        ]
        if len(matches) > 1:
            raise MethodBankError("Ambiguous pilot family alias requires an explicit mapping.")
        if matches:
            test = matches[0]
        else:
            test_id = "bank_" + row["test_item_id"].lower().replace("-", "_")
            if test_id in test_by_id:
                raise MethodBankError("A generated family would overwrite an existing identity.")
            test = CanonicalTest(
                id=test_id,
                name=row["test_item_name"],
                description=(
                    f"Synthetic bank family {row['test_item_id']}; domain: {row['domain']}. "
                    "Associated methods are separate identities, not equivalent procedures or "
                    "lab capability declarations."
                ),
                aliases=[Alias(text=row["test_item_name"], language="en")],
            )
            tests.append(test)
            test_by_id[test.id] = test
        families[row["test_item_id"]] = test
    for row in data["methods"]:
        test = families[row["test_item_id"]]
        key = identifier_key(row["standard_id"])
        method = method_keys.get(key)
        if method is None:
            method = TestMethod(
                id="bank_" + row["record_id"].lower().replace("-", "_"),
                identifier=row["standard_id"],
                name=row["test_name"],
                edition=(
                    re.search(r"-(\d{2}|\d{4})$", row["standard_id"]).group(1)
                    if row["standard_body"] == "ASTM"
                    and re.search(r"-(\d{2}|\d{4})$", row["standard_id"])
                    else row["edition_year"]
                ),
                aliases=[Alias(text=row["standard_id"], language="und")],
            )
            methods.append(method)
            method_keys[key] = method
        if method.id not in test.method_ids:
            test.method_ids.append(method.id)
    return ReferenceCatalog(
        id=pilot.id,
        name="Capstone synthetic test identity catalog",
        version=4,
        synthetic=True,
        source_note=(
            pilot.source_note + " The data team's synthetic bank adds all 71 family names and "
            "77 explicitly selected method identities; full 32,000-row metadata is separately "
            "queryable and is not sent wholesale to a model. Exact pilot IDs are retained. "
            "Bank metadata does not populate any lab declaration or establish method "
            "equivalence. Source CSV SHA-256: " + data["source_csv_sha256"] + "."
        ),
        tests=tests,
        methods=methods,
    )

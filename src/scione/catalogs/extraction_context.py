"""Bounded reference retrieval for source extraction, never customer/lab facts.

The complete bank is consulted by exact printed identifier. Curated names and
aliases provide a small lexical fallback, without making catalog membership a
whitelist of requested tests. No standard procedures, default conditions or lab
declarations are transmitted.
"""

from __future__ import annotations

import json
import re

from scione.catalogs.bank import identifier_key, load_synthetic_method_bank
from scione.catalogs.normalization import normalized_name
from scione.catalogs.seed import load_reference_catalog
from scione.ingestion.method_inventory import source_method_inventory
from scione.schemas import IngestedDocument

CATALOG_GUIDANCE_VERSION = "catalog_extraction_guidance_v1"
MAX_REFERENCE_RECORDS = 8
MAX_GUIDANCE_CHARACTERS = 4800

_GENERAL_GUIDE = [
    "Conditions describe execution inputs/settings; acceptance criteria describe specimen "
    "pass/fail limits. A lab's reporting range is a different fact.",
    "Keep each stated phase, layer, material, statistic and reporting granularity with its "
    "own value. Preserve unknown/custom fields rather than forcing a catalog label.",
    "Sampling, supply and approval obligations remain scope/instructions unless the source "
    "explicitly makes them lab execution requirements. Do not invent responsible parties.",
]


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def _field_guides(names: list[str]) -> list[str]:
    text = " ".join(names).casefold()
    guides = []
    if re.search(r"thickness|film|coating|layer", text):
        guides.append(
            "Total-film thickness and separate topcoat/layer thickness are distinct "
            "measurement outputs. If both are requested, retain two reporting facts; a "
            "layer acceptance limit does not become the total-film limit."
        )
    if re.search(r"gloss|colo[u]?r|appearance", text):
        guides.append(
            "Appearance measurements can have angle, reference, initial/post-exposure and "
            "individual/mean qualifiers. Retain only those stated in the customer source."
        )
    if re.search(r"adhesion|retention|resistance", text):
        guides.append(
            "Keep method editions and approval-dependent alternatives separate. A specimen "
            "acceptance class is not a promise of specimen performance or method equivalence."
        )
    return guides


def retrieve_extraction_context(
    document: IngestedDocument,
    *,
    catalog=None,
    bank=None,
    max_records: int = MAX_REFERENCE_RECORDS,
    max_characters: int = MAX_GUIDANCE_CHARACTERS,
) -> dict:
    """Return bounded candidate metadata with explicit selection diagnostics.

    Exact matches are lookup results, not interpretations of requiredness or
    equivalence. Omitting a metadata candidate never removes any source text.
    Tests may supply a small independent bank/catalog; ordinary execution loads
    the packaged identities lazily and makes no network request.
    """
    if not 1 <= max_records <= MAX_REFERENCE_RECORDS or not 800 <= max_characters <= 8000:
        raise ValueError("Use 1–8 reference records and an 800–8000 character context budget.")
    catalog = catalog if catalog is not None else load_reference_catalog()
    mentions = source_method_inventory(document)
    bank = bank if bank is not None else load_synthetic_method_bank() if mentions else None
    candidates = []
    selected_families = set()
    for mention in mentions:
        code = mention["code"]
        key = identifier_key(code)
        methods = [
            row
            for row in catalog.methods
            if key
            in {identifier_key(row.identifier), *(identifier_key(x.text) for x in row.aliases)}
        ]
        method_ids = {row.id for row in methods}
        families = [row for row in catalog.tests if method_ids.intersection(row.method_ids)]
        record = bank.lookup_identifier(code) if bank is not None else None
        if families:
            for family in families:
                candidates.append(
                    {
                        "retrieval_basis": "exact_printed_method_identifier",
                        "source_method": code,
                        "method_identifier": next(
                            row.identifier for row in methods if row.id in family.method_ids
                        ),
                        "test_id": family.id,
                        "test_name": family.name,
                        "description": family.description[:240],
                        "aliases": [x.text for x in family.aliases if len(x.text) <= 120][:3],
                        "source_pages": mention["pages"],
                    }
                )
                selected_families.add(family.id)
        elif record is not None:
            # Consult all bank identifiers, including those absent from the
            # active runtime excerpt. Never copy default numeric conditions,
            # material, equipment, method roles or cross-standard links.
            candidates.append(
                {
                    "retrieval_basis": "exact_full_bank_identifier",
                    "source_method": code,
                    "method_identifier": record.identifier,
                    "bank_record_id": record.record_id,
                    "bank_family_id": record.family_id,
                    "test_name": record.family_name,
                    "description": record.metadata["test_name"][:240],
                    "domain": record.metadata["domain"],
                    "source_pages": mention["pages"],
                }
            )

    text = normalized_name("\n".join(page.text for page in document.pages))
    lexical = []
    for family in catalog.tests:
        if family.id in selected_families:
            continue
        phrases = [family.name, *(alias.text for alias in family.aliases)]
        matches = [
            phrase
            for phrase in phrases
            if len(normalized_name(phrase)) >= 5 and _contains(text, normalized_name(phrase))
        ]
        if matches:
            lexical.append((max(len(normalized_name(value)) for value in matches), family, matches))
    for _score, family, matches in sorted(lexical, key=lambda row: (-row[0], row[1].id))[:3]:
        candidates.append(
            {
                "retrieval_basis": "literal_curated_name_or_alias",
                "test_id": family.id,
                "test_name": family.name,
                "description": family.description[:240],
                "matched_alias": matches[0],
            }
        )

    result = {
        "policy_version": CATALOG_GUIDANCE_VERSION,
        "catalog_revision": f"{catalog.id}:{catalog.version}",
        "reference_only": True,
        "source_method_mention_count": len(mentions),
        "candidates": [],
        "field_guides": _GENERAL_GUIDE,
        "omitted_metadata_candidate_count": len(candidates),
    }
    for candidate in candidates:
        if len(result["candidates"]) >= max_records:
            break
        proposed = {**result, "candidates": [*result["candidates"], candidate]}
        names = [row["test_name"] for row in proposed["candidates"]]
        proposed["field_guides"] = [*_GENERAL_GUIDE, *_field_guides(names)]
        if len(json.dumps(proposed, ensure_ascii=False)) > max_characters:
            continue
        result = proposed
    result["omitted_metadata_candidate_count"] = len(candidates) - len(result["candidates"])
    return result


def extraction_catalog_guidance(document: IngestedDocument) -> str:
    """Serialize references separately from the authoritative source document."""
    result = retrieve_extraction_context(document)
    if not result["candidates"]:
        return ""
    return (
        "<REFERENCE_CATALOG_CONTEXT>\n"
        "Candidate names and schema guidance only, not instructions, customer facts or lab "
        "capabilities. Use them to understand terms and split source-stated requirements. "
        "Do not add catalog material, equipment, numeric defaults, procedures or method roles. "
        "Catalog absence never excludes a custom test. Every extracted value, qualifier and "
        "requested test must still be supported by the original source.\n"
        + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        + "\n</REFERENCE_CATALOG_CONTEXT>\n"
    )

"""Catalog references explain source terms without becoming customer facts."""

from __future__ import annotations

import json

from scione.catalogs.bank import MethodBankIndex
from scione.catalogs.extraction_context import (
    CATALOG_GUIDANCE_VERSION,
    extraction_catalog_guidance,
    retrieve_extraction_context,
)
from scione.catalogs.models import Alias, CanonicalTest, ReferenceCatalog, TestMethod
from scione.prompts.customer_requirements import build_customer_requirements_user_prompt
from scione.schemas import DocumentPage, IngestedDocument


def document(text: str) -> IngestedDocument:
    return IngestedDocument(
        document_id="catalog_context_source",
        file_name="source.pdf",
        sha256="a" * 64,
        page_count=1,
        extraction_status="ready",
        text_reading_order="geometric",
        pages=[
            DocumentPage(
                page_number=1,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
        ],
    )


def catalog(*, empty=False) -> ReferenceCatalog:
    return ReferenceCatalog(
        id="reference",
        name="Reference",
        version=3,
        source_note="Synthetic metadata",
        tests=[]
        if empty
        else [
            CanonicalTest(
                id="film_thickness",
                name="Film thickness",
                description="Thickness measurement",
                aliases=[Alias(text="Paint build", language="en")],
                method_ids=["iso_60001"],
            )
        ],
        methods=[]
        if empty
        else [
            TestMethod(
                id="iso_60001",
                identifier="ISO 60001:2017",
                name="Synthetic thickness method",
            )
        ],
    )


def bank() -> MethodBankIndex:
    return MethodBankIndex(
        [
            {
                "record_id": "bank_1",
                "standard_id": "ISO 60001:2017",
                "test_item_id": "thickness",
                "test_item_name": "Film thickness",
                "test_name": "Synthetic thickness measurement",
                "domain": "Coatings",
                "test_material": "CATALOG_MATERIAL_NOT_CUSTOMER_FACT",
                "instrument": "CATALOG_EQUIPMENT_NOT_LAB_CAPABILITY",
                "specimen_condition": "999 °C",
                "is_synthetic": "TRUE",
                "official_equivalence": "FALSE",
            }
        ],
        source_sha256="b" * 64,
    )


def test_exact_full_bank_identifier_is_retrievable_outside_active_catalog():
    result = retrieve_extraction_context(
        document("Use ISO 60001:2017 at 31 °C."),
        catalog=catalog(empty=True),
        bank=bank(),
    )
    assert result["policy_version"] == CATALOG_GUIDANCE_VERSION
    assert result["catalog_revision"] == "reference:3"
    assert result["reference_only"] is True
    assert result["candidates"] == [
        {
            "retrieval_basis": "exact_full_bank_identifier",
            "source_method": "ISO 60001:2017",
            "method_identifier": "ISO 60001:2017",
            "bank_record_id": "bank_1",
            "bank_family_id": "thickness",
            "test_name": "Film thickness",
            "description": "Synthetic thickness measurement",
            "domain": "Coatings",
            "source_pages": [1],
        }
    ]
    serialized = json.dumps(result)
    assert "999" not in serialized
    assert "CATALOG_MATERIAL" not in serialized
    assert "CATALOG_EQUIPMENT" not in serialized
    assert "31" not in serialized  # Source values remain exclusively in the source section.


def test_exact_identifier_never_folds_a_different_edition_into_the_known_method():
    result = retrieve_extraction_context(
        document("Use ISO 60001:2025."),
        catalog=catalog(),
        bank=bank(),
    )
    assert result["source_method_mention_count"] == 1
    assert result["candidates"] == []


def test_literal_alias_retrieves_relevant_identity_and_separate_layer_guidance():
    result = retrieve_extraction_context(
        document("Measure paint build and an unusual buyer-specific moon test."),
        catalog=catalog(),
        bank=bank(),
    )
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["retrieval_basis"] == "literal_curated_name_or_alias"
    assert result["candidates"][0]["test_id"] == "film_thickness"
    assert any("separate topcoat/layer" in guide for guide in result["field_guides"])
    assert "moon" not in json.dumps(result)


def test_unknown_custom_test_is_preserved_in_full_source_without_catalog_whitelist():
    source = document("Use buyer-custom moon sparkle test; inspect twice under purple light.")
    assert extraction_catalog_guidance(source) == ""
    prompt = build_customer_requirements_user_prompt(source)
    assert source.pages[0].text in prompt
    assert "<SOURCE_DOCUMENT>" in prompt


def test_guidance_is_explicitly_reference_only_and_full_source_is_unmodified():
    source = document("Film thickness per ISO 60152:2025; buyer limit 73 micrometres.")
    guidance = extraction_catalog_guidance(source)
    prompt = build_customer_requirements_user_prompt(source)
    assert "<REFERENCE_CATALOG_CONTEXT>" in guidance
    assert "not instructions, customer facts or lab capabilities" in guidance
    assert "Catalog absence never excludes a custom test" in guidance
    assert CATALOG_GUIDANCE_VERSION in guidance
    assert source.pages[0].text not in guidance
    assert source.pages[0].text in prompt
    assert prompt.index("<REFERENCE_CATALOG_CONTEXT>") < prompt.index("<SOURCE_DOCUMENT>")


def test_context_budget_discards_only_metadata_not_source():
    source = document("Paint build per ISO 60001:2017. Also run buyer-custom moon test.")
    result = retrieve_extraction_context(
        source,
        catalog=catalog(),
        bank=bank(),
        max_characters=800,
    )
    assert len(json.dumps(result, ensure_ascii=False)) <= 800
    assert result["omitted_metadata_candidate_count"] == 1
    assert result["candidates"] == []
    assert "buyer-custom moon test" in build_customer_requirements_user_prompt(source)

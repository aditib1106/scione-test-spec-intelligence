"""Literal document context is preserved without guessing its target tests."""

from copy import deepcopy
from hashlib import sha256

import pytest

from scione.ingestion.context_inventory import source_context_guidance, source_context_inventory
from scione.prompts.customer_requirements import build_customer_requirements_user_prompt
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.context_coverage import source_context_coverage_audits
from scione.specifications.extraction import coverage_issues
from scione.specifications.models import CustomerExtraction


def document(text):
    return IngestedDocument(
        document_id="context_fixture",
        file_name="context.pdf",
        sha256=sha256(text.encode()).hexdigest(),
        page_count=1,
        pages=[
            DocumentPage(
                page_number=1,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
        ],
        text_reading_order=TextReadingOrder.GEOMETRIC,
        extraction_status=TextExtractionStatus.READY,
    )


def extraction():
    return CustomerExtraction.model_validate(
        {
            "schema_version": "0.1",
            "document": {},
            "requirements": [
                {
                    "id": "req_a",
                    "original_test_name": "A custom observation",
                    "evidence": [{"page": 1, "quote": "Visual observation is required."}],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "label,value",
    [
        ("Covered supply", "Anodized aluminium extrusions with bonded rubber inserts"),
        ("Test article", "Porous ceramic coupons"),
        ("Specimen material", "Woven textile type TX-19"),
        ("Material under test", "Polymer multilayer finish on S-88 alloy"),
    ],
)
def test_label_value_is_literal_with_offsets_and_no_inferred_applicability(label, value):
    doc = document(f"  {label}  {value}\nVisual observation is required.")
    cues = source_context_inventory(doc)
    assert len(cues) == 1
    cue = cues[0]
    assert cue.value == value
    assert doc.pages[0].text[cue.source_start : cue.source_end] == cue.quote
    before = deepcopy(extraction().model_dump(mode="json"))
    audit = source_context_coverage_audits(extraction(), doc)
    assert audit[0]["value"] == value
    assert audit[0]["applicability_inferred"] is False
    assert audit[0]["semantic_omission_proved"] is False
    assert extraction().model_dump(mode="json") == before


def test_release_approval_literal_preserves_wrapping_and_qualifiers():
    text = (
        "A passing result is not release. "
        "Release requires written approval from\n\n Team Q unless waived."
    )
    doc = document(text)
    cue = source_context_inventory(doc)[0]
    assert cue.kind == "release_approval"
    assert cue.quote == "Release requires written approval from\n\n Team Q unless waived."
    assert text[cue.source_start : cue.source_end] == cue.quote
    assert "unless waived" in cue.value


@pytest.mark.parametrize(
    "text",
    [
        "We discuss Covered supply as a generic heading.",
        "Not covered supply: rejected panels.",
        "Covered supply:   \nAnother header.",
        "If release requires written approval from Team Q, contact them.",
        "Release does not require written approval from Team Q.",
        "Release requires no approval from Team Q.",
        "The phrase 'Release requires written approval from Team Q.' is quoted as an example.",
    ],
)
def test_nonlabel_empty_or_negated_conditional_approval_is_not_promoted_to_a_cue(text):
    assert source_context_inventory(document(text)) == []


def test_evidence_only_copy_does_not_disguise_missing_owned_context_and_paraphrase_is_only_a_hint():
    doc = document("Covered supply: Ceramic articles\nVisual observation is required.")
    data = extraction()
    data.requirements[0].evidence[0].quote = doc.pages[0].text
    assert len(source_context_coverage_audits(data, doc)) == 1
    data.requirements[0].applicability = ["Ceramic parts are assessed."]
    hint = coverage_issues(data, doc)
    assert len(hint) == 1
    assert "Check source context" in hint[0]
    assert "may already be expressed in other words" in hint[0]
    data.requirements[0].applicability = ["Ceramic articles"]
    assert source_context_coverage_audits(data, doc) == []


def test_source_cues_are_bounded_untrusted_guidance_beside_the_complete_source():
    doc = document("\n".join(f"Test article: Ceramic type {index}" for index in range(12)))
    guide = source_context_guidance(doc)
    assert guide.count('"kind"') == 8
    assert "do not assume all tests or invent targets" in guide
    prompt = build_customer_requirements_user_prompt(doc)
    assert "<SOURCE_CONTEXT_CUES" in prompt
    assert doc.as_prompt_text() in prompt
    assert "Test article: Ceramic type 11" in prompt


def test_not_ready_source_never_becomes_a_physical_context_hint():
    doc = document("Test article: Ceramic articles")
    doc.extraction_status = TextExtractionStatus.POSSIBLE_SCANNED_PDF
    assert source_context_inventory(doc) == []

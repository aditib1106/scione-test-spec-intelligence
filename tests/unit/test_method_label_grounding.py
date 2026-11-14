"""Method-format grounding is auditable and cannot remove scientific qualifiers."""

from scione.providers import StaticModelProvider
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.extraction import CustomerRequirementRunner
from scione.specifications.models import CustomerExtractionRun


def run(reference):
    text = f"Colour difference. Use {reference}. Measure at 23 °C."
    doc = IngestedDocument(
        document_id="literal_label", file_name="synthetic.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(page_number=1, text=text, character_count=len(text),
                            non_whitespace_character_count=len("".join(text.split())))],
    )
    payload = {
        "document": {}, "requirements": [{
            "test": "Colour difference", "methods": [reference],
            "conditions": ["Measure at 23 °C."],
            "evidence": [{"page": 1, "quote": text}],
        }],
    }
    return CustomerRequirementRunner(StaticModelProvider(payload)).run(
        doc, strategy="compact_document"
    )


def test_exact_bank_family_suffix_keeps_original_reference_in_source_grounding_audit():
    result = run("ISO 60570:2025 (COR-11)")
    assert result.extraction.requirements[0].requested_methods[0].code == "ISO 60570:2025"
    action = result.grounding_actions[-1]
    assert action.action == "normalized_method_label" and action.family_id == "COR-11"
    assert action.raw_reference == "ISO 60570:2025 (COR-11)"
    assert action.source_start <= action.code_start < action.code_end < action.source_end
    assert action.raw_reference in result.raw_response
    assert CustomerExtractionRun.model_validate_json(result.model_dump_json()) == result


def test_modified_or_unknown_parentheses_are_not_treated_as_a_bank_label():
    for reference in ["ISO 60570:2025 (modified)", "ISO 60570:2025 (COR-11; modified)"]:
        result = run(reference)
        assert result.extraction.requirements[0].requested_methods[0].code == reference
        assert not any(a.action == "normalized_method_label" for a in result.grounding_actions)

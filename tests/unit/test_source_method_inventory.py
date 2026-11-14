"""Source checklists retain editions without inventing methods or test roles."""

from scione.ingestion.method_inventory import source_method_inventory
from scione.prompts.customer_requirements import build_customer_requirements_user_prompt
from scione.schemas import DocumentPage, IngestedDocument


def document(*texts):
    return IngestedDocument(
        document_id="method_mentions", file_name="synthetic.pdf", sha256="a" * 64,
        page_count=len(texts), extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(page_number=n, text=t, character_count=len(t),
                            non_whitespace_character_count=len("".join(t.split())))
               for n, t in enumerate(texts, 1)],
    )


def test_checklist_preserves_full_editions_parts_and_adoption_prefixes():
    source = document(
        "Use DIN EN ISO 12345-2:2024-08. Alternatively ASTM D1234-20. "
        "Also GB/T 230.1-2018; this mention alone does not declare a requested test.",
        "DIN EN ISO 12345-2:2024-08 remains distinct from ISO 12345-2:2024.",
    )
    assert source_method_inventory(source) == [
        {"code": "DIN EN ISO 12345-2:2024-08", "pages": [1, 2]},
        {"code": "ASTM D1234-20", "pages": [1]},
        {"code": "GB/T 230.1-2018", "pages": [1]},
        {"code": "ISO 12345-2:2024", "pages": [2]},
    ]


def test_checklist_is_source_only_and_does_not_guess_custom_methods_or_bad_revisions():
    source = document("Custom check QZ-17. Malformed ISO 123:20 and ISO 123-. Length 20 mm.")
    assert source_method_inventory(source) == []
    assert source_method_inventory(document("采用ISO 12345:2024测试。")) == [
        {"code": "ISO 12345:2024", "pages": [1]},
    ]


def test_prompt_labels_mentions_as_nonexhaustive_not_test_inventory_or_equivalence():
    text = build_customer_requirements_user_prompt(document("Read ISO 12345:2024."))
    checklist = text.split("<SOURCE_METHOD_MENTIONS>")[1].split("</SOURCE_METHOD_MENTIONS>")[0]
    assert "ISO 12345:2024 | physical pages: 1" in checklist
    assert "NOT a list of requested tests" in checklist
    assert "ASTM" not in checklist

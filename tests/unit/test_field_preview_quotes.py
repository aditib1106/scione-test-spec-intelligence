from scione.specifications.models import CustomerRequirement
from scione.specifications.preview_quotes import field_preview_quotes


def test_field_quotes_preserve_item_page_ownership_and_fall_back_without_fuzzy_guessing():
    item = CustomerRequirement.model_validate(
        {
            "id": "req_a",
            "original_test_name": "Translated test name",
            "requested_methods": [{"code": "ISO 15184"}],
            "conditions": [{"name": "Temperature", "raw_value": "23 degrees C"}],
            "acceptance_criteria": [{"name": "Hardness", "raw_value": "at least F"}],
            "evidence": [
                {"page": 1, "quote": "Pencil hardness ISO 15184 at 23 degrees C."},
                {"page": 2, "quote": "The required hardness is at least F."},
            ],
        }
    )
    assert field_preview_quotes(item, 1, "method") == (["ISO 15184"], "field")
    assert field_preview_quotes(item, 1, "condition") == (["23 degrees C"], "field")
    assert field_preview_quotes(item, 2, "criterion") == (["at least F"], "field")
    assert field_preview_quotes(item, 1, "name") == ([item.evidence[0].quote], "item")
    assert field_preview_quotes(item, 1, "criterion") == ([item.evidence[0].quote], "item")
    assert field_preview_quotes(item, 3, "method") == ([], "none")

"""A defective attachment cannot erase an independently valid source inventory."""

import copy

import pytest
from pydantic import ValidationError

from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.inventory import (
    CustomerTestInventory,
    ground_inventory,
    parse_inventory_payload,
)

SOURCE = "Test alpha is requested. Test beta is requested. All except beta use 50 °C."


def proposal():
    return {
        "tests": [
            {"test": "Alpha", "evidence": [{"page": 1, "quote": "Test alpha is requested."}]},
            {"test": "Beta", "evidence": [{"page": 1, "quote": "Test beta is requested."}]},
        ],
        "attachments": [{
            "relation": "shared", "applies_to": [1],
            "evidence": [{"page": 1, "quote": "All except beta use 50 °C."}],
        }],
        "uncertainties": ["Source applicability requires review."],
    }


def source():
    return IngestedDocument(
        document_id="inventory-source", file_name="source.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=SOURCE, character_count=len(SOURCE),
            non_whitespace_character_count=len("".join(SOURCE.split())),
        )],
    )


def test_valid_payload_keeps_all_indices_and_ordinary_schema_behavior():
    payload = proposal()
    before = copy.deepcopy(payload)
    result = parse_inventory_payload(payload)
    assert result.inventory == CustomerTestInventory.model_validate(payload)
    assert result.unused_attachment_indices == result.issues == ()
    assert result.kept_attachment_original_indices == (1,)
    assert payload == before


def test_dangling_shared_exception_is_wholly_unused_without_trimming_or_guessing():
    payload = proposal()
    payload["attachments"][0]["applies_to"] = [1, 2, 3]
    before = copy.deepcopy(payload)
    result = parse_inventory_payload(payload)
    assert [test.test for test in result.inventory.tests] == ["Alpha", "Beta"]
    assert result.inventory.tests == CustomerTestInventory.model_validate({
        **payload, "attachments": [],
    }).tests
    assert result.inventory.attachments == []
    assert result.unused_attachment_indices == (1,)
    assert result.kept_attachment_original_indices == ()
    assert len(result.issues) == 1 and "attachment 1" in result.issues[0]
    assert "existing tests" in result.issues[0]
    assert payload == before


@pytest.mark.parametrize("change", [
    {"relation": "invented_relation"},
    {"applies_to": [1, 1]},
    {"applies_to": [0]},
    {"applies_to": ["not an index"]},
    {"evidence": []},
    {"unexpected": "cannot become accepted source facts"},
])
def test_each_invalid_attachment_is_audited_without_discarding_other_entries(change):
    payload = proposal()
    invalid = {**copy.deepcopy(payload["attachments"][0]), **change}
    payload["attachments"].insert(0, invalid)
    before = copy.deepcopy(payload)
    result = parse_inventory_payload(payload)
    assert result.unused_attachment_indices == (1,)
    assert result.kept_attachment_original_indices == (2,)
    assert result.inventory.attachments[0].model_dump() == payload["attachments"][1]
    assert [test.test for test in result.inventory.tests] == ["Alpha", "Beta"]
    assert "attachment 1 was unused" in result.issues[0]
    assert payload == before


def test_grounding_omitted_attachment_indices_map_back_to_original_provider_positions():
    payload = proposal()
    attachment = payload["attachments"][0]
    payload["attachments"] = [
        {**copy.deepcopy(attachment), "applies_to": [1, 9]},
        {**copy.deepcopy(attachment), "relation": "invented_relation"},
        copy.deepcopy(attachment),
        {**copy.deepcopy(attachment), "evidence": [{"page": 1, "quote": "Not in source"}]},
    ]
    result = parse_inventory_payload(payload)
    grounded = ground_inventory(result.inventory, source())
    assert result.unused_attachment_indices == (1, 2)
    assert result.kept_attachment_original_indices == (3, 4)
    assert grounded.omitted_attachment_indexes == (2,)
    assert tuple(result.kept_attachment_original_indices[index - 1]
                 for index in grounded.omitted_attachment_indexes) == (4,)
    assert grounded.proposal.attachments[0].evidence[0].quote == attachment["evidence"][0]["quote"]
    assert grounded.proposal.attachments[0].applies_to == [1]


@pytest.mark.parametrize("change", [
    "missing_tests", "invalid_test", "extra_root", "invalid_uncertainties",
    "attachment_container", "too_many_attachments",
])
def test_root_or_test_schema_errors_cannot_be_repaired_by_attachment_filtering(change):
    payload = proposal()
    payload["attachments"][0]["applies_to"] = [1, 99]
    if change == "missing_tests":
        del payload["tests"]
    elif change == "invalid_test":
        del payload["tests"][1]["test"]
    elif change == "extra_root":
        payload["unknown"] = "root"
    elif change == "invalid_uncertainties":
        payload["uncertainties"] = [17]
    elif change == "attachment_container":
        payload["attachments"] = {"relation": "shared"}
    else:
        payload["attachments"] *= 301
    before = copy.deepcopy(payload)
    with pytest.raises(ValidationError):
        parse_inventory_payload(payload)
    assert payload == before


def test_schema_valid_test_duplicates_and_order_are_never_changed():
    payload = proposal()
    payload["tests"].append(copy.deepcopy(payload["tests"][0]))
    payload["attachments"][0]["applies_to"] = [1, 2, 3, 4]
    result = parse_inventory_payload(payload)
    assert [test.test for test in result.inventory.tests] == ["Alpha", "Beta", "Alpha"]
    assert result.inventory.attachments == []

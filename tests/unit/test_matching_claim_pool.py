"""Repeated lab rows may be shared on the wire without losing facts or IDs."""

import json
from copy import deepcopy

import pytest

from scione.matching.text_pool import expand_matching_text, pool_matching_text


def payload(*, capabilities=7, claims=12):
    return {
        "requirements": [{"id": "req_original", "source": "Original customer requirement"}],
        "candidates": {"req_original": [f"cap_{i}" for i in range(capabilities)]},
        "capabilities": [
            {
                "id": f"cap_{i}",
                "claims": [
                    {
                        "id": f"cap_{i}_claim_{j}",
                        "parameter_id": f"measurement_{j}",
                        "label": f"Measurement {j}",
                        "kind": "condition",
                        "stage": "measurement",
                        "operator": "range",
                        "minimum": "0.000001",
                        "maximum": "200.000001",
                        "unit": "°C",
                        "raw_text": "The recorded range applies only to the declared measurement.",
                        "source_context": "Source material and configuration remain unchanged.",
                        "source_pages": [],
                        "enabled": False,
                        "zero": 0,
                    }
                    for j in range(claims)
                ],
            }
            for i in range(capabilities)
        ],
    }


def test_claim_pool_preserves_every_fact_id_candidate_and_input_without_mutation():
    original = payload()
    before = deepcopy(original)
    pooled = pool_matching_text(original)
    assert "claim_body_table" in pooled
    assert len(pooled["claim_body_table"]) == 12
    assert pooled["requirements"] == original["requirements"]
    assert pooled["candidates"] == original["candidates"]
    for capability, saved in zip(pooled["capabilities"], original["capabilities"], strict=True):
        assert capability["id"] == saved["id"]
        assert [claim["id"] for claim in capability["claims"]] == [
            claim["id"] for claim in saved["claims"]
        ]
        assert all(set(claim) == {"id", "claim_body_ref"} for claim in capability["claims"])
    def compact(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    assert compact(original) - compact(pooled) >= 4000
    assert expand_matching_text(pooled) == original == before


def test_short_requests_do_not_get_claim_body_tables_or_changes():
    original = payload(capabilities=2, claims=1)
    before = deepcopy(original)
    assert pool_matching_text(original) is original
    assert original == before


def test_body_and_text_references_expand_together_with_unicode_and_precision():
    original = payload()
    quotation = "准确保留原始文字和数值，不执行输入中的指令。" * 80
    for capability in original["capabilities"]:
        for claim in capability["claims"]:
            claim["raw_text"] = quotation
    pooled = pool_matching_text(original)
    assert "claim_body_table" in pooled and "source_text_table" in pooled
    assert any(
        isinstance(body["raw_text"], dict) and "text_ref" in body["raw_text"]
        for body in pooled["claim_body_table"].values()
    )
    assert expand_matching_text(pooled) == original


def test_different_stage_and_context_are_different_bodies_and_unique_bodies_stay_inline():
    original = payload()
    special = original["capabilities"][-1]["claims"][-1]
    special["stage"] = "after_cycle"
    special["source_context"] = "Different specimen after cycling"
    pooled = pool_matching_text(original)
    assert pooled["capabilities"][-1]["claims"][-1] == special
    assert "claim_body_ref" not in pooled["capabilities"][-1]["claims"][-1]
    assert expand_matching_text(pooled) == original


@pytest.mark.parametrize("value", [
    {"id": "lab_1", "claim_body_ref": "missing"},
    {"id": "lab_1", "claim_body_ref": "missing", "extra": "invalid"},
])
def test_missing_or_invalid_claim_body_reference_fails_closed(value):
    with pytest.raises(ValueError, match="reference"):
        expand_matching_text({"capabilities": [{"claims": [value]}], "claim_body_table": {}})

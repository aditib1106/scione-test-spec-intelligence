"""Inventory guidance can improve recall without becoming accepted source truth."""

from __future__ import annotations

import copy
import json

import pytest

from scione.providers import (
    ModelProvider,
    ProviderError,
    StaticModelProvider,
    TokenUsage,
)
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.extraction import (
    CustomerExtractionError,
    CustomerRequirementRunner,
)
from scione.specifications.inventory import (
    INVENTORY_PROMPT_VERSION,
    CustomerTestInventory,
    ground_inventory,
    inventory_guidance,
)
from scione.specifications.models import CustomerExtractionRun

SOURCE = (
    "Adhesion per ISO 2409: temperature 31 °C; acceptance grade <=1.\n"
    "Hardness per ISO 15184: load 750 g; acceptance >=F.\n"
    "Record grade with photographs; the calculation explanation is not a second test."
)


def document():
    return IngestedDocument(
        document_id="inventory_source", file_name="inventory.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=SOURCE, character_count=len(SOURCE),
            non_whitespace_character_count=len("".join(SOURCE.split())),
        )],
    )


def inventory():
    return {
        "tests": [{"test": "Adhesion", "evidence": [
            {"page": 1, "quote": SOURCE.splitlines()[0]},
        ]}],
        "attachments": [{
            "relation": "calculation", "applies_to": [1], "evidence": [{
                "page": 1, "quote": SOURCE.splitlines()[2],
            }],
        }],
        "uncertainties": ["INVENTED_QUESTION_FROM_AN_EARLIER_READER"],
    }


def extraction():
    return {
        "document": {"title": None, "identifier": None, "revision": None},
        "shared_requirements": [], "review_issues": [],
        "requirements": [{
            "test": "Adhesion", "methods": ["ISO 2409"], "method_roles": [],
            "conditions": ["temperature 31 °C"], "criteria": ["acceptance grade <=1"],
            "scope": [], "instructions": [], "atomic_claims": [], "uncertainty": None,
            "evidence": [{"page": 1, "quote": SOURCE.splitlines()[0]}],
        }],
    }


class SequenceProvider(ModelProvider):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    @property
    def provider_name(self):
        return "static"

    @property
    def model_name(self):
        return "fixture"

    def generate_structured(self, request):
        self.requests.append(request)
        value = self.outputs[len(self.requests) - 1]
        if isinstance(value, Exception):
            raise value
        response = StaticModelProvider(value).generate_structured(request)
        return response.model_copy(update={
            "usage": TokenUsage(input_tokens=11, output_tokens=7, total_tokens=18),
            "latency_ms": 9.0,
            "provider_request_id": f"fixture-{len(self.requests)}",
        })


def run(provider, **kwargs):
    return CustomerRequirementRunner(provider, **kwargs).run(
        document(), strategy="inventory_compact_document"
    )


def test_two_bounded_calls_preserve_both_raw_outputs_requests_purposes_and_usage():
    plan, facts = inventory(), extraction()
    original = copy.deepcopy((plan, facts))
    provider = SequenceProvider([plan, facts])
    result = run(provider)
    assert len(provider.requests) == 2
    assert [call.purpose for call in result.calls] == ["inventory", "extraction"]
    assert result.calls[0].prompt_version == INVENTORY_PROMPT_VERSION
    assert result.calls[1].prompt_version == result.prompt_version
    assert [call.call_index for call in result.calls] == [1, 2]
    assert all(call.request is not None for call in result.calls)
    assert json.loads(result.calls[0].raw_response) == plan
    assert json.loads(result.calls[1].raw_response) == facts
    assert result.raw_response_kind == "inventory_guided_manifest"
    assert json.loads(result.raw_response)["calls"][1]["raw_response_path"] == (
        "calls[1].raw_response"
    )
    assert result.inventory_audit.status == "source_verified_guidance"
    assert result.inventory_audit.guidance_sha256
    assert result.usage == TokenUsage(input_tokens=22, output_tokens=14, total_tokens=36)
    assert result.latency_ms == 18
    assert SOURCE in provider.requests[0].user_prompt
    assert SOURCE in provider.requests[1].user_prompt
    assert "<SOURCE_INVENTORY_DRAFT>" in provider.requests[1].user_prompt
    assert "INVENTED_QUESTION_FROM_AN_EARLIER_READER" not in provider.requests[1].user_prompt
    assert (plan, facts) == original
    assert CustomerExtractionRun.model_validate_json(result.model_dump_json()) == result


def test_both_source_readers_receive_the_same_bounded_reference_context():
    source = document()
    text = SOURCE + "\nMeasure gloss per ASTM D1627-16."
    source.pages[0] = source.pages[0].model_copy(update={
        "text": text, "character_count": len(text),
        "non_whitespace_character_count": len("".join(text.split())),
    })
    provider = SequenceProvider([inventory(), extraction()])
    CustomerRequirementRunner(provider).run(source, strategy="inventory_compact_document")
    contexts = []
    for request in provider.requests:
        assert "<REFERENCE_CATALOG_CONTEXT>" in request.user_prompt
        assert "catalog_extraction_guidance_v1" in request.user_prompt
        contexts.append(request.user_prompt.split("<REFERENCE_CATALOG_CONTEXT>")[1].split(
            "</REFERENCE_CATALOG_CONTEXT>"
        )[0])
        assert text in request.user_prompt
    assert contexts[0] == contexts[1]


@pytest.mark.parametrize("mutation,status", [
    ("invented_quote", "unused_invalid_source"),
    ("wrong_page", "unused_invalid_source"),
    ("invalid_shape", "unused_invalid_schema"),
    ("empty", "unused_empty_inventory"),
])
def test_untrusted_inventory_is_unused_but_full_source_extraction_still_runs(mutation, status):
    plan = inventory()
    if mutation == "invented_quote":
        plan["tests"][0]["evidence"][0]["quote"] = "INVENTED_SOURCE_999_DEGREES"
    elif mutation == "wrong_page":
        plan["tests"][0]["evidence"][0]["page"] = 9
    elif mutation == "invalid_shape":
        plan = {"unexpected": True}
    else:
        plan = {"tests": [], "attachments": [], "uncertainties": []}
    provider = SequenceProvider([plan, extraction()])
    result = run(provider)
    assert len(provider.requests) == 2
    assert result.inventory_audit.status == status
    assert result.inventory_audit.guidance_sha256 is None
    assert "<SOURCE_INVENTORY_DRAFT>" not in provider.requests[1].user_prompt
    assert SOURCE in provider.requests[1].user_prompt
    assert "INVENTED_SOURCE_999_DEGREES" not in provider.requests[1].user_prompt
    assert len(result.extraction.requirements) == 1
    assert json.loads(result.calls[0].raw_response) == plan


def test_dangling_attachment_is_unused_but_independently_valid_test_guidance_survives():
    plan = inventory()
    # One valid and one dangling target must discard the entire attachment,
    # rather than silently trimming its applicability to the remaining test.
    plan["attachments"][0]["applies_to"] = [1, 2]
    original = copy.deepcopy(plan)
    source = document()
    source_before = source.model_dump_json()
    provider = SequenceProvider([plan, extraction()])

    result = CustomerRequirementRunner(provider).run(
        source, strategy="inventory_compact_document"
    )

    assert len(provider.requests) == 2
    assert result.inventory_audit.status == "source_verified_partial_guidance"
    assert result.inventory_audit.guidance_sha256
    assert result.inventory_audit.omitted_test_indexes == []
    assert result.inventory_audit.omitted_attachment_indexes == [1]
    assert any("attachment 1" in issue and "existing tests" in issue
               for issue in result.inventory_audit.issues)
    prompt = provider.requests[1].user_prompt
    assert "inventory guide is incomplete" in prompt
    guide = json.loads(prompt.split("<SOURCE_INVENTORY_DRAFT>\n", 1)[1].split(
        "\n</SOURCE_INVENTORY_DRAFT>", 1
    )[0])
    assert guide["tests"] == original["tests"]
    assert guide["attachments"] == []
    assert SOURCE in prompt
    assert len(result.extraction.requirements) == 1
    assert json.loads(result.calls[0].raw_response) == original
    assert plan == original
    assert source.model_dump_json() == source_before


def test_salvage_and_grounding_audits_both_use_original_raw_attachment_positions():
    plan = inventory()
    plan["tests"].append({
        "test": "Unlocated work", "evidence": [{"page": 1, "quote": "UNLOCATED TEST"}],
    })
    attachment = plan["attachments"][0]
    plan["attachments"] = [
        {**copy.deepcopy(attachment), "applies_to": [1, 3]},
        {**copy.deepcopy(attachment), "evidence": [{"page": 1, "quote": "UNLOCATED NOTE"}]},
        {**copy.deepcopy(attachment), "applies_to": [2]},
        copy.deepcopy(attachment),
    ]
    original = copy.deepcopy(plan)
    provider = SequenceProvider([plan, extraction()])

    result = run(provider)

    audit = result.inventory_audit
    assert audit.status == "source_verified_partial_guidance"
    assert audit.omitted_test_indexes == [2]
    assert audit.omitted_attachment_indexes == [1, 2, 3]
    assert any(issue.startswith("Inventory attachment 1 was unused:") for issue in audit.issues)
    assert (
        "Inventory attachment 2 evidence 1 is not a literal fragment on physical page 1."
        in audit.issues
    )
    assert (
        "Inventory attachment 3 was unused because not all target tests had "
        "independently located evidence." in audit.issues
    )
    assert not any(issue.startswith("Inventory attachment 1 evidence") for issue in audit.issues)
    assert not any(issue.startswith("Inventory attachment 2 was unused because")
                   for issue in audit.issues)
    prompt = provider.requests[1].user_prompt
    assert SOURCE in prompt and "inventory guide is incomplete" in prompt
    guide = json.loads(prompt.split("<SOURCE_INVENTORY_DRAFT>\n", 1)[1].split(
        "\n</SOURCE_INVENTORY_DRAFT>", 1
    )[0])
    assert guide["tests"] == original["tests"][:1]
    assert guide["attachments"] == [original["attachments"][3]]
    assert json.loads(result.calls[0].raw_response) == original
    assert plan == original


def test_guide_is_not_a_whitelist_and_a_real_test_missed_by_inventory_survives():
    facts = extraction()
    facts["requirements"].append({
        **copy.deepcopy(facts["requirements"][0]), "test": "Hardness",
        "methods": ["ISO 15184"], "conditions": ["load 750 g"],
        "criteria": ["acceptance >=F"],
        "evidence": [{"page": 1, "quote": SOURCE.splitlines()[1]}],
    })
    result = run(SequenceProvider([inventory(), facts]))
    assert [row.original_test_name for row in result.extraction.requirements] == [
        "Adhesion", "Hardness",
    ]
    assert result.extraction.requirements[1].requested_methods[0].code == "ISO 15184"


def test_oversized_guide_is_unused_instead_of_truncating_source_or_starting_more_calls():
    probe = SequenceProvider([extraction()])
    CustomerRequirementRunner(probe).run(document(), strategy="compact_document")
    request = probe.requests[0]
    limit = (
        len(request.system_prompt) + len(request.user_prompt)
        + len(json.dumps(request.json_schema, ensure_ascii=False)) + 100
    )
    plan = inventory()
    plan["tests"] *= 100
    provider = SequenceProvider([plan, extraction()])
    result = run(provider, max_input_characters=limit)
    assert result.inventory_audit.status == "unused_input_budget"
    assert len(provider.requests) == 2
    assert SOURCE in provider.requests[1].user_prompt
    assert "<SOURCE_INVENTORY_DRAFT>" not in provider.requests[1].user_prompt


def test_inventory_transport_failure_stops_before_extraction_with_complete_failure_record():
    provider = SequenceProvider([ProviderError("temporary unavailable", raw_text="failed")])
    with pytest.raises(CustomerExtractionError) as error:
        run(provider)
    assert len(provider.requests) == 1
    artifact = error.value.artifact
    assert len(artifact.calls) == 1
    assert artifact.calls[0].purpose == "inventory"
    assert artifact.calls[0].failure.raw_text == "failed"
    assert artifact.calls[0].prompt_version == INVENTORY_PROMPT_VERSION


def test_final_schema_failure_records_inventory_and_extraction_without_accepting_partial_rows():
    bad = extraction()
    bad["requirements"][0]["evidence"] = []
    provider = SequenceProvider([inventory(), bad])
    with pytest.raises(CustomerExtractionError) as error:
        run(provider)
    assert len(provider.requests) == 2
    artifact = error.value.artifact
    assert artifact.error_code == "invalid_output_schema"
    assert [call.purpose for call in artifact.calls] == ["inventory", "extraction"]
    assert json.loads(artifact.calls[0].response.raw_text) == inventory()
    assert json.loads(artifact.calls[1].response.raw_text) == bad


def test_fixed_full_source_requests_are_preflighted_before_either_call():
    provider = SequenceProvider([inventory(), extraction()])
    with pytest.raises(CustomerExtractionError, match="No model call was made"):
        run(provider, max_input_characters=1)
    assert provider.requests == []


def test_shared_exception_targets_are_not_primed_as_facts_in_the_guidance():
    plan = inventory()
    text = "Condition all specimens except cold-test samples at 31 °C."
    plan["attachments"] = [{
        "relation": "shared", "applies_to": [1],
        "evidence": [{"page": 1, "quote": text}],
    }]
    typed = CustomerTestInventory.model_validate(plan)
    guidance = inventory_guidance(typed)
    body = json.loads(guidance.split("<SOURCE_INVENTORY_DRAFT>\n")[1].split(
        "\n</SOURCE_INVENTORY_DRAFT>"
    )[0])
    assert body["attachments"][0]["applies_to"] is None
    assert body["attachments"][0]["applicability_requires_source_reading"]
    assert body["attachments"][0]["evidence"][0]["quote"] == text
    assert typed.attachments[0].applies_to == [1]


def test_one_bad_fragment_does_not_erase_independently_valid_guidance():
    plan = inventory()
    plan["tests"][0]["evidence"].append({"page": 1, "quote": "INVENTED_999_DEGREES"})
    provider = SequenceProvider([plan, extraction()])
    result = run(provider)
    assert result.inventory_audit.status == "source_verified_partial_guidance"
    assert result.inventory_audit.issues
    assert "inventory guide is incomplete" in provider.requests[1].user_prompt
    guide = provider.requests[1].user_prompt.split("<SOURCE_INVENTORY_DRAFT>")[1]
    assert SOURCE.splitlines()[0] in guide
    assert "INVENTED_999_DEGREES" not in guide
    assert json.loads(result.calls[0].raw_response) == plan


def test_dropping_an_unlocated_test_remaps_only_fully_retained_attachment_targets():
    plan = inventory()
    plan["tests"] = [
        {"test": "Unlocated work", "evidence": [{"page": 1, "quote": "UNSTATED"}]},
        plan["tests"][0],
    ]
    item = plan["attachments"][0]
    plan["attachments"] = [
        {**copy.deepcopy(item), "applies_to": [2]},
        {**copy.deepcopy(item), "applies_to": [1, 2]},
    ]
    original = CustomerTestInventory.model_validate(plan)
    result = ground_inventory(original, document())
    assert [test.test for test in result.proposal.tests] == ["Adhesion"]
    assert len(result.proposal.attachments) == 1
    assert result.proposal.attachments[0].applies_to == [1]
    assert result.omitted_test_indexes == (1,)
    assert result.omitted_attachment_indexes == (2,)
    assert result.issues
    assert original.attachments[0].applies_to == [2]
    assert original.attachments[1].applies_to == [1, 2]

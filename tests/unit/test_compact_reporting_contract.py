"""An explicit report request can be complete without inventing a pass limit."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest
from jsonschema import Draft202012Validator

from scione.prompts.customer_requirements import (
    CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION,
    load_compact_customer_requirements_system_prompt,
)
from scione.providers import StaticModelProvider
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.extraction import CustomerRequirementRunner, _structured_customer_schema
from scione.specifications.models import CompactCustomerExtraction
from scione.specifications.output_schema import constrain_compact_extraction_schema
from scione.specifications.review import assess_requirements

RAW = "Record film thickness and topcoat thickness."
SOURCE = "Film measurements use ISO 60152:2025. " + RAW


def source(text=SOURCE):
    return IngestedDocument(
        document_id="reporting_source",
        file_name="reporting.pdf",
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


def payload():
    return {
        "document": {"title": None, "identifier": None, "revision": None},
        "shared_requirements": [],
        "document_notices": [],
        "review_issues": [],
        "requirements": [
            {
                "test": "Film measurements",
                "methods": ["ISO 60152:2025"],
                "method_roles": [],
                "conditions": [],
                "criteria": [],
                "scope": [],
                "instructions": [RAW],
                "evidence": [{"page": 1, "quote": SOURCE}],
                "uncertainty": None,
                "atomic_claims": [
                    {
                        "parameter_id": identifier,
                        "kind": "reporting",
                        "stage": "general",
                        "operator": "required",
                        "values": [],
                        "unit": None,
                        "text_value": name,
                        "raw_text": RAW,
                        "source_pages": [1],
                        "source_context": None,
                        "configuration": None,
                        "sequence": None,
                    }
                    for identifier, name in [
                        ("film_thickness", "film thickness"),
                        ("topcoat_thickness", "topcoat thickness"),
                    ]
                ],
            }
        ],
    }


def test_compact_required_reporting_wire_preserves_two_properties_without_numeric_limits():
    given = payload()
    untouched = deepcopy(given)
    schema = constrain_compact_extraction_schema(
        _structured_customer_schema(CompactCustomerExtraction.model_json_schema())
    )
    assert Draft202012Validator(schema).is_valid(given)
    run = CustomerRequirementRunner(StaticModelProvider(given)).run(
        source(), strategy="compact_document"
    )
    assert given == untouched
    assert json.loads(run.raw_response) == untouched
    row = run.extraction.requirements[0]
    assert {claim.parameter_id for claim in row.atomic_claims} == {
        "film_thickness",
        "topcoat_thickness",
    }
    assert all(
        claim.operator == "required" and claim.value is None and claim.unit is None
        for claim in row.atomic_claims
    )
    assert not row.conditions and not row.acceptance_criteria
    assert assess_requirements(run, source())[row.id].state == "ready"


@pytest.mark.parametrize(
    "defect",
    [
        "omitted_property",
        "custom_property",
        "invented_unit",
        "wrong_role",
        "unlocated",
        "extra_condition",
        "shaved_joint_request",
        "negated_request",
    ],
)
def test_only_a_closed_complete_located_numeric_reporting_request_avoids_missing_fields_cue(defect):
    given = payload()
    row = given["requirements"][0]
    doc = source()
    if defect == "omitted_property":
        row["atomic_claims"] = row["atomic_claims"][:1]
    elif defect == "custom_property":
        for claim in row["atomic_claims"]:
            claim["parameter_id"] = "custom_property"
    elif defect == "invented_unit":
        for claim in row["atomic_claims"]:
            claim["unit"] = "µm"
    elif defect == "wrong_role":
        for claim in row["atomic_claims"]:
            claim["kind"] = "instruction"
    elif defect == "unlocated":
        doc = source("No explicit film measurement request is printed here.")
    elif defect == "extra_condition":
        changed = "Record film thickness after three days."
        row["atomic_claims"] = row["atomic_claims"][:1]
        row["atomic_claims"][0]["raw_text"] = changed
        row["evidence"] = [{"page": 1, "quote": changed}]
        row["instructions"] = [changed]
        doc = source(changed)
    elif defect == "shaved_joint_request":
        row["atomic_claims"] = row["atomic_claims"][:1]
        row["atomic_claims"][0]["raw_text"] = "Record film thickness"
    else:
        row["atomic_claims"] = row["atomic_claims"][:1]
        row["atomic_claims"][0]["raw_text"] = "record film thickness."
        row["evidence"] = [{"page": 1, "quote": "Do not record film thickness."}]
        row["instructions"] = ["Do not record film thickness."]
        doc = source("Do not record film thickness.")
    run = CustomerRequirementRunner(StaticModelProvider(given)).run(
        doc, strategy="compact_document"
    )
    assessment = assess_requirements(run, doc)[run.extraction.requirements[0].id]
    assert any("No conditions or acceptance criteria" in reason for reason in assessment.reasons)


def test_current_prompt_preserves_short_reporting_contract_and_archived_version():
    assert CUSTOMER_REQUIREMENTS_COMPACT_PROMPT_VERSION.endswith("v0.18")
    prompt = load_compact_customer_requirements_system_prompt()
    assert "report request with no threshold" in prompt
    assert "one atom per distinct property" in prompt
    assert "material, shape and geometry" in prompt
    assert "consistent repeats in evidence" in prompt

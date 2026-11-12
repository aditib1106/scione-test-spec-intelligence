"""A same-capability CH mode can supply scope, never a different duration."""

from copy import deepcopy
from decimal import Decimal

import pytest
from test_semantic_matching import claim, proposal, snapshot
from test_semantic_wording import compile_case

from scione.catalogs.models import TestMethod
from scione.matching.metadata import canonical_alignment_pair
from scione.matching.models import SemanticMatchingOutput
from scione.matching.service import validate_matching_output
from scione.specifications.models import CustomerRequirement, RequestedMethod, RequirementEvidence

RULE = "source_method_and_reviewed_sibling_exposure_mode"
RAW = "ISO 6270-2 (CH), 240 h at 40 °C"


def case():
    requested = claim(
        "req_claim_1", parameter_id="duration", label="Duration", value=Decimal(240),
        unit="h", stage="exposure", source_context="ISO 6270-2 (CH)",
        source_pages=[1], raw_text=RAW,
    )
    declared = claim(
        "lab_claim_1", parameter_id="duration", label="Continuous humidity exposure",
        value=None, operator="range", minimum=Decimal(0), maximum=Decimal(168),
        unit="h", stage="exposure", normalization_state="reviewed",
        raw_text="Continuous humidity exposure: 0–168 h.",
    )
    mode = claim(
        "lab_mode", parameter_id="exposure_mode", label="Humidity exposure mode", value=None,
        operator="text", text_value="CH constant-condensation exposure.", unit=None,
        stage="exposure", normalization_state="reviewed",
        raw_text="Humidity exposure mode: CH constant-condensation exposure.",
    )
    source = snapshot(requested=[requested], declared=[declared, mode])
    source.catalog.methods.append(TestMethod(
        id="iso_6270_2", identifier="ISO 6270-2", name="Humidity test",
    ))
    source.catalog.tests[0].method_ids = ["iso_6270_2"]
    source.capabilities[0].capability.method_ids = ["iso_6270_2"]
    source.requirements[0].requirement = CustomerRequirement(
        id="req_001", original_test_name="Humidity resistance",
        requested_methods=[RequestedMethod(code="ISO 6270-2")],
        evidence=[RequirementEvidence(page=1, quote=RAW)], atomic_claims=[requested],
    )
    payload = proposal()
    payload["items"][0]["claim_alignments"][0]["scope_relation"] = "covered"
    return source, payload


def test_reviewed_same_cap_mode_enables_numeric_gap_without_changing_source_or_lab_records():
    source, payload = case()
    before = source.model_dump_json()
    original_proposal = deepcopy(payload)

    run, check = compile_case(source, payload)

    assert run.output.items[0].claim_alignments[0].relation == "same_parameter"
    assert check.status == "gap"
    assert check.requirement_claim.value == 240
    assert check.declared == "0–168 h"
    assert any(RULE in issue.reason for issue in run.validation_issues)
    assert any(action.get("rule") == RULE for action in check.comparison_metadata)
    assert source.model_dump_json() == before
    assert run.input_snapshot.model_dump_json() == before
    assert payload == original_proposal
    assert run.proposal == SemanticMatchingOutput.model_validate(original_proposal)


@pytest.mark.parametrize("defect", [
    "two_modes", "mode_configuration", "other_exposure_configuration", "unreviewed_mode",
    "mode_unsupported", "different_mode", "different_source_method", "different_lab_method",
    "multiple_lab_methods", "wrong_mode_stage", "wrong_scalar_stage", "wrong_sequence",
    "source_wrong_page", "source_unowned", "extra_source_qualifier", "mode_negated",
])
def test_missing_ambiguous_or_incompatible_sibling_mode_proof_stays_unknown(defect):
    source, payload = case()
    row = source.requirements[0]
    requested = row.normalized.claims[0]
    scalar, mode = source.capabilities[0].claims
    if defect == "two_modes":
        source.capabilities[0].claims.append(mode.model_copy(update={"id": "second_mode"}))
    elif defect == "mode_configuration":
        mode.configuration = "another_setup"
    elif defect == "other_exposure_configuration":
        source.capabilities[0].claims.append(scalar.model_copy(update={
            "id": "another_scalar", "configuration": "another_setup",
        }))
    elif defect == "unreviewed_mode":
        mode.normalization_state = "parsed"
    elif defect == "mode_unsupported":
        mode.availability = "unsupported"
    elif defect == "different_mode":
        mode.text_value = "AHT"
        mode.raw_text = "Humidity exposure mode: AHT."
    elif defect == "different_source_method":
        requested.source_context = "ISO 6270-1 (CH)"
        requested.raw_text = RAW.replace("6270-2", "6270-1")
        row.requirement.evidence[0].quote = requested.raw_text
        row.requirement.requested_methods[0].code = "ISO 6270-1"
    elif defect == "different_lab_method":
        source.capabilities[0].capability.method_ids = ["iso_16925"]
    elif defect == "multiple_lab_methods":
        source.capabilities[0].capability.method_ids.append("iso_16925")
    elif defect == "wrong_mode_stage":
        mode.stage = "conditioning"
    elif defect == "wrong_scalar_stage":
        scalar.stage = "conditioning"
    elif defect == "wrong_sequence":
        mode.sequence = 2
    elif defect == "source_wrong_page":
        requested.source_pages = [2]
    elif defect == "source_unowned":
        row.requirement.evidence[0].quote = "Different source quotation."
    elif defect == "extra_source_qualifier":
        requested.source_context += "; steel only"
        requested.raw_text += "; steel only"
        row.requirement.evidence[0].quote = requested.raw_text
    else:
        mode.raw_text = "CH constant-condensation exposure is not offered."
    before = source.model_dump_json()
    proposed = SemanticMatchingOutput.model_validate(payload)
    accepted, issues = validate_matching_output(source, proposed)
    assert accepted.items[0].claim_alignments[0].relation == "unknown"
    assert not any(RULE in issue.reason for issue in issues)
    assert source.model_dump_json() == before


def test_a_plain_iso_owner_caption_never_supplies_or_invents_the_ch_setup():
    source, _ = case()
    requested = source.requirements[0].normalized.claims[0]
    requested.source_context = "ISO 6270-2"
    first, second, actions = canonical_alignment_pair(
        requested, source.capabilities[0].claims[0], source.requirements[0].requirement,
        source.capabilities[0].claims, declared_method_codes=("ISO 6270-2",),
    )
    assert second.source_context is None
    assert not any(action.get("rule") == RULE for action in actions)
    assert "CH" not in (first.source_context or "")

"""Adversarial wording proposals preserve encoded identity, scope and units."""

from copy import deepcopy

import pytest
from test_semantic_matching import claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.providers.static import StaticModelProvider


def source_pair(field, left, right, *, parameter="custom_probe", kind="condition", lab_kind=None):
    common = {"parameter_id": parameter, "kind": kind, "stage": "measurement"}
    if field == "value_equivalence":
        requested = claim(
            "req_claim_1",
            **common,
            operator="required",
            value=None,
            unit=None,
            text_value=left,
            raw_text=f"Customer explicitly requires {left}.",
        )
        declared = claim(
            "lab_claim_1",
            **{**common, "kind": lab_kind or kind},
            operator="text",
            value=None,
            unit=None,
            text_value=right,
            raw_text=f"Lab explicitly lists {right}.",
        )
    else:
        quantity = (
            {"unit": "s", "raw_text": "Execution duration 60 s"} if parameter == "duration" else {}
        )
        requested = claim("req_claim_1", **common, **quantity, source_context=left)
        declared = claim("lab_claim_1", **common, **quantity, source_context=right)
    data = proposal()
    data["items"][0]["claim_alignments"][0][field] = {
        "requested_text": left,
        "declared_text": right,
        "reason": "Author-written wording proposal used to exercise the hard guard.",
    }
    return snapshot(requested=[requested], declared=[declared]), data


def decisions(field, left, right, **options):
    source, data = source_pair(field, left, right, **options)
    unchanged = deepcopy(source.model_dump(mode="json"))
    run = SemanticMatchingRunner(StaticModelProvider(data)).run(source)
    check = compile_matching_run(run, {"req_001": "supported"})[0].checks[-1]
    assert source.model_dump(mode="json") == unchanged
    assert run.input_snapshot.model_dump(mode="json") == unchanged
    return run, check


def frozen_cases():
    return [
        (
            "embedded_case",
            "value_equivalence",
            "Probe XQ-a",
            "Probe XQ-A",
            "custom_probe",
            "unknown",
        ),
        (
            "embedded_punctuation",
            "value_equivalence",
            "Probe XQ-7",
            "Probe XQ+7",
            "custom_probe",
            "unknown",
        ),
        (
            "scope_number_roles",
            "context_equivalence",
            "2 panels per lot, 3 lots",
            "3 panels per lot, 2 lots",
            "temperature",
            "unknown",
        ),
        (
            "scope_number_multiplicity",
            "context_equivalence",
            "2 panels at 2 locations",
            "2 panels at each location",
            "temperature",
            "unknown",
        ),
        (
            "scope_unit_change",
            "context_equivalence",
            "water at 60 °C",
            "water at 60 °F",
            "duration",
            "unknown",
        ),
        (
            "material_contradiction",
            "value_equivalence",
            "steel panel",
            "painted aluminium panel",
            "specimen_material",
            "unknown",
        ),
        (
            "text_number_change",
            "value_equivalence",
            "grade 7 finish",
            "grade 9 finish",
            "custom_finish",
            "unknown",
        ),
        (
            "bilingual_embedded_code",
            "context_equivalence",
            "test water at outlet of XQ-7",
            "型号XQ-7出口试验用水",
            "temperature",
            "supported",
        ),
        (
            "curated_category_alias",
            "value_equivalence",
            "UV lamp",
            "紫外灯",
            "equipment",
            "supported",
        ),
        (
            "natural_material_alias",
            "value_equivalence",
            "低碳钢",
            "mild steel",
            "specimen_material",
            "supported",
        ),
    ]


@pytest.mark.parametrize(
    "case_id,field,left,right,parameter,expected",
    frozen_cases(),
    ids=[row[0] for row in frozen_cases()],
)
def test_wording_cannot_erase_hard_source_distinctions(
    case_id, field, left, right, parameter, expected
):
    _, check = decisions(field, left, right, parameter=parameter)
    assert check.status == expected, f"{case_id}: {check.reason}"


@pytest.mark.parametrize("field", ["value_equivalence", "context_equivalence"])
def test_fabricated_equivalence_quotes_are_rejected_without_mutating_source(field):
    source, data = source_pair(field, "Recorded left", "Recorded right")
    data["items"][0]["claim_alignments"][0][field]["requested_text"] = "Invented left"
    run = SemanticMatchingRunner(StaticModelProvider(data)).run(source)
    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert run.proposal.items[0].claim_alignments[0].relation == "same_parameter"


@pytest.mark.parametrize("field", ["value_equivalence", "context_equivalence"])
def test_missing_input_cannot_be_created_by_an_equivalence(field):
    source, data = source_pair(field, "Recorded left", "Recorded right")
    attribute = "text_value" if field == "value_equivalence" else "source_context"
    setattr(source.capabilities[0].claims[0], attribute, None)
    run = SemanticMatchingRunner(StaticModelProvider(data)).run(source)
    assert run.output.items[0].claim_alignments[0].relation == "unknown"

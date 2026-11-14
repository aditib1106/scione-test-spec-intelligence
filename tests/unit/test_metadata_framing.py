"""Closed nonbinding purpose and count-basis wording cannot hide actual scope."""

import pytest
from test_joint_scope_wording import run_case
from test_semantic_matching import claim, proposal, snapshot

from scione.specifications.models import RequirementEvidence


def reporting_source(**lab_updates):
    requested = claim(
        "req_claim_1",
        parameter_id="reporting_package",
        label="Reporting package",
        kind="reporting",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="Report panel scores, photos and the temperature-cycle record.",
        raw_text="Report panel scores, photos and the temperature-cycle record.",
        source_context=None,
        source_pages=[1],
    )
    lab = claim(
        "lab_claim_1",
        parameter_id="reporting",
        label="Reporting",
        kind="reporting",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="panel scores, photos and the temperature-cycle record",
        raw_text="Lab reporting entry",
        source_context="customer evidence package",
    ).model_copy(update=lab_updates)
    source = snapshot(requested=[requested], declared=[lab])
    source.requirements[0].requirement.evidence = [
        RequirementEvidence(page=1, quote=requested.raw_text)
    ]
    return source


def test_exact_reporting_body_does_not_need_a_matching_imperative_or_purpose_label():
    source = reporting_source()
    run, check = run_case(source, proposal())
    assert check.status == "supported"
    assert any(
        "literal_reporting_body_and_purpose" in issue.reason for issue in run.validation_issues
    )
    assert check.declared == source.capabilities[0].claims[0].display_value
    assert any(
        action.get("side") == "declaration"
        and action.get("field") == "text_value"
        and action.get("source") == source.capabilities[0].claims[0].text_value
        for action in check.comparison_metadata
    )
    assert source.capabilities[0].claims[0].source_context == "customer evidence package"


def test_whole_literal_reporting_service_can_recover_an_ai_deferral_in_selected_lab():
    source = reporting_source()
    run, check = run_case(source, proposal(declared_ids=[]))
    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert check.status == "supported"
    assert check.comparison_basis == "deterministic_literal_text"
    assert check.parameter_id == "reporting_package"


@pytest.mark.parametrize(
    "change",
    [
        {"text_value": "panel scores only"},
        {"source_context": "on aluminium panels"},
        {"source_context": "customer evidence package at 50 °C"},
        {"stage": "after_cycle"},
        {"kind": "condition"},
        {"text_value": "no photos; panel scores and temperature-cycle record"},
    ],
)
def test_a_purpose_label_cannot_supply_missing_outputs_or_erase_scientific_scope(change):
    _, check = run_case(reporting_source(**change), proposal())
    assert check.status != "supported"


def test_source_owned_per_lot_count_basis_has_the_same_denominator():
    requested = claim(
        "req_claim_1",
        parameter_id="panel_count",
        label="Panel count",
        stage="general",
        operator="=",
        value=9,
        unit="panels",
        source_context="From each lot",
        source_pages=[1],
        raw_text="From each lot prepare nine panels.",
    )
    lab = claim(
        "lab_claim_1",
        parameter_id="panel_count",
        label="Panel count",
        stage="general",
        operator="range",
        value=None,
        minimum=1,
        maximum=20,
        unit="panels",
        source_context="per lot",
    )
    source = snapshot(requested=[requested], declared=[lab])
    source.requirements[0].requirement.evidence = [
        RequirementEvidence(page=1, quote=requested.raw_text)
    ]
    _, check = run_case(source, proposal())
    assert check.status == "supported"
    lab.source_context = "per panel"
    _, check = run_case(snapshot(requested=[requested], declared=[lab]), proposal())
    assert check.status == "unknown"


def test_complete_operational_description_does_not_need_a_polite_request_prefix():
    text = "We want a fine square scoring pattern through the paint film."
    requested = claim(
        "req_claim_1",
        parameter_id="scoring_procedure",
        label="Procedure",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        source_context=None,
        text_value=text,
        raw_text=text,
        source_pages=[1],
    )
    lab = requested.model_copy(
        update={
            "id": "lab_claim_1",
            "text_value": "fine square scoring pattern through the paint film",
        }
    )
    given = snapshot(requested=[requested], declared=[lab])
    given.requirements[0].requirement.evidence = [RequirementEvidence(page=1, quote=text)]
    _, check = run_case(given, proposal())
    assert check.status == "supported"
    lab.text_value = "fine square scoring pattern through aluminium"
    _, check = run_case(snapshot(requested=[requested], declared=[lab]), proposal())
    assert check.status == "unknown"


@pytest.mark.parametrize(
    "change", [None, "different_basis", "missing_repeatability", "extra_scope"]
)
def test_repeatable_map_proof_preserves_both_repeatability_and_per_panel_basis(change):
    from scione.matching.metadata_rules import measurement_map_pair

    text = "Mark a repeatable five-position map on each panel."
    first = claim(
        "req_claim_1",
        parameter_id="measurement_locations",
        label="Locations",
        value=5,
        unit="locations",
        stage="measurement",
        raw_text=text,
        source_context="on each panel",
        source_pages=[1],
    )
    second = first.model_copy(
        update={"id": "lab_claim_1", "source_context": "repeatable map on each panel"}
    )
    source = snapshot(requested=[first], declared=[second]).requirements[0].requirement
    source.evidence = [RequirementEvidence(page=1, quote=text)]
    if change == "different_basis":
        second.source_context = "repeatable map on each lot"
    if change == "missing_repeatability":
        first.raw_text = "Mark five positions on each panel."
    if change == "extra_scope":
        second.source_context += " under nitrogen"
    a, b, actions = measurement_map_pair(first, second, source)
    assert bool(actions) == (change is None)
    if actions:
        assert a.source_context == b.source_context


def test_initial_phase_decorator_leaves_the_actual_statistic_unchanged():
    from scione.matching.metadata_rules import initial_phase_pair

    text = "The arithmetic mean must be 85-95 GU as delivered."
    first = claim(
        "req_claim_1",
        parameter_id="gloss",
        label="Gloss",
        kind="criterion",
        operator="range",
        value=None,
        minimum=85,
        maximum=95,
        unit="GU",
        stage="initial",
        raw_text=text,
        source_context="arithmetic mean; as delivered",
        source_pages=[1],
    )
    second = first.model_copy(
        update={"id": "lab_claim_1", "kind": "reporting", "source_context": "arithmetic mean"}
    )
    source = snapshot(requested=[first], declared=[second]).requirements[0].requirement
    source.evidence = [RequirementEvidence(page=1, quote=text)]
    a, b, actions = initial_phase_pair(first, second, source)
    assert actions and a.source_context == b.source_context == "arithmetic mean"
    second.source_context = "minimum"
    assert not initial_phase_pair(first, second, source)[2]
    second.source_context = "arithmetic mean"
    second.stage = "after_cycle"
    assert not initial_phase_pair(first, second, source)[2]

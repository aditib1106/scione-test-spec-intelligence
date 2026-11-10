"""Per-panel topcoat scope needs a real same-capability reporting declaration."""

from copy import deepcopy
from decimal import Decimal

import pytest
from test_semantic_matching import claim, proposal, snapshot
from test_semantic_wording import compile_case

from scione.matching.metadata import canonical_alignment_pair
from scione.specifications.models import CustomerRequirement, RequirementEvidence

PARENT = (
    "Record total dry film build and the separate topcoat thickness; "
    "the latter must be between 42 and 55 µm on every panel."
)


def topcoat_case():
    requested = claim(
        "req_claim_1",
        parameter_id="topcoat_thickness",
        label="Topcoat thickness",
        kind="criterion",
        stage="general",
        operator="range",
        value=None,
        minimum=Decimal(42),
        maximum=Decimal(55),
        unit="µm",
        raw_text="the latter must be between 42 and 55 µm on every panel",
        source_context="separate topcoat thickness; on every panel",
        source_pages=[1],
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="topcoat_thickness",
        label="Separate topcoat thickness",
        kind="reporting",
        stage="general",
        operator="range",
        value=None,
        minimum=Decimal(1),
        maximum=Decimal(150),
        unit="µm",
        raw_text="Declared separate topcoat measurement range is 1–150 µm.",
    )
    scope = claim(
        "lab_scope",
        parameter_id="result_scope",
        label="Reporting scope",
        kind="scope",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="per_panel",
        raw_text="Reporting scope: per_panel.",
    )
    source = CustomerRequirement(
        id="req_001",
        original_test_name="Separate topcoat thickness",
        atomic_claims=[requested],
        evidence=[RequirementEvidence(page=1, quote=PARENT)],
    )
    return requested, declared, scope, source


def test_parent_owned_topcoat_property_and_per_panel_scope_have_explicit_proof():
    requested, declared, scope, source = topcoat_case()
    untouched = [deepcopy(row.model_dump(mode="json")) for row in (requested, declared, scope)]
    first, second, actions = canonical_alignment_pair(
        requested, declared, source, [declared, scope]
    )
    assert first.source_context is None
    assert first.minimum == 42 and first.maximum == 55
    assert second == declared
    assert any(action.get("claim_ids") == ["lab_scope"] for action in actions)
    assert all(
        "source_topcoat_property_and_explicit_per_panel_output" == action["rule"]
        for action in actions
    )
    assert [row.model_dump(mode="json") for row in (requested, declared, scope)] == untouched


@pytest.mark.parametrize(
    "change",
    [
        "missing_scope",
        "batch_scope",
        "conflicting_scope",
        "scope_other_config",
        "scope_other_stage",
        "scope_other_sequence",
        "scope_unknown",
        "scope_unsupported",
        "scope_unresolved",
        "scope_conditional",
        "scope_negated",
        "lab_other_config",
        "lab_other_stage",
        "lab_other_sequence",
        "lab_conditional",
        "lab_other_property",
        "request_material",
        "request_quantity",
        "request_statistic",
        "unowned_quote",
        "wrong_page",
        "missing_pages",
        "missing_antecedent",
        "unrelated_antecedent",
        "not_every_panel",
        "except_clause",
        "unless_clause",
        "request_wrong_number",
    ],
)
def test_scope_proof_never_erases_missing_or_independent_restrictions(change):
    requested, declared, scope, source = topcoat_case()
    declarations = [declared, scope]
    if change == "missing_scope":
        declarations = [declared]
    elif change == "batch_scope":
        scope.text_value = "batch_mean"
    elif change == "conflicting_scope":
        declarations.append(
            scope.model_copy(update={"id": "other_scope", "text_value": "batch_mean"})
        )
    elif change == "scope_other_config":
        scope.configuration = "other_configuration"
    elif change == "scope_other_stage":
        scope.stage = "after_cycle"
    elif change == "scope_other_sequence":
        scope.sequence = 2
    elif change == "scope_unknown":
        scope.availability = "unknown"
    elif change == "scope_unsupported":
        scope.availability = "unsupported"
    elif change == "scope_unresolved":
        scope.normalization_state = "unresolved"
    elif change == "scope_conditional":
        scope.source_context = "steel only"
    elif change == "scope_negated":
        scope.raw_text = "Not per-panel reporting."
    elif change == "lab_other_config":
        declared.configuration = "other_configuration"
    elif change == "lab_other_stage":
        declared.stage = "after_cycle"
    elif change == "lab_other_sequence":
        declared.sequence = 2
    elif change == "lab_conditional":
        declared.source_context = "steel only"
    elif change == "lab_other_property":
        declared.parameter_id = "film_thickness"
    elif change == "request_material":
        requested.source_context += "; steel only"
    elif change == "request_quantity":
        requested.source_context += "; at 3 bar"
    elif change == "request_statistic":
        requested.source_context += "; panel mean"
    elif change == "unowned_quote":
        source.evidence[0].quote = "An unrelated topcoat measurement."
    elif change == "wrong_page":
        source.evidence[0].page = 2
    elif change == "missing_pages":
        requested.source_pages = []
    elif change == "missing_antecedent":
        source.evidence[0].quote = requested.raw_text
    elif change == "unrelated_antecedent":
        source.evidence[0].quote = (
            "Record separate topcoat thickness; the latter is a process record. "
            "The release requirement is another property: " + requested.raw_text
        )
    elif change == "not_every_panel":
        requested.raw_text = requested.raw_text.replace("on every panel", "not on every panel")
        source.evidence[0].quote = "Record separate topcoat thickness; " + requested.raw_text
    elif change == "except_clause":
        requested.raw_text += " except on the retained panel"
        source.evidence[0].quote = "Record separate topcoat thickness; " + requested.raw_text
    elif change == "unless_clause":
        requested.raw_text += " unless the customer approves otherwise"
        source.evidence[0].quote = "Record separate topcoat thickness; " + requested.raw_text
    elif change == "request_wrong_number":
        requested.minimum = Decimal(999)
    first, _second, actions = canonical_alignment_pair(requested, declared, source, declarations)
    assert first.source_context == requested.source_context
    assert not actions


@pytest.mark.parametrize("ceiling,expected", [(150, "supported"), (50, "gap")])
def test_topcoat_scope_proof_leaves_actual_reporting_bounds_to_code(ceiling, expected):
    requested, declared, scope, source = topcoat_case()
    declared.maximum = Decimal(ceiling)
    data = snapshot(requested=[requested], declared=[declared, scope])
    data.requirements[0].requirement = source
    untouched = deepcopy(data.model_dump(mode="json"))
    run, check = compile_case(data, proposal())
    assert check.status == expected
    assert check.requirement_claim == requested
    assert check.requirement_claim.source_context == "separate topcoat thickness; on every panel"
    assert data.model_dump(mode="json") == untouched
    assert run.input_snapshot.model_dump(mode="json") == untouched
    assert any(
        "source_topcoat_property_and_explicit_per_panel_output" in issue.reason
        for issue in run.validation_issues
    )

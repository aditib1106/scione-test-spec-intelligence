"""Saved explanation provenance is presentation-only and never guesses legacy authorship."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scione.library.comparison_explanations import present_comparison_explanations


def alignment(reason, *, relation="same_parameter", **updates):
    return SimpleNamespace(
        requirement_claim_id="claim_temperature",
        reason=reason,
        relation=relation,
        context_role=None,
        value_relation=None,
        **updates,
    )


def matching_run(proposal=None, accepted=None, issues=(), *, children=()):
    def output(link):
        return (
            SimpleNamespace(
                items=[SimpleNamespace(requirement_id="req_temperature", claim_alignments=[link])]
            )
            if link is not None
            else None
        )

    return SimpleNamespace(
        run_id="matching_saved",
        proposal=output(proposal),
        output=output(accepted),
        validation_issues=list(issues),
        child_attempts=list(children),
    )


def comparison(reason, **updates):
    return {
        "comparison_id": "immutable_saved_assessment",
        "matching_run_id": "matching_saved",
        "comparison_engine": "ai_semantic_matching",
        "input_fingerprint": "saved_input",
        "requirements": [
            {
                "requirement_id": "req_temperature",
                "comparison_engine": "ai_semantic_matching",
                "comparison": {
                    "status": "fully_matched",
                    "checks": [
                        {
                            "id": "claim_temperature",
                            "kind": "condition",
                            "parameter_id": "temperature",
                            "status": "supported",
                            "reason": reason,
                            "comparison_basis": "semantic_alignment",
                            "comparison_metadata": [],
                            **updates,
                        }
                    ],
                },
            }
        ],
    }


def first_check(result):
    return {"reason_source": result["explanation_sources"]["req_temperature"]["claim_temperature"]}


def test_numeric_verdict_reason_is_system_even_when_ai_helped_align_the_pair():
    model_reason = "The cure setting belongs to the panel-metal operating range."
    run = matching_run(alignment(model_reason), alignment(model_reason))
    source = comparison(
        "The requested setting or range is covered by this lab declaration in the same context.",
        comparison_metadata=[{"semantic_inference": True, "reason": model_reason}],
    )
    before = deepcopy(source)
    presented = present_comparison_explanations(source, run)
    assert first_check(presented)["reason_source"] == "system"
    assert source == before
    assert presented["requirements"] == before["requirements"]
    presented.pop("explanation_sources")
    assert presented == before


@pytest.mark.parametrize("relation", ["unknown", "ambiguous"])
def test_unresolved_original_model_reason_is_attributed_to_ai(relation):
    reason = "No lab declaration describes the requested wet condition."
    run = matching_run(alignment(reason, relation=relation), alignment(reason, relation=relation))
    source = comparison(reason, status="unknown", comparison_basis=None)
    assert first_check(present_comparison_explanations(source, run))["reason_source"] == "ai"


def test_validation_rejection_is_system_even_when_the_original_proposal_used_the_same_words():
    reason = "The stage, subject, sequence, or selected configuration differs."
    issue = SimpleNamespace(
        code="incompatible_claim_context",
        requirement_id="req_temperature",
        requirement_claim_id="claim_temperature",
        reason=reason,
    )
    run = matching_run(alignment(reason), alignment(reason, relation="unknown"), issues=[issue])
    source = comparison(reason, status="unknown", comparison_basis=None)
    assert first_check(present_comparison_explanations(source, run))["reason_source"] == "system"


def test_ai_incompatible_text_reason_is_not_labelled_as_a_numeric_system_check():
    reason = "The lab explicitly excludes the requested cleaning process."
    proposed = alignment(reason)
    proposed.value_relation = "incompatible"
    accepted = deepcopy(proposed)
    run = matching_run(proposed, accepted)
    source = comparison(reason, status="gap")
    assert first_check(present_comparison_explanations(source, run))["reason_source"] == "ai"


def test_source_context_reason_combines_system_introduction_and_ai_wording():
    reason = "The requested lot allocation belongs to preparation and planning."
    proposed = alignment(reason, relation="unknown")
    proposed.context_role = "sample_plan"
    run = matching_run(proposed, deepcopy(proposed))
    source = comparison(
        "Sample submission/allocation retained for scheduling and quoting; "
        "it is not a machine operating limit. " + reason,
        status="informational",
    )
    assert (
        first_check(present_comparison_explanations(source, run))["reason_source"] == "ai_assisted"
    )


def test_unknown_reason_without_matching_original_model_text_is_not_invented_as_ai():
    proposed = alignment("These parameters appear to match.")
    accepted = alignment("The proposal combines distinct configurations.", relation="unknown")
    source = comparison(accepted.reason, status="unknown", comparison_basis=None)
    assert (
        first_check(present_comparison_explanations(source, matching_run(proposed, accepted)))[
            "reason_source"
        ]
        == "unrecorded"
    )


@pytest.mark.parametrize(
    "basis",
    ["deterministic_exact_fields", "deterministic_literal_text", "source_optional_route_policy"],
)
def test_saved_deterministic_basis_identifies_a_system_reason_without_model_artifacts(basis):
    source = comparison("A stored comparison explanation.", comparison_basis=basis)
    assert first_check(present_comparison_explanations(source))["reason_source"] == "system"


def test_ambiguous_old_semantic_reason_stays_unrecorded_without_model_artifacts():
    source = comparison("The saved wording does not establish an origin.")
    assert first_check(present_comparison_explanations(source))["reason_source"] == "unrecorded"


def test_explicit_ai_metadata_can_identify_the_exact_saved_reason_without_model_artifacts():
    reason = "The specimen handling descriptions establish different services."
    source = comparison(
        reason,
        comparison_metadata=[{"semantic_inference": True, "reason": reason}],
    )
    assert first_check(present_comparison_explanations(source))["reason_source"] == "ai"


def test_row_engine_overrides_an_overall_ai_run_and_does_not_attribute_a_rule_result_to_ai():
    source = comparison("A local rule explanation.")
    source["requirements"][0]["comparison_engine"] = "deterministic_rules"
    assert first_check(present_comparison_explanations(source))["reason_source"] == "system"


def test_a_different_matching_run_cannot_supply_explanation_provenance():
    reason = "A saved model response."
    run = matching_run(alignment(reason, relation="unknown"), alignment(reason, relation="unknown"))
    run.run_id = "another_matching_run"
    source = comparison(reason, status="unknown", comparison_basis=None)
    assert (
        first_check(present_comparison_explanations(source, run))["reason_source"] == "unrecorded"
    )


def test_child_validation_history_remains_available_for_a_saved_batch_parent():
    reason = "The proposal combines declarations from different configurations."
    issue = SimpleNamespace(
        code="incompatible_claim_context",
        requirement_id="req_temperature",
        requirement_claim_id="claim_temperature",
        reason=reason,
    )
    child = matching_run(
        alignment("These are the same phase."),
        alignment(reason, relation="unknown"),
        issues=[issue],
    )
    parent = matching_run(children=[child])
    source = comparison(reason, status="unknown", comparison_basis=None)
    assert first_check(present_comparison_explanations(source, parent))["reason_source"] == "system"

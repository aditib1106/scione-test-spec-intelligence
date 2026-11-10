"""A model deferral never overrides one independently decisive registered field."""

import json
from copy import deepcopy
from pathlib import Path

from scione.evaluation.generated_matching import _policy_run
from scione.matching import compile_matching_run
from scione.matching.models import MatchingInput, SemanticMatchingOutput


def attempted():
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/generated_matching_workflow_v2.json").read_text()
    )
    snapshot = MatchingInput.model_validate(fixture["snapshot"])
    output = SemanticMatchingOutput.model_validate(fixture["expected_output"])
    for item in output.items:
        for link in item.claim_alignments:
            link.relation = "unknown"
            link.capability_claim_ids = []
            link.context_equivalence = None
            link.value_equivalence = None
    return _policy_run(snapshot, output, "all_model_fields_deferred_policy_probe")


def test_registered_exact_fields_are_decisive_while_original_ai_deferrals_remain():
    run = attempted()
    before = deepcopy(run.output.model_dump())
    results = compile_matching_run(
        run, {q.requirement.id: "supported" for q in run.input_snapshot.requirements}
    )
    checks = [c for q in results for c in q.checks if c.kind != "method"]
    assert sum(c.status == "supported" for c in checks) == 33
    assert sum(c.status == "gap" for c in checks) == 1
    assert sum(c.status == "unknown" for c in checks) == 1
    assert all(
        c.comparison_basis == "deterministic_exact_fields" for c in checks if c.status != "unknown"
    )
    assert run.output.model_dump() == before


def test_multiple_setups_or_duplicate_same_scope_fields_do_not_backfill():
    run = attempted()
    cap = run.input_snapshot.capabilities[0]
    cap.claims[0].configuration = "Setup A"
    cap.claims[1].configuration = "Setup B"
    result = compile_matching_run(
        run, {q.requirement.id: "supported" for q in run.input_snapshot.requirements}
    )[0]
    assert all(c.status == "unknown" for c in result.checks if c.kind != "method")
    run = attempted()
    cap = run.input_snapshot.capabilities[0]
    duplicate = cap.claims[0].model_copy(update={"id": "duplicate"})
    cap.claims.append(duplicate)
    result = compile_matching_run(
        run, {q.requirement.id: "supported" for q in run.input_snapshot.requirements}
    )[0]
    assert next(c for c in result.checks if c.id == "req_workflow_1_shared_0").status == "unknown"


def test_unknown_method_or_unresolved_source_never_uses_code_backfill():
    run = attempted()
    statuses = {q.requirement.id: "unknown" for q in run.input_snapshot.requirements}
    assert all(c.status == "unknown" for q in compile_matching_run(run, statuses) for c in q.checks)
    run = attempted()
    run.input_snapshot.requirements[0].normalized.claims[0].normalization_state = "unresolved"
    results = compile_matching_run(
        run, {q.requirement.id: "supported" for q in run.input_snapshot.requirements}
    )
    assert (
        next(c for c in results[0].checks if c.id == "req_workflow_1_shared_0").status == "unknown"
    )

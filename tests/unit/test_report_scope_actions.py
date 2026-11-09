"""Report-only scope history and unchanged source/pricing semantics."""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_report_matching import NOW, _inputs, _supported

from scione.reports.matching import build_report
from scione.reports.models import ReportScopeAction, ReportWorkspace


def scope_action(*, excluded=True, previous_excluded=False, **changes):
    return ReportScopeAction(
        action_id=str(uuid4()),
        requirement_id="req_001",
        excluded=excluded,
        previous_excluded=previous_excluded,
        previous_status="excluded" if previous_excluded else "in_house",
        actor="local_reviewer",
        occurred_at=NOW,
        note="User selected report scope.",
        **changes,
    )


def test_report_scope_removal_preserves_source_quantities_and_restores_underlying_quote():
    inputs = _supported(_inputs())
    before = build_report(*inputs)
    assert before.lines[0].quote is not None
    original_run = inputs[1].model_dump(mode="json")
    original_review = inputs[2].model_dump(mode="json")
    original_quantities = inputs[-1].quantities.copy()
    action = scope_action()
    inputs[-1].scope_actions.append(action)
    removed = build_report(*inputs)
    assert removed.lines[0].status == "excluded"
    assert removed.lines[0].quote is None
    assert removed.totals_by_currency == {}
    assert removed.lines[0].report_scope_exclusion == action
    assert removed.lines[0].extraction_review_state == before.lines[0].extraction_review_state
    assert removed.source_run.model_dump(mode="json") == original_run
    assert removed.source_review.model_dump(mode="json") == original_review
    assert removed.workspace.quantities == original_quantities
    assert not any("saved quantity was not priced" in warning for warning in removed.warnings)
    inputs[-1].scope_actions.append(scope_action(excluded=False, previous_excluded=True))
    restored = build_report(*inputs)
    assert restored.lines[0].status == before.lines[0].status
    assert restored.lines[0].quote == before.lines[0].quote
    assert restored.totals_by_currency == before.totals_by_currency
    assert restored.lines[0].report_scope_exclusion is None


def test_history_inconsistent_transitions_or_duplicate_ids_are_rejected():
    workspace = _inputs()[-1]
    action = scope_action()
    for history in ([action, action], [scope_action(previous_excluded=True, excluded=False)]):
        data = workspace.model_dump()
        data["scope_actions"] = history
        with pytest.raises(ValidationError):
            ReportWorkspace.model_validate(data)


def test_report_scope_action_cannot_belong_to_another_source_requirement():
    inputs = _inputs()
    action = scope_action()
    action.requirement_id = "req_from_other_extraction"
    inputs[-1].scope_actions.append(action)
    with pytest.raises(ValueError, match="extraction"):
        build_report(*inputs)


def test_restoration_recomputes_underlying_gap_instead_of_restoring_an_old_support_status():
    inputs = _inputs()
    inputs[-1].scope_actions.append(scope_action())
    removed = build_report(*inputs)
    assert removed.lines[0].status == "excluded"
    inputs[3].capabilities = []
    inputs[-1].scope_actions.append(scope_action(excluded=False, previous_excluded=True))
    restored = build_report(*inputs)
    assert restored.lines[0].status == "test_gap"
    assert restored.lines[0].quote is None

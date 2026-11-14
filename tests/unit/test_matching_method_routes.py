"""Source-authorized alternatives must not become cumulative method gaps."""

from types import SimpleNamespace

import pytest

from scione.catalogs.models import CanonicalTest, LabCapability, LabProfile, ReferenceCatalog
from scione.catalogs.models import TestMethod as Method
from scione.matching.workspace import MatchingWorkspace
from scione.reports.matching import _source_permitted_alternative_method
from scione.reports.models import MappingChoice
from scione.specifications.models import CustomerRequirement, RequestedMethod, RequirementEvidence
from scione.structured.models import ClaimCheck, StructuredComparison


def source_case(quote=None, roles=("alternative", "alternative")):
    first, second = "ASTM E1353-17", "ISO 60357:2021"
    methods = [
        Method(id="astm_impact", identifier=first, name="Synthetic impact route A", edition="17"),
        Method(id="iso_impact", identifier=second, name="Synthetic impact route B", edition="2021"),
    ]
    catalog = ReferenceCatalog(
        id="independent_routes", name="Method-choice regression", version=1,
        source_note="Synthetic identities; no method equivalence.",
        methods=methods,
        tests=[CanonicalTest(id="impact", name="Impact energy", description="Impact energy",
                             method_ids=[m.id for m in methods])],
    )
    cap = LabCapability(id="registered_a", canonical_test_id="impact", method_ids=[methods[0].id])
    row = CustomerRequirement(
        id="req_impact", original_test_name="Impact energy",
        requested_methods=[RequestedMethod(code=code, role=role)
                           for code, role in zip((first, second), roles, strict=True)],
        evidence=[RequirementEvidence(page=2, quote=quote or (
            f"For impact energy the laboratory may reference {first} or {second}. "
            "The customer does not assert that the methods are numerically equivalent."
        ))],
    )
    return row, cap, catalog


def test_located_source_permission_selects_only_the_registered_alternative():
    row, cap, catalog = source_case()
    before = row.model_dump(mode="json")
    selected = _source_permitted_alternative_method(row, cap, catalog)
    assert selected.code == "ASTM E1353-17"
    assert row.model_dump(mode="json") == before


@pytest.mark.parametrize("quote", [
    "Candidate references: ASTM E1353-17; ISO 60357:2021.",
    "The laboratory may reference ASTM E1353-17 or ISO 60357:2021 with prior approval.",
    "The laboratory may reference ASTM E1353-17 or ISO 60357:2021. Buyer approval is required.",
    "The laboratory may reference ASTM E1353-17 or ISO 60357:2020.",
])
def test_roles_alone_missing_permission_approval_and_wrong_edition_remain_unresolved(quote):
    row, cap, catalog = source_case(quote)
    assert _source_permitted_alternative_method(row, cap, catalog) is None


def test_cumulative_roles_or_explicit_human_selection_are_never_overridden():
    row, cap, catalog = source_case(roles=("exposure", "measurement"))
    assert _source_permitted_alternative_method(row, cap, catalog) is None
    row, cap, catalog = source_case()
    choice = MappingChoice(
        canonical_test_id="impact", capability_id=cap.id,
        selected_method_codes=[method.code for method in row.requested_methods],
        note="Reviewer explicitly retained both methods.",
    )
    assert _source_permitted_alternative_method(row, cap, catalog, choice) is None


def test_a_lab_service_listing_both_routes_still_needs_a_recorded_route_choice():
    row, cap, catalog = source_case()
    cap.method_ids.append("iso_impact")
    assert _source_permitted_alternative_method(row, cap, catalog) is None


@pytest.mark.parametrize("permission,expected", [(True, "supported"), (False, "unknown")])
def test_matching_gate_distinguishes_a_source_choice_from_an_unresolved_combination(
    monkeypatch, permission, expected,
):
    row, cap, catalog = source_case(None if permission else (
        "Candidate references: ASTM E1353-17; ISO 60357:2021."
    ))
    line = SimpleNamespace(
        requirement=row, mapping_choice=None,
        considered_method_codes=[m.code for m in row.requested_methods],
        status="needs_review", canonical_test_id="impact", capability=None,
        unknown_methods=[], match_basis="Exact curated name / alias",
    )
    report = SimpleNamespace(
        catalog=catalog, lines=[line],
        lab=LabProfile(id="lab", name="Independent test lab", capabilities=[cap]),
    )
    proposal = SimpleNamespace(
        requirement_id=row.id, identity_status="aligned", canonical_test_id="impact",
        capability_id=cap.id,
    )
    attempt = SimpleNamespace(output=SimpleNamespace(items=[proposal]))

    def capture_method_gate(_attempt, statuses):
        assert statuses[row.id] == expected
        return [StructuredComparison(
            requirement_id=row.id, status="needs_information",
            checks=[ClaimCheck(id="method", label="Method", kind="method",
                               requested="Original source references", status=statuses[row.id],
                               reason="Captured deterministic method gate")],
        )]

    monkeypatch.setattr("scione.matching.workspace.compile_matching_run", capture_method_gate)
    comparisons, _ = MatchingWorkspace.__new__(MatchingWorkspace)._compile(report, attempt)
    audit = comparisons[0].checks[0].comparison_metadata[0]
    assert audit["establishes_method_equivalence"] is False
    assert audit["retained_original_references"] == [m.code for m in row.requested_methods]
    if permission:
        assert audit["selected_method"] == "ASTM E1353-17"
    else:
        assert audit["rule"] == "unresolved_source_method_relationship"

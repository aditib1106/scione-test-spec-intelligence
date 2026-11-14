"""Candidate recall boundaries, source identity retention and in-memory projection."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scione.catalogs.models import (
    Alias,
    CanonicalTest,
    LabCapability,
    LabProfile,
    ReferenceCatalog,
    TestMethod,
)
from scione.matching.retrieval import (
    RETRIEVAL_POLICY_VERSION,
    RetrievalLimitError,
    evaluate_candidate_recall,
    retrieve_identity_candidates,
)
from scione.matching.service import build_matching_input, input_fingerprint
from scione.matching.workspace import MatchingWorkspace
from scione.reports.models import MappingChoice
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
)
from scione.structured.models import AtomicClaim


def catalog(size=100, methods_per_test=4):
    methods = [
        TestMethod(
            id=f"method_{test}_{index}",
            identifier=f"ISO {70000 + test * 100 + index}:2025",
            name=f"Method for distinct property {test}",
            edition="2025",
        )
        for test in range(size)
        for index in range(methods_per_test)
    ]
    return ReferenceCatalog(
        id="retrieval_test",
        name="Independent synthetic identity fixtures",
        source_note="No document outcomes or capability claims are catalog aliases.",
        version=1,
        tests=[
            CanonicalTest(
                id=f"test_{i}",
                name=f"Distinct property {i}",
                description=f"Measure synthetic property {i}",
                method_ids=[f"method_{i}_{j}" for j in range(methods_per_test)],
            )
            for i in range(size)
        ],
        methods=methods,
    )


def requirement(name="Requested unfamiliar property", methods=()):
    return CustomerRequirement(
        id="req_one",
        original_test_name=name,
        requested_methods=[RequestedMethod(code=code) for code in methods],
        evidence=[RequirementEvidence(page=1, quote=name)],
    )


def source_run(row):
    return CustomerExtractionRun(
        run_id="source_run",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        document_id="a" * 16,
        prompt_version="fixture",
        schema_version="0.1",
        provider="static",
        model="fixture",
        latency_ms=0,
        usage={},
        raw_response="isolated extraction fixture",
        extraction=CustomerExtraction(
            schema_version="0.1", document=CustomerDocumentMetadata(), requirements=[row]
        ),
    )


def lab(capabilities=()):
    return LabProfile(id="test_lab", name="Synthetic lab", capabilities=list(capabilities))


def capability(identifier="cap_target", test_id="test_73", methods=("method_73_0",)):
    return LabCapability(id=identifier, canonical_test_id=test_id, method_ids=list(methods))


def report_for(bank, row, *, capabilities=(), choice=None, candidates=()):
    return SimpleNamespace(
        catalog=bank,
        source_run=source_run(row),
        lab=lab(capabilities),
        source_facts_revision_id=None,
        source_review=SimpleNamespace(model_dump=lambda **kwargs: {"version": 0}),
        workspace=SimpleNamespace(mappings={"req_one": choice} if choice else {}),
        lines=[
            SimpleNamespace(
                requirement=row,
                canonical_test_id=None,
                candidate_test_ids=list(candidates),
                mapping_choice=choice,
            )
        ],
    )


def workspace():
    # The projection is pure: no SQL connection, service or environment is needed.
    return MatchingWorkspace.__new__(MatchingWorkspace)


def test_exact_method_candidates_are_retained_ahead_of_many_rule_distractors():
    bank = catalog()
    row = requirement(methods=["ISO 77300:2025"])
    before = row.model_dump(mode="json")
    selected = retrieve_identity_candidates(
        row, bank, preferred_test_ids=[f"test_{i}" for i in range(60)], test_limit=5
    )
    assert selected.test_ids[0] == "test_73"
    assert selected.test_ids == ("test_73",)
    assert selected.method_ids == ("method_73_0",)
    assert selected.diagnostics["omitted_ranked_candidate_count"] > 0
    assert selected.diagnostics["metrics"]["recall"] is None
    assert row.model_dump(mode="json") == before


def test_exact_method_capabilities_exclude_other_method_distractors_without_merging_setups():
    bank = catalog()
    row = requirement(methods=["ISO 77300:2025"])
    caps = [capability("exact_one"), capability("exact_two"),
            capability("other_method", methods=("method_73_1",))]
    snapshot, _ = workspace()._snapshot_and_retrieval(report_for(bank, row, capabilities=caps))
    assert {c.capability.id for c in snapshot.capabilities} == {"exact_one", "exact_two"}
    assert set(snapshot.requirements[0].capability_ids) == {"exact_one", "exact_two"}


def test_other_method_capability_stays_when_requested_method_is_not_declared():
    bank = catalog()
    row = requirement(methods=["ISO 77300:2025"])
    caps = [capability("other_method", methods=("method_73_1",))]
    snapshot, _ = workspace()._snapshot_and_retrieval(report_for(bank, row, capabilities=caps))
    assert [c.capability.id for c in snapshot.capabilities] == ["other_method"]


def test_curated_multilingual_alias_and_typo_retrieval_do_not_create_catalog_aliases():
    bank = catalog()
    target = bank.tests[73]
    target.name = "Pressure water jet adhesion"
    target.aliases = [Alias(text="高压水射流附着力", language="zh")]
    before = bank.model_dump(mode="json")
    translated = retrieve_identity_candidates(requirement("高压水射流附着力"), bank)
    misspelled = retrieve_identity_candidates(requirement("Pressure watrr jet adhesion"), bank)
    assert translated.test_ids[0] == misspelled.test_ids[0] == "test_73"
    assert bank.model_dump(mode="json") == before


def test_unknown_code_is_not_renamed_to_a_neighboring_or_equivalent_method():
    bank = catalog()
    row = requirement("⨁异星声纹", ["XYZ-UNLISTED-19"])
    selected = retrieve_identity_candidates(row, bank)
    assert selected.test_ids == selected.method_ids == ()
    assert selected.diagnostics["mode"] == "no_catalog_candidate"
    assert selected.diagnostics["method_references"][0]["state"] == "unresolved_reference"
    assert row.requested_methods[0].code == "XYZ-UNLISTED-19"


def test_source_output_parameter_can_retrieve_untranslated_name_without_inventing_alias():
    bank = catalog()
    bank.tests[73].name = "Tensile strength"
    row = requirement("引張強さ")
    row.atomic_claims = [
        AtomicClaim(
            id="claim_strength",
            parameter_id="tensile_strength",
            label="引張強さ",
            kind="criterion",
            operator=">=",
            value=12,
            unit="MPa",
            raw_text="12 MPa",
        )
    ]
    selected = retrieve_identity_candidates(row, bank)
    assert selected.test_ids == ("test_73",)
    assert selected.diagnostics["candidates"][0]["signals"] == ["source_output_parameter_candidate"]
    assert bank.tests[73].aliases == []


def test_different_editions_stay_distinct_exact_identity_candidates():
    bank = catalog(size=2, methods_per_test=1)
    bank.methods[0].identifier = "ISO 12345:2021"
    bank.methods[0].edition = "2021"
    bank.methods[1].identifier = "ISO 12345-2:2025"
    selected = retrieve_identity_candidates(requirement(methods=["ISO 12345:2021"]), bank)
    assert selected.method_ids == ("method_0_0",)
    assert selected.test_ids == ("test_0",)


def test_method_revision_review_retains_identity_without_claiming_equivalence():
    bank = catalog(size=2, methods_per_test=1)
    selected = retrieve_identity_candidates(requirement(methods=["ISO 70000:2023"]), bank)
    assert selected.test_ids == ("test_0",)
    assert selected.method_ids == ("method_0_0",)
    assert selected.diagnostics["method_references"][0]["state"] == "identity_needs_review"


def test_conflicting_exact_name_and_method_candidates_survive_soft_top_k():
    bank = catalog()
    selected = retrieve_identity_candidates(
        requirement("Distinct property 12", ["ISO 77300:2025"]),
        bank,
        preferred_test_ids=["test_0", "test_1", "test_2"],
        test_limit=2,
    )
    assert set(selected.test_ids) == {"test_12", "test_73"}


def test_human_pins_override_ranking_and_unresolved_choice_never_falls_back():
    bank = catalog()
    row = requirement("Distinct property 12", ["ISO 77300:2025"])
    pinned = retrieve_identity_candidates(row, bank, human_reviewed=True, pinned_test_id="test_4")
    unresolved = retrieve_identity_candidates(row, bank, human_reviewed=True)
    stale = retrieve_identity_candidates(row, bank, human_reviewed=True, pinned_test_id="removed")
    assert pinned.test_ids == ("test_4",)
    assert pinned.method_ids == ("method_73_0",)
    assert unresolved.test_ids == stale.test_ids == ()


def test_mandatory_exact_identity_overflow_fails_visibly_instead_of_dropping():
    bank = catalog(size=10)
    for test in bank.tests:
        test.name = "Identical disputed catalog name"
    with pytest.raises(RetrievalLimitError, match="no exact match was dropped"):
        retrieve_identity_candidates(requirement("Identical disputed catalog name"), bank)
    with pytest.raises(RetrievalLimitError, match="No source reference was silently discarded"):
        retrieve_identity_candidates(
            requirement(methods=[method.identifier for method in bank.methods]),
            bank,
            method_limit=2,
        )


def test_method_projection_retains_lab_and_source_metadata_without_whole_family():
    bank = catalog()
    row = requirement(methods=["ISO 77300:2025"])
    declared = lab([capability(methods=("method_73_1",))])
    before = bank.model_dump(mode="json")
    snapshot = build_matching_input(
        source_run(row),
        bank,
        declared,
        capability_revision="lab_v1",
        candidate_test_ids={"req_one": ["test_73"]},
        candidate_method_ids=["method_73_0"],
    )
    assert {item.id for item in snapshot.catalog.methods} == {"method_73_0", "method_73_1"}
    assert snapshot.catalog.tests[0].method_ids == ["method_73_0", "method_73_1"]
    assert snapshot.requirements[0].requirement.requested_methods[0].code == "ISO 77300:2025"
    assert bank.model_dump(mode="json") == before
    assert len(bank.methods) == 400


def test_method_projection_rejects_nonexistent_ids():
    bank = catalog()
    with pytest.raises(ValueError, match="existing catalog identities"):
        build_matching_input(
            source_run(requirement()),
            bank,
            lab(),
            capability_revision="lab_v1",
            candidate_test_ids={"req_one": []},
            candidate_method_ids=["imaginary_method"],
        )


def test_workspace_candidates_preserve_known_identity_even_when_lab_has_no_entry():
    bank = catalog()
    report = report_for(bank, requirement(methods=["ISO 77300:2025"]))
    snapshot, diagnostics = workspace()._snapshot_and_retrieval(report)
    assert snapshot.requirements[0].catalog_test_ids == ["test_73"]
    assert snapshot.requirements[0].capability_ids == []
    assert snapshot.capabilities == []
    assert diagnostics["policy_version"] == RETRIEVAL_POLICY_VERSION
    assert len(snapshot.catalog.methods) == 1


def test_workspace_human_capability_pin_does_not_expose_other_configurations():
    bank = catalog()
    chosen = capability()
    other = capability("cap_other", methods=("method_73_2",))
    choice = MappingChoice(
        canonical_test_id="test_73", capability_id="cap_target", note="Reviewed setup"
    )
    report = report_for(
        bank, requirement(methods=["ISO 77300:2025"]), capabilities=[chosen, other], choice=choice
    )
    snapshot, _ = workspace()._snapshot_and_retrieval(report)
    assert snapshot.requirements[0].catalog_test_ids == ["test_73"]
    assert snapshot.requirements[0].capability_ids == ["cap_target"]
    assert [cap.capability.id for cap in snapshot.capabilities] == ["cap_target"]
    assert "method_73_2" not in {method.id for method in snapshot.catalog.methods}
    assert len(report.lab.capabilities) == 2


def test_workspace_pin_keeps_conflicting_source_method_metadata_unassociated():
    bank = catalog()
    choice = MappingChoice(
        canonical_test_id="test_4", capability_id="cap_pinned", note="Review another identity"
    )
    pinned = capability("cap_pinned", "test_4", ("method_4_0",))
    report = report_for(
        bank, requirement(methods=["ISO 77300:2025"]), capabilities=[pinned], choice=choice
    )
    snapshot, _ = workspace()._snapshot_and_retrieval(report)
    assert snapshot.requirements[0].catalog_test_ids == ["test_4"]
    assert snapshot.catalog.tests[0].method_ids == ["method_4_0"]
    assert {method.id for method in snapshot.catalog.methods} == {"method_4_0", "method_73_0"}
    assert snapshot.requirements[0].requirement.requested_methods[0].code == "ISO 77300:2025"


def test_explicitly_unresolved_human_mapping_exposes_no_alternative_test_or_capability():
    bank = catalog()
    choice = MappingChoice(note="No known identity applies")
    report = report_for(
        bank, requirement(methods=["ISO 77300:2025"]), capabilities=[capability()], choice=choice
    )
    snapshot, diagnostics = workspace()._snapshot_and_retrieval(report)
    assert snapshot.requirements[0].catalog_test_ids == []
    assert snapshot.requirements[0].capability_ids == []
    assert snapshot.capabilities == []
    assert diagnostics["requirements"][0]["mode"] == "human_unresolved_or_stale"


def test_retrieval_policy_version_changes_cache_fingerprint(monkeypatch):
    bank = catalog()
    report = report_for(bank, requirement(methods=["ISO 77300:2025"]))
    first = workspace().snapshot(report)
    monkeypatch.setattr("scione.matching.workspace.RETRIEVAL_POLICY_VERSION", "next_policy")
    second = workspace().snapshot(report)
    assert input_fingerprint(first) != input_fingerprint(second)


def test_retrieval_metrics_require_independent_expected_ids():
    selected = retrieve_identity_candidates(requirement("Distinct property 12"), catalog())
    measured = evaluate_candidate_recall(selected, {"test_12"})
    assert measured["found"] == measured["expected"] == 1
    assert measured["recall"] == 1
    assert selected.diagnostics["metrics"]["recall"] is None

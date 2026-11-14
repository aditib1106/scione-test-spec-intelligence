"""Frozen matching-only development probes based on generated customer PDFs.

No provider/settings/client is constructed here. Source facts are manual typed
references; lab declarations are independent controlled examples. Policy probes
and model runs are distinguished, and source/candidate fingerprints are checked
before any expected outcome is used for scoring.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from scione.catalogs.models import LabCapability, LabProfile, ReferenceCatalog
from scione.evaluation.semantic_challenge import _comparison_policy, _links, _score_outcome
from scione.matching import (
    MatchingInput,
    SemanticMatchingOutput,
    SemanticMatchingRun,
    build_matching_input,
    compile_matching_run,
    input_fingerprint,
    validate_matching_output,
)
from scione.matching.retrieval import RETRIEVAL_POLICY_VERSION, retrieve_identity_candidates
from scione.providers.base import StructuredGenerationRequest
from scione.reports.matching import _method_resolution, _selected_methods
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
)
from scione.structured.comparison import compare_requirement
from scione.structured.models import NormalizedRequirement

FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests/fixtures/generated_matching_reference_v1.json"
)
FROZEN_SHA256 = "4270607aaab88f95701beb740103c5e6838abf2b6176226dda2474705a45105e"
FROZEN_CANONICAL_SHA256 = "8c964b115f9c6e8e2d02e517a68fa65b3681833288c43e16c26072f4af8f959e"
EVALUATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _canonical(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _assert_frozen(fixture: dict) -> None:
    if _canonical(fixture) != FROZEN_CANONICAL_SHA256:
        raise ValueError("Frozen generated matching labels changed; use a new fixture version.")


def load_reference(path: str | Path | None = None) -> dict:
    raw = Path(path or FIXTURE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_SHA256:
        raise ValueError("Frozen generated matching bytes changed; use a new fixture version.")
    fixture = json.loads(raw)
    _assert_frozen(fixture)
    return fixture


def build_batch(batch_id: str, fixture: dict | None = None) -> tuple[MatchingInput, dict]:
    """Build bounded inputs while keeping each scenario's lab rows isolated."""
    fixture = fixture or load_reference()
    _assert_frozen(fixture)
    batch = next((row for row in fixture["batches"] if row["id"] == batch_id), None)
    if batch is None:
        raise ValueError("Unknown generated matching batch.")
    by_id = {row["id"]: row for row in fixture["cases"]}
    cases = [by_id[identifier] for identifier in batch["case_ids"]]
    requirements = [CustomerRequirement.model_validate(case["requirement"]) for case in cases]
    normalized = [NormalizedRequirement.model_validate(case["normalized"]) for case in cases]
    catalog = ReferenceCatalog.model_validate(fixture["catalog"])
    lab = LabProfile(
        id=f"generated_matching_{batch_id}",
        name="Independent synthetic lab scenarios",
        capabilities=[
            LabCapability.model_validate(capability)
            for case in cases
            for capability in case["capabilities"]
        ],
    )
    retrieved = [retrieve_identity_candidates(requirement, catalog) for requirement in requirements]
    method_ids = {identifier for row in retrieved for identifier in row.method_ids}
    method_ids.update(identifier for cap in lab.capabilities for identifier in cap.method_ids)
    extraction = CustomerExtractionRun(
        run_id=f"generated_reference_{batch_id}",
        started_at=datetime(2026, 11, 10, tzinfo=UTC),
        document_id=FROZEN_SHA256[:16],
        prompt_version="manual_typed_physical_source_reference",
        schema_version="0.1",
        provider="manual_reference",
        model="none",
        latency_ms=0,
        usage={},
        raw_response="Manual source reference; no extraction model was called.",
        extraction=CustomerExtraction(
            schema_version="0.1", document=CustomerDocumentMetadata(), requirements=requirements
        ),
    )
    snapshot = build_matching_input(
        extraction,
        catalog,
        lab,
        capability_revision=f"author_created_fixture:{FROZEN_SHA256}",
        catalog_revision=f"frozen_catalog:{FROZEN_SHA256}",
        review_revision=f"manual_source:{FROZEN_SHA256}:{RETRIEVAL_POLICY_VERSION}",
        normalized_requirements=normalized,
        candidate_test_ids={row.requirement_id: list(row.test_ids) for row in retrieved},
        candidate_method_ids=sorted(method_ids),
    )
    local = {
        case["requirement"]["id"]: {cap["id"] for cap in case["capabilities"]} for case in cases
    }
    for row in snapshot.requirements:
        row.capability_ids = [
            identifier
            for identifier in row.capability_ids
            if identifier in local[row.requirement.id]
        ]
    snapshot = MatchingInput.model_validate(snapshot.model_dump(mode="json"))
    return snapshot, {
        "fixture_sha256": FROZEN_SHA256,
        "batch_id": batch_id,
        "cases": deepcopy(cases),
        "retrieval": [row.diagnostics for row in retrieved],
        "scope": "matching_only_with_manual_source_facts_and_independent_synthetic_lab",
    }


def method_statuses(snapshot: MatchingInput, output: SemanticMatchingOutput) -> dict[str, str]:
    """Use production source-role selection and actual catalog/lab identities."""
    proposed = {row.requirement_id: row for row in output.items}
    capabilities = {row.capability.id: row.capability for row in snapshot.capabilities}
    tests = {row.id: row for row in snapshot.catalog.tests}
    result = {}
    for row in snapshot.requirements:
        status = "unknown"
        alignment = proposed.get(row.requirement.id)
        selected, role_issues = _selected_methods(row.requirement, None)
        resolved = [_method_resolution(method, snapshot.catalog) for method in selected]
        if (
            alignment is None
            or alignment.identity_status != "aligned"
            or role_issues
            or not resolved
        ):
            result[row.requirement.id] = status
            continue
        if any(identifier is None or reason for identifier, reason in resolved):
            result[row.requirement.id] = status
            continue
        requested_ids = {identifier for identifier, _reason in resolved}
        test = tests.get(alignment.canonical_test_id)
        if test is None or not requested_ids <= set(test.method_ids):
            result[row.requirement.id] = status
            continue
        capability = capabilities.get(alignment.capability_id)
        if capability is not None and capability.id in row.capability_ids:
            if capability.canonical_test_id == test.id:
                status = "supported" if requested_ids <= set(capability.method_ids) else "gap"
        elif alignment.capability_id is None:
            compatible = [
                capabilities[identifier]
                for identifier in row.capability_ids
                if capabilities[identifier].canonical_test_id == test.id
                and requested_ids <= set(capabilities[identifier].method_ids)
            ]
            status = "unknown" if compatible else "gap"
        result[row.requirement.id] = status
    return result


def _policy_run(
    snapshot: MatchingInput, output: SemanticMatchingOutput, mode: str
) -> SemanticMatchingRun:
    accepted, issues = validate_matching_output(snapshot, output)
    now = datetime(2026, 11, 10, tzinfo=UTC)
    return SemanticMatchingRun(
        run_id=f"{mode}_{snapshot.lab_id}",
        started_at=now,
        finished_at=now,
        status="completed",
        provider=mode,
        model="none",
        prompt_version=mode,
        input_fingerprint=input_fingerprint(snapshot),
        input_snapshot=snapshot.model_copy(deep=True),
        request=StructuredGenerationRequest(
            system_prompt="Offline policy probe. No model inference was performed.",
            user_prompt=snapshot.model_dump_json(),
            schema_name="matching_policy_probe",
            json_schema=SemanticMatchingOutput.model_json_schema(),
        ),
        raw_response=output.model_dump_json(),
        parsed_response=output.model_dump(mode="json"),
        proposal=output.model_copy(deep=True),
        output=accepted,
        validation_issues=issues,
    )


def oracle_run(snapshot: MatchingInput, fixture: dict | None = None) -> SemanticMatchingRun:
    fixture = fixture or load_reference()
    _assert_frozen(fixture)
    cases = {case["requirement"]["id"]: case for case in fixture["cases"]}
    output = SemanticMatchingOutput.model_validate(
        {
            "schema_version": "0.1",
            "review_issues": [],
            "items": [
                cases[row.requirement.id]["expected_outcomes"][0]["alignment"]
                for row in snapshot.requirements
            ],
        }
    )
    return _policy_run(snapshot, output, "manual_oracle_policy_probe")


def rules_only_run(snapshot: MatchingInput, fixture: dict | None = None) -> SemanticMatchingRun:
    """Source method identity plus production typed claim comparison, no gold links."""
    fixture = fixture or load_reference()
    _assert_frozen(fixture)
    capabilities = {row.capability.id: row for row in snapshot.capabilities}
    items = []
    for row in snapshot.requirements:
        methods, role_issues = _selected_methods(row.requirement, None)
        resolved = [_method_resolution(method, snapshot.catalog) for method in methods]
        ids = {identifier for identifier, reason in resolved if identifier and reason is None}
        possible = [test for test in snapshot.catalog.tests if ids and ids <= set(test.method_ids)]
        test = possible[0] if len(possible) == 1 and not role_issues else None
        local = [
            capabilities[i]
            for i in row.capability_ids
            if test and capabilities[i].capability.canonical_test_id == test.id
        ]
        cap = local[0] if len(local) == 1 else None
        checks = {}
        if cap:
            compared = compare_requirement(row.normalized, cap.claims)
            checks = {check.id: check for check in compared.checks}
        links = []
        for claim in row.normalized.claims:
            check = checks.get(claim.id)
            selected_ids = check.capability_claim_ids if check else []
            links.append(
                {
                    "requirement_claim_id": claim.id,
                    "capability_claim_ids": selected_ids,
                    "relation": "same_parameter" if selected_ids else "unknown",
                    "reason": "Offline registered parameter/context comparison; "
                    "no semantic inference.",
                }
            )
        items.append(
            {
                "requirement_id": row.requirement.id,
                "identity_status": "aligned" if test else "unknown",
                "canonical_test_id": test.id if test else None,
                "capability_id": cap.capability.id if cap else None,
                "reason": "Offline explicit method identity lookup.",
                "claim_alignments": links,
            }
        )
    return _policy_run(
        snapshot,
        SemanticMatchingOutput.model_validate({"schema_version": "0.1", "items": items}),
        "rules_only_policy_probe",
    )


def _assert_snapshot(attempt: SemanticMatchingRun, fixture: dict) -> tuple[str, list[dict]]:
    if input_fingerprint(attempt.input_snapshot) != attempt.input_fingerprint:
        raise ValueError("Attempt fingerprint differs from its source snapshot.")
    for batch in fixture["batches"]:
        expected, bundle = build_batch(batch["id"], fixture)
        if expected.extraction_run_id == attempt.input_snapshot.extraction_run_id:
            if expected.model_dump(mode="json") != attempt.input_snapshot.model_dump(mode="json"):
                raise ValueError(
                    "Attempt source facts, lab declarations or candidate boundaries changed."
                )
            return batch["id"], bundle["cases"]
    raise ValueError("Attempt is not one complete frozen batch.")


def evaluate_matching(attempt: SemanticMatchingRun, fixture: dict | None = None) -> dict:
    fixture = fixture or load_reference()
    _assert_frozen(fixture)
    batch, cases = _assert_snapshot(attempt, fixture)
    comparisons = {}
    actual = {}
    compilation_error = None
    if attempt.status == "completed" and attempt.output is not None:
        try:
            comparisons = {
                row.requirement_id: row
                for row in compile_matching_run(
                    attempt, method_statuses(attempt.input_snapshot, attempt.output)
                )
            }
            actual = {
                row.requirement_id: row.model_dump(mode="json") for row in attempt.output.items
            }
        except (ValueError, KeyError) as exc:
            compilation_error = f"{type(exc).__name__}: {exc}"
    proposed = (
        {row.requirement_id: row.model_dump(mode="json") for row in attempt.proposal.items}
        if attempt.proposal
        else {}
    )
    results = []
    extra_supported = []
    for case in cases:
        identifier = case["requirement"]["id"]
        compared = comparisons.get(identifier)
        checks = (
            {check.id: check for check in compared.checks if check.kind != "method"}
            if compared
            else {}
        )
        outcomes = [
            _score_outcome(
                actual.get(identifier), checks, compared.status if compared else None, outcome
            )
            for outcome in case["expected_outcomes"]
        ]
        selected_index, score = max(
            enumerate(outcomes),
            key=lambda pair: (
                pair[1]["agreement_points"],
                pair[1]["correct_decisions"],
                pair[1]["exact_links"],
                -pair[0],
            ),
        )
        outcome = case["expected_outcomes"][selected_index]
        method = (
            next((check.status for check in compared.checks if check.kind == "method"), "error")
            if compared
            else "error"
        )
        raw = proposed.get(identifier, {})
        raw_identity = all(
            raw.get(key) == outcome["alignment"].get(key)
            for key in ["identity_status", "canonical_test_id", "capability_id"]
        )
        raw_links = _links(raw)
        expected_links = _links(outcome["alignment"])
        score.update(
            case_id=case["id"],
            requirement_id=identifier,
            selected_outcome_index=selected_index,
            method_expected=outcome["method_status"],
            method_predicted=method,
            method_correct=score["identity_correct"] and method == outcome["method_status"],
            raw_proposal_correct_links=sum(
                raw_identity and raw_links.get(key) == value
                for key, value in expected_links.items()
            ),
        )
        results.append(score)
        extra_supported.extend(
            {"requirement_id": identifier, "claim_id": key}
            for key, check in checks.items()
            if key not in outcome["checks"] and check.status == "supported"
        )
    claims = [claim for case in results for claim in case["claims"]]
    total = len(claims)
    false_support = sum(row["false_support"] for row in claims) + len(extra_supported)
    false_methods = sum(
        row["method_predicted"] == "supported" and not row["method_correct"] for row in results
    )
    false_items = sum(
        row["overall_predicted"] == "fully_matched" and not row["overall_correct"]
        for row in results
    )
    return {
        "dataset_id": fixture["dataset_id"],
        "batch_id": batch,
        "fixture_sha256": FROZEN_SHA256,
        "evaluator_sha256": EVALUATOR_SHA256,
        "scoring_dependency_sha256": hashlib.sha256(
            Path(_score_outcome.__code__.co_filename).read_bytes()
        ).hexdigest(),
        "comparison_policy": _comparison_policy(),
        "mode": "offline_policy_probe_NOT_model_accuracy"
        if attempt.provider.endswith("policy_probe")
        else "live_matching_only_development_probe",
        "annotation_status": fixture["annotation_status"],
        "run_id": attempt.run_id,
        "provider": attempt.provider,
        "model": attempt.model,
        "contract_failure": attempt.status != "completed" or compilation_error is not None,
        "compilation_error": compilation_error,
        "expected_cases": len(results),
        "expected_parameter_decisions": total,
        "correct_identities": sum(row["identity_correct"] for row in results),
        "correct_links": sum(row["exact_links"] for row in results),
        "raw_proposal_correct_links": sum(row["raw_proposal_correct_links"] for row in results),
        "correct_parameter_decisions": sum(row["correct_decisions"] for row in results),
        "parameter_accuracy": sum(row["correct_decisions"] for row in results) / total
        if total
        else None,
        "correct_methods": sum(row["method_correct"] for row in results),
        "correct_overall": sum(row["overall_correct"] for row in results),
        "false_support_count": false_support,
        "false_supported_method_count": false_methods,
        "false_supported_item_count": false_items,
        "zero_false_support_all_layers": not (false_support or false_methods or false_items),
        "predicted_counts": {
            status: sum(row["predicted"] == status for row in claims)
            for status in ["supported", "gap", "unknown", "error", "informational"]
        },
        "extra_supported": extra_supported,
        "cases": results,
        "limitations": fixture["limitations"],
    }

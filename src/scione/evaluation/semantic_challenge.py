"""Pure builders and strict scoring for a frozen semantic-matching challenge.

No settings, API clients, network, application stores or source extraction are
loaded. The typed reference facts deliberately isolate correspondence and
capability policy; these author-written cases are not independent domain gold.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scione.catalogs.models import LabCapability, ReferenceCatalog
from scione.matching import comparison as production_matching
from scione.matching.comparison import compile_matching_run
from scione.matching.models import (
    MATCHING_VALIDATION_VERSION,
    CapabilityCandidate,
    MatchingInput,
    RequirementCandidates,
    SemanticMatchingOutput,
    SemanticMatchingRun,
)
from scione.matching.service import input_fingerprint
from scione.providers.base import StructuredGenerationRequest
from scione.reports.matching import _method_resolution
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.models import CustomerRequirement
from scione.structured.models import NormalizedRequirement

FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests/fixtures/semantic_capability_challenge_v2.json"
)
FROZEN_SHA256 = "54a4e286922e27b13a99e37d1583e0ecc41d53c73395a96f9e5a992794b7bf19"
FROZEN_CANONICAL_SHA256 = "29c4cf9c6dd611acff5fc6b83b9b7e7f479a84953cf243ea17eacc8284b7f531"
EVALUATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
E2E_CASE_IDS = (
    "c01_bilingual_typo",
    "c02_water_fahrenheit",
    "c03_spanish_context_duration",
    "c04_novel_holder_force",
    "c11_unknown_catalog_test",
    "c17_reporting_and_material_synonym",
)


def _canonical_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _comparison_policy() -> dict:
    """Hash the actually imported compiler tree, including isolated old baselines."""
    source_root = Path(production_matching.__file__).resolve().parents[1]
    paths = []
    for directory in ("matching", "structured", "parameters"):
        paths.extend(sorted((source_root / directory).glob("*.py")))
    paths.append(Path(_method_resolution.__code__.co_filename).resolve())
    return {
        "mode": "CURRENT_POLICY_RECOMPILE",
        "recompilation": "current_imported_production_policy",
        "validation_version": MATCHING_VALIDATION_VERSION,
        "source_sha256": {
            str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
    }


def _assert_frozen(fixture: dict) -> None:
    if _canonical_hash(fixture) != FROZEN_CANONICAL_SHA256:
        raise ValueError("Frozen semantic challenge or labels changed; create a new version.")


def load_challenge(path: str | Path | None = None) -> dict:
    """Read the exact pre-response fixture and reject changed bytes or labels."""
    raw = Path(path or FIXTURE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_SHA256:
        raise ValueError("Frozen semantic challenge bytes changed; create a new version.")
    fixture = json.loads(raw)
    _assert_frozen(fixture)
    return fixture


def e2e_source_plan(fixture: dict | None = None) -> dict:
    """Derive six text pages and predeclared labels without changing the fixture.

    Neutral request IDs and explicit stage headers make request boundaries and
    typed reference stages visible in source, rather than expecting an extractor
    to know hidden fixture metadata. This does not create or evaluate a PDF/OCR.
    """
    fixture = fixture or load_challenge()
    _assert_frozen(fixture)
    cases = {case["id"]: case for case in fixture["cases"]}
    pages = []
    expectations = []
    for page, identifier in enumerate(E2E_CASE_IDS, start=1):
        case = cases[identifier]
        stages = {claim["stage"] for claim in case["normalized"]["claims"]}
        if len(stages) != 1:
            raise ValueError("The six-page source plan requires one explicit stage per page.")
        anchor = f"R{page:02d}"
        title, body = case["source_statement"].split("\n", 1)
        text = (
            f"INDEPENDENT CUSTOMER REQUEST {anchor}\n"
            f"{title}\nStage: {next(iter(stages))}.\n{body}\n"
        )
        pages.append(
            DocumentPage(
                page_number=page,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=sum(not char.isspace() for char in text),
            )
        )
        claims = deepcopy(case["normalized"]["claims"])
        for claim in claims:
            claim["source_pages"] = [page]
        expectations.append(
            {
                "case_id": identifier,
                "request_anchor": anchor,
                "physical_page": page,
                "source_title": title,
                "method_codes": [
                    method["code"] for method in case["requirement"]["requested_methods"]
                ],
                "reference_claims": claims,
                "matching_reference_outcomes": deepcopy(case["expected_outcomes"]),
            }
        )
    source = "\f".join(page.text for page in pages)
    digest = hashlib.sha256(source.encode()).hexdigest()
    document = IngestedDocument(
        document_id=f"semantic_v2_e2e_{digest[:16]}",
        file_name="semantic_capability_v2_six_requests.txt",
        media_type="text/plain",
        sha256=digest,
        page_count=len(pages),
        pages=pages,
        text_reading_order=TextReadingOrder.CONTENT_STREAM,
        extraction_status=TextExtractionStatus.READY,
        metadata={"synthetic": "true", "fixture_sha256": FROZEN_SHA256},
    )
    plan = {
        "schema_version": "1.0",
        "plan_id": "semantic_v2_e2e_six_physical_text_pages",
        "frozen_fixture_sha256": FROZEN_SHA256,
        "annotation_status": "derived_from_frozen_author_labels_before_e2e_outputs",
        "document": document.model_dump(
            mode="json",
            exclude={"total_character_count", "total_non_whitespace_character_count"},
        ),
        "expectations": expectations,
        "expected_test_requests": len(expectations),
        "expected_source_facts": sum(len(row["reference_claims"]) for row in expectations),
        "evaluation_protocol": {
            "inventory_association": (
                "Use physical source page and the neutral R01-R06 request boundary first. "
                "Each page contains one independent request; the two same-method jet requests "
                "must not borrow each other's facts or be merged for scoring. Method alone "
                "cannot uniquely identify both requests. Missing or duplicate associations "
                "are errors, not inferred successful extractions."
            ),
            "strict_fact_fields": [
                "parameter_id",
                "kind",
                "stage",
                "operator",
                "value",
                "minimum",
                "maximum",
                "tolerance",
                "values",
                "unit",
                "text_value",
                "source_context",
                "sequence",
                "configuration",
                "source_pages",
            ],
            "strict_comparison": (
                "Keep the predeclared typed source values, units, role and page. Report strict "
                "field agreement separately from raw source coverage and downstream verdicts. "
                "Known runtime alias normalization may be reported as a distinct derived score; "
                "a novel valid representation requires labelled human adjudication rather than "
                "editing these expectations after seeing a model response."
            ),
            "evidence": (
                "Require actual verbatim source evidence on the associated physical page. "
                "A found quote does not alone prove the quantity belongs to the correct role."
            ),
            "matching": (
                "Use the actual extraction and normalization, not these reference claims, as "
                "matching input. Explicitly map generated request/claim IDs to the reviewed "
                "page/request association. Keep each case's local capability candidate boundary. "
                "The unknown muon test remains unknown; never invent a catalog or lab entry."
            ),
            "error_propagation": (
                "A missing or wrong extracted fact counts as an end-to-end error even if the "
                "expected capability verdict is unknown. Do not replace machine facts with "
                "oracle facts or report manual corrections as unassisted model performance."
            ),
        },
        "limitations": [
            "Six author-designed text pages are a development probe, "
            "not independently labelled production data.",
            "This exercises text-to-structured extraction and matching, "
            "not PDF parsing, OCR, tables or scan quality.",
            "The source explicitly includes independent request boundaries and stage labels; "
            "general documents may be harder.",
        ],
    }
    plan["plan_sha256"] = _canonical_hash(plan)
    return plan


def batch_snapshot(fixture: dict, batch: str | dict) -> MatchingInput:
    """Batch independent scenarios without leaking other cases' lab candidates.

    All capabilities are carried in one compact snapshot, but each requirement's
    authorized capability IDs come only from its own scenario. A missing lab
    declaration must not be filled by another test case in the same API request.
    """
    _assert_frozen(fixture)
    batch_id = batch if isinstance(batch, str) else batch["id"]
    found = next((row for row in fixture["batches"] if row["id"] == batch_id), None)
    if found is None:
        raise ValueError("Unknown frozen semantic batch.")
    by_id = {case["id"]: case for case in fixture["cases"]}
    rows = [by_id[identifier] for identifier in found["case_ids"]]
    capabilities = []
    requirements = []
    for case in rows:
        local = [LabCapability.model_validate(row) for row in case["capabilities"]]
        capabilities.extend(
            CapabilityCandidate(capability=capability, claims=deepcopy(capability.atomic_claims))
            for capability in local
        )
        requirements.append(
            RequirementCandidates(
                requirement=CustomerRequirement.model_validate(case["requirement"]),
                normalized=NormalizedRequirement.model_validate(case["normalized"]),
                catalog_test_ids=list(case["candidate_test_ids"]),
                capability_ids=[capability.id for capability in local],
            )
        )
    return MatchingInput(
        lab_id=f"semantic_challenge_v2_lab_{batch_id}",
        extraction_run_id=f"manual_semantic_challenge_v2_{batch_id}",
        catalog_revision=f"semantic_challenge_v2_catalog:1:{FROZEN_SHA256[:12]}",
        capability_revision=f"fixture:{FROZEN_SHA256}",
        review_revision=f"manual_typed_reference:{FROZEN_SHA256}",
        catalog=ReferenceCatalog.model_validate(fixture["catalog"]),
        capabilities=capabilities,
        requirements=requirements,
    )


def oracle_run(snapshot: MatchingInput, fixture: dict | None = None) -> SemanticMatchingRun:
    """Build the first frozen alignment outcome, never an AI-generated result.

    Compilation still runs production guards. Desired context/category positives
    may therefore remain unknown in a limited implementation; do not rewrite the
    author label to match that implementation.
    """
    fixture = fixture or load_challenge()
    _assert_frozen(fixture)
    cases = {case["requirement"]["id"]: case for case in fixture["cases"]}
    output = SemanticMatchingOutput.model_validate(
        {
            "schema_version": "0.1",
            "items": [
                cases[row.requirement.id]["expected_outcomes"][0]["alignment"]
                for row in snapshot.requirements
            ],
            "review_issues": [],
        }
    )
    now = datetime(2026, 11, 9, tzinfo=UTC)
    return SemanticMatchingRun(
        run_id=f"manual_oracle_{snapshot.lab_id}",
        started_at=now,
        finished_at=now,
        status="completed",
        input_fingerprint=input_fingerprint(snapshot),
        prompt_version="manual_frozen_semantic_challenge_v2",
        provider="manual_oracle",
        model="none",
        requested_provider="manual_oracle",
        requested_model="none",
        input_snapshot=snapshot.model_copy(deep=True),
        request=StructuredGenerationRequest(
            system_prompt="Manual frozen reference alignment; no inference was performed.",
            user_prompt=json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False),
            schema_name="semantic_matching_reference_v2",
            json_schema=SemanticMatchingOutput.model_json_schema(),
        ),
        raw_response=output.model_dump_json(),
        parsed_response=output.model_dump(mode="json"),
        proposal=output.model_copy(deep=True),
        output=output,
    )


def method_statuses(snapshot: MatchingInput, output: SemanticMatchingOutput) -> dict[str, str]:
    """Use production method identity and declared methods, never the gold label.

    A known catalog identity with no candidate supporting its requested methods
    is a gap. Declining an available compatible candidate is unresolved, not proof
    of a gap. Unknown methods/editions or unresolved test identity stay unknown.
    """
    alignments = {item.requirement_id: item for item in output.items}
    capabilities = {row.capability.id: row.capability for row in snapshot.capabilities}
    tests = {test.id: test for test in snapshot.catalog.tests}
    statuses = {}
    for row in snapshot.requirements:
        alignment = alignments.get(row.requirement.id)
        status = "unknown"
        if alignment is None or alignment.identity_status != "aligned":
            statuses[row.requirement.id] = status
            continue
        test = tests.get(alignment.canonical_test_id)
        resolved = [
            _method_resolution(method, snapshot.catalog)
            for method in row.requirement.requested_methods
        ]
        if not resolved or any(reason or identifier is None for identifier, reason in resolved):
            statuses[row.requirement.id] = status
            continue
        requested = {identifier for identifier, _reason in resolved}
        if test is None or not requested <= set(test.method_ids):
            statuses[row.requirement.id] = status
            continue
        capability = capabilities.get(alignment.capability_id)
        if capability is not None:
            if capability.canonical_test_id == test.id:
                status = "supported" if requested <= set(capability.method_ids) else "gap"
        else:
            compatible = [
                capabilities[identifier]
                for identifier in row.capability_ids
                if capabilities[identifier].canonical_test_id == test.id
                and requested <= set(capabilities[identifier].method_ids)
            ]
            status = "unknown" if compatible else "gap"
        statuses[row.requirement.id] = status
    return statuses


def _identity_signature(alignment: dict) -> tuple:
    return tuple(
        alignment.get(field) for field in ("identity_status", "canonical_test_id", "capability_id")
    )


def _links(alignment: dict) -> dict[str, tuple]:
    return {
        row["requirement_claim_id"]: (row["relation"], tuple(sorted(row["capability_claim_ids"])))
        for row in alignment.get("claim_alignments", [])
    }


def _assert_snapshot_sources(snapshot: MatchingInput, cases: dict, fixture: dict) -> None:
    """Do not score changed source/lab facts using labels for another scenario."""
    if snapshot.catalog.model_dump(mode="json") != ReferenceCatalog.model_validate(
        fixture["catalog"]
    ).model_dump(mode="json"):
        raise ValueError("Attempt catalog differs from the frozen challenge.")
    capabilities = {row.capability.id: row for row in snapshot.capabilities}
    for row in snapshot.requirements:
        case = cases[row.requirement.id]
        requirement = CustomerRequirement.model_validate(case["requirement"])
        normalized = NormalizedRequirement.model_validate(case["normalized"])
        local = {item["id"]: LabCapability.model_validate(item) for item in case["capabilities"]}
        if (
            row.requirement.model_dump(mode="json") != requirement.model_dump(mode="json")
            or row.normalized.model_dump(mode="json") != normalized.model_dump(mode="json")
            or set(row.catalog_test_ids) != set(case["candidate_test_ids"])
            or set(row.capability_ids) != local.keys()
        ):
            raise ValueError(
                "Attempt source facts or candidate boundaries differ from the fixture."
            )
        for identifier, capability in local.items():
            actual = capabilities.get(identifier)
            expected = CapabilityCandidate(capability=capability, claims=capability.atomic_claims)
            if actual is None or actual.model_dump(mode="json") != expected.model_dump(mode="json"):
                raise ValueError("Attempt lab facts differ from the frozen challenge.")


def _score_outcome(actual: dict | None, checks: dict, overall: str | None, outcome: dict) -> dict:
    expected = outcome["alignment"]
    identity_correct = actual is not None and _identity_signature(actual) == _identity_signature(
        expected
    )
    links = _links(actual or {})
    expected_links = _links(expected)
    claim_rows = []
    for identifier, expected_status in outcome["checks"].items():
        check = checks.get(identifier)
        predicted = check.status if check is not None else "error"
        correct = bool(identity_correct and predicted == expected_status)
        claim_rows.append(
            {
                "claim_id": identifier,
                "expected": expected_status,
                "predicted": predicted,
                "correct": correct,
                "raw_status_agreement": predicted == expected_status,
                "link_correct": bool(
                    identity_correct and links.get(identifier) == expected_links[identifier]
                ),
                "expected_link": {
                    "relation": expected_links[identifier][0],
                    "capability_claim_ids": list(expected_links[identifier][1]),
                },
                "predicted_link": {
                    "relation": links[identifier][0],
                    "capability_claim_ids": list(links[identifier][1]),
                }
                if identifier in links
                else None,
                "reason": check.reason if check is not None else "No compiled parameter decision.",
                "false_support": predicted == "supported" and not correct,
                "false_rejection": predicted == "gap" and expected_status == "supported",
                "false_gap": predicted == "gap" and expected_status != "gap",
            }
        )
    correct_decisions = sum(row["correct"] for row in claim_rows)
    exact_links = sum(row["link_correct"] for row in claim_rows)
    overall_correct = bool(identity_correct and overall == outcome["overall"])
    return {
        "identity_correct": bool(identity_correct),
        "correct_decisions": correct_decisions,
        "exact_links": exact_links,
        "overall_expected": outcome["overall"],
        "overall_predicted": overall or "error",
        "overall_correct": overall_correct,
        "agreement_points": int(identity_correct)
        + correct_decisions
        + exact_links
        + int(overall_correct),
        "rationale": outcome["rationale"],
        "claims": claim_rows,
    }


def evaluate_matching(attempt: SemanticMatchingRun, fixture: dict | None = None) -> dict:
    """Score one immutable attempt against complete, predeclared allowed outcomes.

    The chosen outcome maximizes total agreement points; ties prefer parameter
    decisions, then exact links, then the earliest author outcome. Every metric
    for a case uses that one outcome. Missing/failed output stays error rather
    than being credited as a legitimate unknown. Identity correctness gates
    successful parameter decisions to prevent accidental right labels on a
    wrong test/capability.
    """
    fixture = fixture or load_challenge()
    _assert_frozen(fixture)
    by_requirement = {case["requirement"]["id"]: case for case in fixture["cases"]}
    requested = [row.requirement.id for row in attempt.input_snapshot.requirements]
    if set(requested) - by_requirement.keys():
        raise ValueError("Attempt includes requirements outside the frozen challenge.")
    if input_fingerprint(attempt.input_snapshot) != attempt.input_fingerprint:
        raise ValueError("Attempt fingerprint does not match its input snapshot.")
    _assert_snapshot_sources(attempt.input_snapshot, by_requirement, fixture)
    comparisons = {}
    actual = {}
    compilation_error = None
    if attempt.status == "completed" and attempt.output is not None:
        try:
            statuses = method_statuses(attempt.input_snapshot, attempt.output)
            comparisons = {
                row.requirement_id: row for row in compile_matching_run(attempt, statuses)
            }
            actual = {
                row.requirement_id: row.model_dump(mode="json") for row in attempt.output.items
            }
        except (ValueError, KeyError) as exc:
            # Domain error text contains no network object or credential metadata.
            compilation_error = f"{type(exc).__name__}: {exc}"
    case_rows = []
    extra_supported = []
    for identifier in requested:
        case = by_requirement[identifier]
        comparison = comparisons.get(identifier)
        checks = (
            {check.id: check for check in comparison.checks if check.kind != "method"}
            if comparison
            else {}
        )
        outcomes = [
            _score_outcome(
                actual.get(identifier), checks, comparison.status if comparison else None, expected
            )
            for expected in case["expected_outcomes"]
        ]
        outcome_index, selected = max(
            enumerate(outcomes),
            key=lambda row: (
                row[1]["agreement_points"],
                row[1]["correct_decisions"],
                row[1]["exact_links"],
                -row[0],
            ),
        )
        selected.update(
            case_id=case["id"],
            requirement_id=identifier,
            family=case["family"],
            selected_outcome_index=outcome_index,
        )
        case_rows.append(selected)
        labelled = set(case["expected_outcomes"][outcome_index]["checks"])
        extra_supported.extend(
            {"requirement_id": identifier, "claim_id": check.id}
            for check in checks.values()
            if check.id not in labelled and check.status == "supported"
        )
    claim_rows = [row for case in case_rows for row in case["claims"]]
    expected_count = len(claim_rows)
    correct_count = sum(row["correct"] for row in claim_rows)
    supported_count = sum(row["predicted"] == "supported" for row in claim_rows) + len(
        extra_supported
    )
    false_support = sum(row["false_support"] for row in claim_rows) + len(extra_supported)
    counts = {
        status: sum(row["predicted"] == status for row in claim_rows)
        for status in ("supported", "gap", "unknown", "error", "informational")
    }
    return {
        "dataset_id": fixture["dataset_id"],
        "fixture_sha256": FROZEN_SHA256,
        "evaluator_sha256": EVALUATOR_SHA256,
        "comparison_policy": _comparison_policy(),
        "annotation_status": fixture["annotation_status"],
        "run_id": attempt.run_id,
        "provider": attempt.provider,
        "model": attempt.model,
        "status": attempt.status,
        "error_code": attempt.error_code,
        "compilation_error": compilation_error,
        "contract_failure": attempt.status != "completed" or compilation_error is not None,
        "input_sha256": input_fingerprint(attempt.input_snapshot),
        "expected_cases": len(case_rows),
        "expected_parameter_decisions": expected_count,
        "correct_parameter_decisions": correct_count,
        "parameter_accuracy": correct_count / expected_count if expected_count else None,
        "correct_identities": sum(row["identity_correct"] for row in case_rows),
        "correct_links": sum(row["link_correct"] for row in claim_rows),
        "correct_overall": sum(row["overall_correct"] for row in case_rows),
        "predicted_counts": counts,
        "automation_coverage": (counts["supported"] + counts["gap"]) / expected_count
        if expected_count
        else None,
        "unknown_rate": counts["unknown"] / expected_count if expected_count else None,
        "error_rate": counts["error"] / expected_count if expected_count else None,
        "false_support_count": false_support,
        "predicted_supported_count": supported_count,
        "false_support_rate": false_support / supported_count if supported_count else None,
        "false_rejection_count": sum(row["false_rejection"] for row in claim_rows),
        "false_gap_count": sum(row["false_gap"] for row in claim_rows),
        "extra_supported": extra_supported,
        "cases": case_rows,
        "limitations": fixture["limitations"],
        "metric_note": (
            "One complete predeclared outcome is selected per case. Strict identity/ID links "
            "and final parameter statuses are separate; manual typed inputs do not measure "
            "extraction or independent production accuracy."
        ),
    }

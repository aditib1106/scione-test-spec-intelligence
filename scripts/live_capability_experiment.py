#!/usr/bin/env python3
"""Prepare/replay one frozen synthetic challenge; this script makes no API calls.

The root experiment driver owns provider construction, the global call budget,
timeouts and persisted artifacts. This module neither loads settings nor retries
or calls a provider. CLI replay accepts already saved extraction/matching runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scione.catalogs.models import LabCapability, LabProfile
from scione.catalogs.seed import load_reference_catalog
from scione.matching.comparison import compile_matching_run
from scione.matching.models import SemanticMatchingRun
from scione.parameters import contexts_match, normalize_claim
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.models import (
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
)
from scione.structured import compare_requirement, normalize_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/live_capability_challenge_v1.json"
FROZEN_SHA256 = "cc70563c579364d3a9ee18987a9272f52d7aaf895446287006e8cf2151d9152d"
EVALUATOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

# Manually specified correspondence labels, frozen before challenge responses.
# A threshold-reporting link does not predict that a specimen will pass.
EXPECTED_LINKS = {
    "gold_gloss_temp": ["lab_gloss_temp"],
    "gold_gloss_time": ["lab_gloss_time"],
    "gold_gloss_angle": ["lab_gloss_angle"],
    "gold_gloss_result": ["lab_gloss_report"],
    "gold_adh_temp": ["lab_adh_temp"],
    "gold_adh_time": ["lab_adh_time"],
    "gold_adh_low_temp": ["lab_adh_low_temp"],
    "gold_adh_low_time": ["lab_adh_low_time"],
    "gold_adh_high_temp": ["lab_adh_high_temp"],
    "gold_adh_high_time": ["lab_adh_high_time"],
    "gold_adh_class": ["lab_adh_report"],
    "gold_thickness_force": ["lab_thickness_force"],
    "gold_thickness_material": [],
    "gold_thickness_insert": [],
    "gold_thickness_result": ["lab_thickness_report"],
    "gold_colour_temp": ["lab_colour_temp"],
    "gold_colour_time": ["lab_colour_time"],
    "gold_colour_light": ["lab_colour_light"],
    "gold_colour_panel": ["lab_colour_panel"],
    "gold_colour_result": ["lab_colour_report"],
}


def _fixture():
    raw = FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_SHA256:
        raise ValueError("Frozen challenge changed. Do not revise labels after viewing responses.")
    return json.loads(raw)


def build_challenge() -> dict:
    """Return typed source/catalog/lab/oracle objects without inference or I/O services."""
    data = _fixture()
    source = "\f".join(page["text"] for page in data["pages"])
    document = IngestedDocument(
        document_id=data["challenge_id"],
        file_name="synthetic_live_challenge_v1.txt",
        media_type="text/plain",
        sha256=hashlib.sha256(source.encode()).hexdigest(),
        page_count=len(data["pages"]),
        pages=[
            DocumentPage(
                **page,
                character_count=len(page["text"]),
                non_whitespace_character_count=sum(not c.isspace() for c in page["text"]),
            )
            for page in data["pages"]
        ],
        text_reading_order=TextReadingOrder.CONTENT_STREAM,
        extraction_status=TextExtractionStatus.READY,
        metadata={"synthetic": "true", "fixture_sha256": FROZEN_SHA256},
    )
    catalog = load_reference_catalog()
    test_methods = {row["canonical_test_id"]: row["method_id"] for row in data["tests"]}
    catalog = catalog.model_copy(
        update={
            "tests": [
                test.model_copy(update={"method_ids": [test_methods[test.id]]})
                for test in catalog.tests
                if test.id in test_methods
            ],
            "methods": [method for method in catalog.methods if method.id in test_methods.values()],
        },
        deep=True,
    )
    requirements = []
    normalized = []
    capabilities = []
    for row in data["tests"]:
        claims = [AtomicClaim.model_validate(claim) for claim in row["claims"]]
        requirements.append(
            CustomerRequirement(
                id=row["id"],
                original_test_name=row["name"],
                requested_methods=[{"code": row["method"]}],
                conditions=[
                    {"name": claim.label, "raw_value": claim.raw_text}
                    for claim in claims
                    if claim.kind == "condition"
                ],
                acceptance_criteria=[
                    {"name": claim.label, "raw_value": claim.raw_text}
                    for claim in claims
                    if claim.kind == "criterion"
                ],
                applicability=[claim.raw_text for claim in claims if claim.kind == "scope"],
                evidence=[{"page": row["page"], "quote": row["source_block"]}],
                atomic_claims=claims,
            )
        )
        normalized.append(
            NormalizedRequirement(
                requirement_id=row["id"],
                test_name=row["name"],
                normalizer_version="manual_frozen_oracle_v1",
                claims=claims,
            )
        )
        capabilities.append(
            LabCapability(
                id="cap_live_" + row["canonical_test_id"],
                canonical_test_id=row["canonical_test_id"],
                method_ids=[row["method_id"]],
                declared_constraints=" ".join(claim["raw_text"] for claim in row["lab_claims"]),
                atomic_claims=row["lab_claims"],
            )
        )
    oracle = CustomerExtractionRun(
        run_id="manual_oracle_" + data["challenge_id"],
        started_at=datetime(2026, 11, 9, tzinfo=UTC),
        document_id=document.document_id,
        strategy="compact_document",
        prompt_version="manual_frozen_oracle_v1",
        schema_version="0.1",
        provider="manual_oracle",
        model="none",
        latency_ms=0,
        usage={},
        raw_response=json.dumps(data, ensure_ascii=False),
        raw_response_kind="manual_reference",
        calls=[],
        extraction=CustomerExtraction(
            schema_version="0.1",
            document={
                "title": "Synthetic capability challenge",
                "identifier": "LIVE-SYN-01",
                "revision": "A",
            },
            requirements=requirements,
        ),
    )
    return {
        "document": document,
        "catalog": catalog,
        "lab": LabProfile(
            id="lab_live_challenge", name="Synthetic challenge lab", capabilities=capabilities
        ),
        "oracle_run": oracle,
        "oracle_normalized": normalized,
        "fixture_sha256": FROZEN_SHA256,
        "evaluator_sha256": EVALUATOR_SHA256,
        "data": data,
    }


def _fold(value):
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", value or "").casefold()).strip()


def _method(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).upper())


def _number(value):
    return str(Decimal(value).normalize()) if value is not None else None


def _signature(claim: AtomicClaim, row: dict, data: dict):
    claim = normalize_claim(claim)
    aliases = data["parameter_aliases"] | row["parameter_aliases"]
    parameter = claim.parameter_id
    for canonical, accepted in aliases.items():
        if _fold(parameter) in {_fold(value) for value in accepted}:
            parameter = canonical
            break
    return (
        parameter,
        claim.kind,
        claim.stage,
        claim.sequence,
        claim.operator,
        _number(claim.value),
        _number(claim.minimum),
        _number(claim.maximum),
        _number(claim.tolerance),
        tuple(_number(value) for value in claim.values),
        claim.unit,
        _fold(claim.text_value),
        tuple(sorted(set(claim.source_pages))),
        claim.normalization_state != "unresolved",
    )


def _requirements_by_gold(requirements, data):
    by_gold = {row["id"]: [] for row in data["tests"]}
    extras = []
    for requirement in requirements:
        matches = [
            row
            for row in data["tests"]
            if any(
                _method(method.code) == _method(row["method"])
                for method in requirement.requested_methods
            )
        ]
        if len(matches) == 1:
            by_gold[matches[0]["id"]].append(requirement)
        else:
            extras.append(requirement.id)
    return by_gold, extras


def _claim_matches(gold, predicted, row, data):
    return _signature(gold, row, data) == _signature(predicted, row, data) and contexts_match(
        gold, predicted
    )


def _ratio(n, d):
    return n / d if d else None


def _extraction_claim_metrics(run, *, effective=False):
    data = _fixture()
    grouped, extras = _requirements_by_gold(run.extraction.requirements, data)
    true_positive = 0
    expected_count = sum(len(row["claims"]) for row in data["tests"])
    predicted_count = 0
    unresolved_count = 0
    diagnostics = []
    for row in data["tests"]:
        requirements = grouped[row["id"]]
        predicted = [
            claim
            for requirement in requirements
            for claim in (
                normalize_requirement(requirement).claims
                if effective
                else requirement.atomic_claims
            )
        ]
        unresolved_count += sum(claim.normalization_state == "unresolved" for claim in predicted)
        predicted = [claim for claim in predicted if claim.normalization_state != "unresolved"]
        predicted_count += len(predicted)
        used = set()
        for raw_gold in row["claims"]:
            gold = AtomicClaim.model_validate(raw_gold)
            matches = [
                index
                for index, claim in enumerate(predicted)
                if index not in used and _claim_matches(gold, claim, row, data)
            ]
            if matches:
                used.add(matches[0])
                true_positive += 1
            else:
                diagnostics.append(
                    {
                        "test_id": row["id"],
                        "gold_claim_id": gold.id,
                        "error": "No exact typed fact with correct context/page.",
                    }
                )
    for requirement in run.extraction.requirements:
        if requirement.id in extras:
            extra_claims = (
                normalize_requirement(requirement).claims
                if effective
                else requirement.atomic_claims
            )
            unresolved_count += sum(
                claim.normalization_state == "unresolved" for claim in extra_claims
            )
            predicted_count += sum(
                claim.normalization_state != "unresolved" for claim in extra_claims
            )
    return {
        "expected_claims": expected_count,
        "predicted_claims": predicted_count,
        "exact_claim_true_positives": true_positive,
        "false_negative_claims": expected_count - true_positive,
        "false_positive_claims": predicted_count - true_positive,
        "retained_unresolved_source_records": unresolved_count,
        "precision": _ratio(true_positive, predicted_count),
        "recall": _ratio(true_positive, expected_count),
        "missing_facts": diagnostics,
    }


def evaluate_extraction(run: CustomerExtractionRun) -> dict:
    """Strict frozen-field score; novel valid phrasing still needs human adjudication."""
    data = _fixture()
    if run.document_id != data["challenge_id"]:
        raise ValueError("This extraction is not from the frozen challenge document.")
    grouped, extras = _requirements_by_gold(run.extraction.requirements, data)
    test_tp = sum(bool(requirements) for requirements in grouped.values())
    predicted = len(run.extraction.requirements)
    bundle = build_challenge()
    pages = {page.page_number: " ".join(page.text.split()) for page in bundle["document"].pages}
    evidence = [item for row in run.extraction.requirements for item in row.evidence]
    grounded = sum(" ".join(item.quote.split()) in pages.get(item.page, "") for item in evidence)
    return {
        "fixture_sha256": FROZEN_SHA256,
        "evaluator_sha256": EVALUATOR_SHA256,
        "run_id": run.run_id,
        "provider": run.provider,
        "model": run.model,
        "annotation_status": data["annotation_status"],
        "limitations": data["limitations"],
        "test_inventory": {
            "expected": 4,
            "predicted": predicted,
            "true_positives": test_tp,
            "precision": _ratio(test_tp, predicted),
            "recall": test_tp / 4,
            "unmatched_test_ids": extras,
            "duplicate_method_anchors": [key for key, values in grouped.items() if len(values) > 1],
        },
        "raw_model_atoms": _extraction_claim_metrics(run),
        "effective_normalized_facts": _extraction_claim_metrics(run, effective=True),
        "literal_evidence": {
            "grounded": grounded,
            "total": len(evidence),
            "rate": _ratio(grounded, len(evidence)),
        },
        "metric_note": "Strict typed-claim exact match, not semantic equivalence certification. "
        "Role, stage, sequence, source number/unit, operator, context and page must agree. "
        "Approved aliases were fixed before responses; inspect novel phrasing manually.",
    }


def _method_capability(requirement, catalog, lab):
    requested = {_method(item.code) for item in requirement.requested_methods}
    methods = {method.id: _method(method.identifier) for method in catalog.methods}
    candidates = [
        cap
        for cap in lab.capabilities
        if requested and requested.issubset({methods[key] for key in cap.method_ids})
    ]
    return candidates[0] if len(candidates) == 1 else None


def _score_comparisons(
    requirements, normalized, comparisons, *, alignments=None, selected_capabilities=None
):
    data = _fixture()
    grouped, extras = _requirements_by_gold(requirements, data)
    facts = {row.requirement_id: row for row in normalized}
    comparison_map = {row.requirement_id: row for row in comparisons}
    rows = []
    overall_rows = []
    for row in data["tests"]:
        found = grouped[row["id"]]
        source = found[0] if len(found) == 1 else None
        normalized_row = facts.get(source.id) if source else None
        comparison = comparison_map.get(source.id) if source else None
        alignment = alignments.get(source.id) if source and alignments is not None else None
        identity_ok = bool(source and comparison)
        if comparison is not None:
            method_checks = [check for check in comparison.checks if check.kind == "method"]
            identity_ok = (
                identity_ok
                and bool(method_checks)
                and all(check.status == "supported" for check in method_checks)
            )
        if selected_capabilities is not None:
            identity_ok = bool(
                identity_ok
                and selected_capabilities.get(source.id) == "cap_live_" + row["canonical_test_id"]
            )
        if alignments is not None:
            identity_ok = bool(
                identity_ok
                and alignment
                and alignment.identity_status == "aligned"
                and alignment.canonical_test_id == row["canonical_test_id"]
                and alignment.capability_id == "cap_live_" + row["canonical_test_id"]
            )
        checks = {check.id: check for check in comparison.checks} if comparison else {}
        gold_statuses = set(row["expected_checks"].values())
        expected_overall = (
            "partially_matched"
            if "gap" in gold_statuses
            else "needs_information"
            if "unknown" in gold_statuses
            else "fully_matched"
        )
        overall_rows.append(
            {
                "test_id": row["id"],
                "expected": expected_overall,
                "predicted": comparison.status if comparison else "error",
                "correct": bool(identity_ok and comparison.status == expected_overall),
            }
        )
        for raw_gold in row["claims"]:
            gold = AtomicClaim.model_validate(raw_gold)
            matching_facts = [
                claim
                for claim in (normalized_row.claims if normalized_row else [])
                if _claim_matches(gold, claim, row, data)
            ]
            claim = matching_facts[0] if len(matching_facts) == 1 else None
            check = checks.get(claim.id) if claim else None
            predicted = check.status if check and identity_ok else "error"
            link_correct = None
            if alignment is not None and claim is not None:
                links = [
                    link
                    for link in alignment.claim_alignments
                    if link.requirement_claim_id == claim.id
                ]
                expected_ids = EXPECTED_LINKS[gold.id]
                link_correct = bool(
                    len(links) == 1
                    and set(links[0].capability_claim_ids) == set(expected_ids)
                    and links[0].relation == ("same_parameter" if expected_ids else "unknown")
                )
            rows.append(
                {
                    "test_id": row["id"],
                    "gold_claim_id": gold.id,
                    "expected": row["expected_checks"][gold.id],
                    "predicted": predicted,
                    "correct": predicted == row["expected_checks"][gold.id],
                    "compiled_status": check.status if check else None,
                    "requirement_id": source.id if source else None,
                    "predicted_claim_id": claim.id if claim else None,
                    "source_fact_correct": claim is not None,
                    "identity_correct": identity_ok,
                    "link_exact_correct": link_correct,
                    "reason": check.reason
                    if check
                    else "Missing, wrong or ambiguous extracted fact.",
                }
            )
    counts = Counter(row["predicted"] for row in rows)
    all_supported = {
        (comparison.requirement_id, check.id)
        for comparison in comparisons
        for check in comparison.checks
        if check.kind != "method" and check.status == "supported"
    }
    safe_supported = {
        (row["requirement_id"], row["predicted_claim_id"])
        for row in rows
        if row["correct"] and row["predicted"] == "supported"
    }
    raw_supported = len(all_supported)
    unsafe_supported = len(all_supported - safe_supported)
    return {
        "fixture_sha256": FROZEN_SHA256,
        "evaluator_sha256": EVALUATOR_SHA256,
        "annotation_status": data["annotation_status"],
        "expected_decisions": len(rows),
        "correct_decisions": sum(row["correct"] for row in rows),
        "accuracy": sum(row["correct"] for row in rows) / len(rows),
        "predicted_counts": dict(counts),
        "automation_coverage": (counts["supported"] + counts["gap"]) / len(rows),
        "unknown_rate": counts["unknown"] / len(rows),
        "error_rate": counts["error"] / len(rows),
        "raw_supported_count": raw_supported,
        "unsafe_supported_count": unsafe_supported,
        "unsafe_support_rate": _ratio(unsafe_supported, raw_supported),
        "link_exact_correct_count": sum(row["link_exact_correct"] is True for row in rows),
        "link_label_count": len(rows) if alignments is not None else 0,
        "unmatched_test_ids": extras,
        "overall_tests": overall_rows,
        "overall_correct_tests": sum(row["correct"] for row in overall_rows),
        "rows": rows,
        "limitation": "Author-designed source-text challenge, not independent production accuracy. "
        "A missing/wrong extracted fact is error even when its expected capability is unknown. "
        "Machine identity and claim-link correctness are separate from numeric verdicts.",
    }


def evaluate_code_only(run, *, normalized_requirements=None):
    bundle = build_challenge()
    normalized = normalized_requirements or [
        normalize_requirement(row) for row in run.extraction.requirements
    ]
    facts = {row.requirement_id: row for row in normalized}
    comparisons = []
    selected = {}
    for requirement in run.extraction.requirements:
        capability = _method_capability(requirement, bundle["catalog"], bundle["lab"])
        selected[requirement.id] = capability.id if capability else None
        comparisons.append(
            compare_requirement(
                facts[requirement.id],
                capability.atomic_claims if capability else [],
                method_status="supported" if capability else "unknown",
            )
        )
    result = _score_comparisons(
        run.extraction.requirements, normalized, comparisons, selected_capabilities=selected
    )
    result["strategy"] = "code_only_exact_method_and_controlled_parameter_rules"
    return result


def evaluate_matching(attempt: SemanticMatchingRun):
    if attempt.status != "completed" or attempt.output is None:
        return {
            "run_id": attempt.run_id,
            "status": "failed",
            "error_code": attempt.error_code,
            "expected_decisions": 20,
            "correct_decisions": 0,
            "accuracy": 0,
            "error_rate": 1,
            "fixture_sha256": FROZEN_SHA256,
            "evaluator_sha256": EVALUATOR_SHA256,
        }
    snapshot = attempt.input_snapshot
    lab = build_challenge()["lab"]
    if snapshot.lab_id != lab.id:
        raise ValueError("This matching run is not from the frozen challenge lab.")
    statuses = {
        row.requirement.id: (
            "supported" if _method_capability(row.requirement, snapshot.catalog, lab) else "unknown"
        )
        for row in snapshot.requirements
    }
    comparisons = compile_matching_run(attempt, statuses)
    result = _score_comparisons(
        [row.requirement for row in snapshot.requirements],
        [row.normalized for row in snapshot.requirements],
        comparisons,
        alignments={row.requirement_id: row for row in attempt.output.items},
    )
    result.update(
        strategy="accepted_ai_correspondence_plus_deterministic_checks",
        run_id=attempt.run_id,
        provider=attempt.provider,
        model=attempt.model,
        prompt_version=attempt.prompt_version,
        validation_version=attempt.validation_version,
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction", type=Path)
    parser.add_argument("--matching", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("local_data/experiments/live-challenge-replay")
    )
    args = parser.parse_args(argv)
    bundle = build_challenge()
    result = {
        "fixture_sha256": FROZEN_SHA256,
        "new_api_calls": 0,
        "oracle_code_only": evaluate_code_only(
            bundle["oracle_run"], normalized_requirements=bundle["oracle_normalized"]
        ),
    }
    if args.extraction:
        run = CustomerExtractionRun.model_validate_json(args.extraction.read_text())
        result["extraction"] = evaluate_extraction(run)
        result["extracted_code_only"] = evaluate_code_only(run)
    if args.matching:
        result["matching"] = evaluate_matching(
            SemanticMatchingRun.model_validate_json(args.matching.read_text())
        )
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "evaluation.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared/replayed challenge offline; no API calls. Results: {path}")


if __name__ == "__main__":
    main()

"""Preflight bounded whole-requirement matching calls without dropping source facts.

The parent is a local execution record, never a fabricated provider response.
Actual requests, raw responses and usage belong to its individual child attempts.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from scione.inference_progress import ProgressProvider, ProgressReporter
from scione.matching.keyed_wire import KEYED_WIRE_VERSION
from scione.matching.models import MatchingInput, SemanticMatchingOutput, SemanticMatchingRun
from scione.matching.projection import MATCHING_PROJECTION_VERSION, MatchingProjectionError
from scione.matching.service import (
    DEFAULT_MATCHING_INPUT_CHARACTER_LIMIT,
    MatchingReferenceError,
    SemanticMatchingError,
    SemanticMatchingRunner,
    input_fingerprint,
    prepare_matching_request,
    validate_matching_output,
)
from scione.providers.base import ModelProvider, StructuredGenerationRequest, TokenUsage

MATCHING_BATCH_POLICY_VERSION = "whole_requirement_batches_v7"
MAX_MATCHING_BATCH_CALLS = 8
MAX_LONG_DOCUMENT_MATCHING_BATCH_CALLS = 16
MATCHING_BATCH_SOFT_CLAIM_LIMIT = 40


def provider_claim_budget(provider: ModelProvider) -> int:
    """Bound planned output size without padding every test into its own call.

    This is a conservative planning estimate, not a guarantee of model output
    length. Complete schema and coverage validation still reject truncation.
    Unspecified providers retain the earlier forty-claim baseline.
    """
    limit = provider.generation_settings.get("max_output_tokens")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return MATCHING_BATCH_SOFT_CLAIM_LIMIT
    # Reserve global JSON and reasoning; short per-field reasons/IDs still
    # receive 128 tokens apiece. Do not scale beyond eighty claims per batch.
    return min(80, max(1, (limit - 2048) // 128))


def request_character_count(request: StructuredGenerationRequest) -> int:
    """Same character guard as the single-call runner; not a token estimate."""
    return (
        len(request.system_prompt)
        + len(request.user_prompt)
        + len(json.dumps(request.json_schema, ensure_ascii=False))
    )


@dataclass(frozen=True)
class MatchingBatchPlan:
    snapshots: tuple[MatchingInput, ...]
    requests: tuple[StructuredGenerationRequest, ...]
    manifest: dict[str, Any]
    single_call: bool


class MatchingBatchPlanningError(ValueError):
    def __init__(self, code: str, message: str, manifest: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.manifest = manifest


def _subset(snapshot: MatchingInput, indices: list[int]) -> MatchingInput:
    rows = [snapshot.requirements[index] for index in indices]
    tests = {identifier for row in rows for identifier in row.catalog_test_ids}
    caps = {identifier for row in rows for identifier in row.capability_ids}
    data = snapshot.model_dump(mode="json")
    data["requirements"] = [row.model_dump(mode="json") for row in rows]
    data["capabilities"] = [
        row.model_dump(mode="json") for row in snapshot.capabilities if row.capability.id in caps
    ]
    data["catalog"]["tests"] = [
        row.model_dump(mode="json") for row in snapshot.catalog.tests if row.id in tests
    ]
    # Methods already have the upstream bounded, explicit metadata allowlist.
    # Retain it intact: an original source code can belong outside a human-pinned
    # family. Removing it by family would lose the source edition/identity facts.
    return MatchingInput.model_validate(data)


def _entry(snapshot: MatchingInput, request: StructuredGenerationRequest) -> dict[str, Any]:
    return {
        "input_fingerprint": input_fingerprint(snapshot),
        "requirement_ids": [row.requirement.id for row in snapshot.requirements],
        "claim_count": sum(len(row.normalized.claims) for row in snapshot.requirements),
        "request_characters": request_character_count(request),
        "capability_ids": [row.capability.id for row in snapshot.capabilities],
        "catalog_test_ids": [row.id for row in snapshot.catalog.tests],
    }


def plan_matching_batches(
    snapshot: MatchingInput,
    *,
    max_input_characters: int = DEFAULT_MATCHING_INPUT_CHARACTER_LIMIT,
    max_claims_per_batch: int = MATCHING_BATCH_SOFT_CLAIM_LIMIT,
    keyed: bool = False,
    max_calls: int = MAX_MATCHING_BATCH_CALLS,
) -> MatchingBatchPlan:
    """Prepare every child before any network call; whole rows remain unchanged.

    The claim limit is soft for an indivisible parent requirement. An oversized
    single requirement fails preflight rather than losing atoms or source context.
    """
    for name, value in (
        ("max_input_characters", max_input_characters),
        ("max_claims_per_batch", max_claims_per_batch),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer.")
    if (
        isinstance(max_calls, bool) or not isinstance(max_calls, int)
        or not 1 <= max_calls <= MAX_LONG_DOCUMENT_MATCHING_BATCH_CALLS
    ):
        raise ValueError(
            f"max_calls must be an integer between 1 and {MAX_LONG_DOCUMENT_MATCHING_BATCH_CALLS}."
        )
    snapshot = MatchingInput.model_validate(snapshot.model_dump(mode="json"))
    manifest: dict[str, Any] = {
        "policy_version": MATCHING_BATCH_POLICY_VERSION,
        "kind": "local_execution_plan_not_a_provider_request",
        "max_input_characters": max_input_characters,
        "soft_claim_limit": max_claims_per_batch,
        "maximum_calls": max_calls,
        "wire_format": KEYED_WIRE_VERSION if keyed else "matching_array_v1",
        "preflight_complete": False,
        "batches": [],
    }
    full_request = prepare_matching_request(snapshot, keyed=keyed)
    full_claims = sum(len(row.normalized.claims) for row in snapshot.requirements)
    if request_character_count(full_request) <= max_input_characters and (
        full_claims <= max_claims_per_batch
    ):
        manifest.update(preflight_complete=True, batches=[_entry(snapshot, full_request)])
        return MatchingBatchPlan((snapshot,), (full_request,), manifest, True)

    snapshots: list[MatchingInput] = []
    requests: list[StructuredGenerationRequest] = []
    indices: list[int] = []
    current: MatchingInput | None = None
    prepared: StructuredGenerationRequest | None = None

    def append_current() -> None:
        assert current is not None and prepared is not None
        snapshots.append(current)
        requests.append(prepared)
        manifest["batches"].append(_entry(current, prepared))

    for index, row in enumerate(snapshot.requirements):
        candidate = _subset(snapshot, [*indices, index])
        request = prepare_matching_request(candidate, keyed=keyed)
        claims = sum(len(item.normalized.claims) for item in candidate.requirements)
        if indices and (
            claims > max_claims_per_batch or request_character_count(request) > max_input_characters
        ):
            append_current()
            indices = []
            candidate = _subset(snapshot, [index])
            request = prepare_matching_request(candidate, keyed=keyed)
        if request_character_count(request) > max_input_characters:
            manifest["blocked_requirement_id"] = row.requirement.id
            manifest["blocked_request_characters"] = request_character_count(request)
            raise MatchingBatchPlanningError(
                "batch_input_limit_exceeded",
                "One test item exceeds the matching input guard. No model call was made "
                "and no source fact was removed. Review or split the source test item.",
                manifest,
            )
        indices.append(index)
        current, prepared = candidate, request
    if indices:
        append_current()
    manifest["preflight_complete"] = True
    if len(snapshots) > max_calls:
        raise MatchingBatchPlanningError(
            "batch_call_limit_exceeded",
            f"This input needs {len(snapshots)} whole-test matching calls; the limit is "
            f"{max_calls}. No model call was made. Reduce the selected scope.",
            manifest,
        )
    return MatchingBatchPlan(tuple(snapshots), tuple(requests), manifest, False)


def _usage(children: list[SemanticMatchingRun]) -> TokenUsage:
    if not children:
        return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    totals = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        values = [getattr(child.usage, name) for child in children]
        totals[name] = sum(values) if all(value is not None for value in values) else None
    return TokenUsage(**totals)


def _parent_fields(
    provider: ModelProvider,
    snapshot: MatchingInput,
    started: datetime,
    manifest: dict[str, Any],
    children: list[SemanticMatchingRun],
) -> dict[str, Any]:
    def resolved(name: str, fallback: str) -> str:
        values = {getattr(child, name) for child in children}
        return (
            next(iter(values))
            if len(values) == 1
            else ("multiple_children" if values else fallback)
        )

    latencies = [child.latency_ms for child in children]
    return {
        "run_id": str(uuid4()),
        "started_at": started,
        "finished_at": datetime.now(UTC),
        "input_fingerprint": input_fingerprint(snapshot),
        "provider": resolved("provider", provider.provider_name),
        "model": resolved("model", provider.model_name),
        "requested_provider": provider.provider_name,
        "requested_model": provider.model_name,
        "input_snapshot": snapshot,
        "request": StructuredGenerationRequest(
            system_prompt="LOCAL MATCHING EXECUTION PLAN. This manifest was not sent "
            "to a provider. Actual transmitted requests and responses are retained "
            "in child_attempts.",
            user_prompt=json.dumps(manifest, ensure_ascii=False, sort_keys=True),
            schema_name="local_matching_batch_plan_v1",
            json_schema={
                "type": "object",
                "description": "Local plan manifest, not an inference schema.",
            },
        ),
        "projection_version": MATCHING_PROJECTION_VERSION,
        "generation_settings": provider.generation_settings,
        "wire_format": "matching_batch_manifest_v1",
        "execution_mode": "planned_batches",
        "planned_count": max(
            1, len(manifest["batches"]) + bool(manifest.get("blocked_requirement_id"))
        ),
        "child_attempts": children,
        "policy_snapshot": {"batching": manifest},
        "usage": _usage(children),
        "latency_ms": (
            (datetime.now(UTC) - started).total_seconds() * 1000
            if children and manifest.get("max_concurrent_batches", 1) > 1
            else sum(latencies)
            if latencies and all(value is not None for value in latencies)
            else None
        ),
    }


def run_bounded_matching(
    provider: ModelProvider,
    snapshot: MatchingInput,
    *,
    max_input_characters: int = DEFAULT_MATCHING_INPUT_CHARACTER_LIMIT,
    max_claims_per_batch: int | None = None,
    max_calls: int = MAX_MATCHING_BATCH_CALLS,
    max_concurrent_batches: int = 2,
    on_progress=None,
) -> SemanticMatchingRun:
    """Execute one explicit bounded plan, with no retries or accepted partial output."""
    activity = ProgressReporter(on_progress)
    activity.emit("planning")
    if type(max_concurrent_batches) is not int or not 1 <= max_concurrent_batches <= 4:
        raise ValueError("max_concurrent_batches must be an integer between 1 and 4.")
    snapshot = MatchingInput.model_validate(snapshot.model_dump(mode="json"))
    started = datetime.now(UTC)
    if max_claims_per_batch is None:
        max_claims_per_batch = provider_claim_budget(provider)
    try:
        plan = plan_matching_batches(
            snapshot,
            max_input_characters=max_input_characters,
            max_claims_per_batch=max_claims_per_batch,
            keyed=provider.provider_name in {"sail", "fireworks"},
            max_calls=max_calls,
        )
    except MatchingProjectionError:
        # Preserve the existing single-call projection failure artifact semantics.
        return SemanticMatchingRunner(provider, max_input_characters=max_input_characters).run(
            snapshot
        )
    except MatchingBatchPlanningError as exc:
        fields = _parent_fields(provider, snapshot, started, exc.manifest, [])
        run = SemanticMatchingRun(
            **fields, status="failed", error_code=exc.code, error_message=str(exc)
        )
        raise SemanticMatchingError(str(exc), run=run) from exc
    activity.plan(len(plan.snapshots), stage="matching")
    observed_provider = (
        ProgressProvider(provider, activity, stage="matching")
        if on_progress is not None else provider
    )
    if plan.single_call:
        result = SemanticMatchingRunner(
            observed_provider, max_input_characters=max_input_characters
        ).run(
            snapshot
        )
        activity.emit("validating")
        return result

    children: list[SemanticMatchingRun] = []
    concurrency = max_concurrent_batches if provider.provider_name in {"sail", "fireworks"} else 1
    plan.manifest["max_concurrent_batches"] = concurrency
    plan.manifest["latency_semantics"] = "wall_time_ms" if concurrency > 1 else "sum_child_ms"
    failure = None
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="matching") as executor:
        for offset in range(0, len(plan.snapshots), concurrency):
            activity.emit("matching")
            # A wave starts only the configured bounded set of preflighted whole-test
            # requests. No later wave starts after a known failure. Both
            # in-flight attempts finish and are retained before returning.
            futures = [
                executor.submit(
                    SemanticMatchingRunner(
                        observed_provider, max_input_characters=max_input_characters
                    ).run,
                    child_snapshot,
                )
                for child_snapshot in plan.snapshots[offset : offset + concurrency]
            ]
            for future in futures:
                try:
                    children.append(future.result())
                except SemanticMatchingError as exc:
                    children.append(exc.run)
                    failure = failure or exc
            if failure is not None:
                break
    if failure is not None:
        failed_kinds = {
            child.provider_failure_kind for child in children if child.status == "failed"
        }
        cause = (
            "The AI service timed out in a comparison batch. "
            if "timeout" in failed_kinds else ""
        )
        message = (
            cause
            + f"Matching stopped after {len(children)} attempted batches of {len(plan.snapshots)}. "
            "No partial result was accepted. Completed and failed attempts are retained; "
            "previous saved results remain available. Retry must be requested explicitly."
        )
        run = SemanticMatchingRun(
            **_parent_fields(provider, snapshot, started, plan.manifest, children),
            status="failed",
            error_code="batch_child_failed",
            error_message=message,
            provider_failure_kind=(
                "timeout" if "timeout" in failed_kinds
                else next((kind for kind in sorted(failed_kinds - {None})), None)
            ),
        )
        raise SemanticMatchingError(message, run=run) from failure
    activity.emit("validating")
    proposal = SemanticMatchingOutput(
        items=[item for child in children for item in child.proposal.items],
        review_issues=[issue for child in children for issue in child.proposal.review_issues],
    )
    try:
        output, issues = validate_matching_output(snapshot, proposal)
    except MatchingReferenceError as exc:
        message = (
            "The combined matching responses did not cover the original inputs exactly. "
            "No output was accepted."
        )
        run = SemanticMatchingRun(
            **_parent_fields(provider, snapshot, started, plan.manifest, children),
            status="failed",
            error_code="batch_combined_references_invalid",
            error_message=message,
        )
        raise SemanticMatchingError(message, run=run) from exc
    return SemanticMatchingRun(
        **_parent_fields(provider, snapshot, started, plan.manifest, children),
        status="completed",
        proposal=proposal,
        output=output,
        validation_issues=issues,
    )
